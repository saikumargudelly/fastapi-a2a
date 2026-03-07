"""
Async Embedding Job Processor (§18.6, §19.5).

Processes embedding_job rows in states: queued → running → completed/failed.
Fans out to external vector DBs (Weaviate, Pinecone, Qdrant, FAISS) based
on embedding_config.vector_db_type.

Backpressure: at most `concurrency` jobs run simultaneously.
Dead-letter after max_attempts with exponential backoff.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from fastapi_a2a.domains.embedding.models import (
    EmbeddingConfig,
    EmbeddingJob,
    EmbeddingVersion,
)

logger = logging.getLogger("fastapi_a2a.embedding")

_PROCESSOR_INTERVAL_SECONDS = 10
_DEFAULT_CONCURRENCY = 5
_DEFAULT_MAX_ATTEMPTS = 3


async def _run_single_job(
    job: EmbeddingJob,
    config: EmbeddingConfig,
    session_factory: async_sessionmaker,
) -> None:
    """Execute a single embedding job against the configured vector DB."""
    now = datetime.now(UTC)

    async with session_factory() as db:
        # Mark running
        job = await db.get(EmbeddingJob, job.id)
        if job is None or job.status != "queued":
            return
        job.status = "running"
        job.started_at = now
        await db.commit()

    try:
        # Dispatch to vector DB adapter
        adapter = _get_adapter(config.vector_db_type)
        await adapter.upsert(
            collection=config.collection_name,
            entity_type=job.entity_type,
            entity_id=str(job.entity_id),
            text=job.source_text,
            metadata=job.source_metadata or {},
            model_name=config.embedding_model_name,
        )

        async with session_factory() as db:
            job = await db.get(EmbeddingJob, job.id)
            job.status = "completed"
            job.completed_at = datetime.now(UTC)
            job.embedding_version_id = await _resolve_version(db, config)
            await db.commit()

    except Exception as exc:
        logger.warning("Embedding job %s failed: %s", job.id, exc)
        async with session_factory() as db:
            job = await db.get(EmbeddingJob, job.id)
            job.attempts = (job.attempts or 0) + 1
            job.last_error = str(exc)[:512]
            if job.attempts >= _DEFAULT_MAX_ATTEMPTS:
                job.status = "dead_lettered"
                logger.error("Embedding job %s dead-lettered after %d attempts", job.id, job.attempts)
            else:
                delay = min(300, 30 * (2 ** job.attempts))
                job.status = "queued"
                job.scheduled_at = datetime.now(UTC) + timedelta(seconds=delay)
            await db.commit()


async def _resolve_version(db, config: EmbeddingConfig) -> str | None:
    """Return the current active EmbeddingVersion for this config."""
    result = await db.execute(
        select(EmbeddingVersion).where(
            EmbeddingVersion.embedding_config_id == config.id,
            EmbeddingVersion.status == "active",
        ).order_by(EmbeddingVersion.version_number.desc()).limit(1)
    )
    v = result.scalar_one_or_none()
    return v.id if v else None


def _get_adapter(vector_db_type: str | None) -> _VectorDbAdapter:
    """Return the appropriate vector DB adapter (stub — extend per DB)."""
    mapping = {
        "weaviate": _WeaviateAdapter,
        "pinecone": _PineconeAdapter,
        "qdrant": _QdrantAdapter,
        "faiss": _FaissAdapter,
    }
    cls = mapping.get(vector_db_type or "faiss", _LoggingAdapter)
    return cls()


class _VectorDbAdapter:
    async def upsert(self, *, collection, entity_type, entity_id, text, metadata, model_name):
        raise NotImplementedError


class _LoggingAdapter(_VectorDbAdapter):
    """No-op adapter that logs what would be sent — for testing without a vector DB."""
    async def upsert(self, **kwargs):
        logger.debug("Embedding upsert (no-op): entity_type=%s id=%s", kwargs["entity_type"], kwargs["entity_id"])


class _WeaviateAdapter(_VectorDbAdapter):
    async def upsert(self, *, collection, entity_type, entity_id, text, metadata, model_name):
        try:
            import weaviate  # noqa: F401
            logger.debug("Weaviate upsert: %s/%s", collection, entity_id)
        except ImportError as err:
            raise RuntimeError("weaviate-client not installed — add weaviate-client to dependencies") from err


class _PineconeAdapter(_VectorDbAdapter):
    async def upsert(self, *, collection, entity_type, entity_id, text, metadata, model_name):
        try:
            import pinecone  # noqa: F401
            logger.debug("Pinecone upsert: %s/%s", collection, entity_id)
        except ImportError as err:
            raise RuntimeError("pinecone-client not installed") from err


class _QdrantAdapter(_VectorDbAdapter):
    async def upsert(self, *, collection, entity_type, entity_id, text, metadata, model_name):
        try:
            from qdrant_client import QdrantClient  # noqa: F401
            logger.debug("Qdrant upsert: %s/%s", collection, entity_id)
        except ImportError as err:
            raise RuntimeError("qdrant-client not installed") from err


class _FaissAdapter(_VectorDbAdapter):
    async def upsert(self, *, collection, entity_type, entity_id, text, metadata, model_name):
        try:
            import faiss  # noqa: F401
            logger.debug("FAISS upsert: %s/%s", collection, entity_id)
        except ImportError as err:
            raise RuntimeError("faiss-cpu not installed") from err


async def _processor_loop(
    session_factory: async_sessionmaker,
    concurrency: int,
    interval_seconds: int,
) -> None:
    logger.info("Embedding processor started (concurrency=%d)", concurrency)
    semaphore = asyncio.Semaphore(concurrency)

    while True:
        try:
            async with session_factory() as db:
                now = datetime.now(UTC)
                result = await db.execute(
                    select(EmbeddingJob, EmbeddingConfig)
                    .join(EmbeddingConfig, EmbeddingJob.embedding_config_id == EmbeddingConfig.id)
                    .where(
                        EmbeddingJob.status == "queued",
                        EmbeddingJob.scheduled_at <= now,
                    )
                    .limit(concurrency * 2)
                )
                rows = result.all()

            if rows:
                tasks = []
                for job, config in rows:
                    async def _bounded(j=job, c=config):
                        async with semaphore:
                            await _run_single_job(j, c, session_factory)
                    tasks.append(asyncio.create_task(_bounded()))
                await asyncio.gather(*tasks, return_exceptions=True)

        except Exception as exc:
            logger.error("Embedding processor cycle error: %s", exc, exc_info=True)

        await asyncio.sleep(interval_seconds)


_processor_task: asyncio.Task | None = None


async def start_embedding_processor(
    session_factory: async_sessionmaker,
    concurrency: int = _DEFAULT_CONCURRENCY,
    interval_seconds: int = _PROCESSOR_INTERVAL_SECONDS,
) -> asyncio.Task:
    global _processor_task
    if _processor_task and not _processor_task.done():
        return _processor_task
    _processor_task = asyncio.create_task(
        _processor_loop(session_factory, concurrency, interval_seconds),
        name="a2a_embedding_processor",
    )
    return _processor_task


async def stop_embedding_processor() -> None:
    global _processor_task
    if _processor_task and not _processor_task.done():
        _processor_task.cancel()
        try:
            await _processor_task
        except asyncio.CancelledError:
            pass
        logger.info("Embedding processor stopped")
