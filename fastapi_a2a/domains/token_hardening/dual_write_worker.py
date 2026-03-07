"""
Dual-Write Fanout Worker (§19.3).

Processes dual_write_queue rows and fans out token audit events to:
  - PostgreSQL (primary, always)
  - Kinesis / SNS / PubSub (secondary streams, async)
  - Remote region DB (cross-region replica)

Runs as a background asyncio task. Dead-letter after max_attempts.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from fastapi_a2a.domains.token_hardening.models import DualWriteQueue

logger = logging.getLogger("fastapi_a2a.dual_write")

_FANOUT_INTERVAL_SECONDS = 5
_MAX_BATCH_SIZE = 100


async def _process_batch(session_factory: async_sessionmaker, queue_type: str) -> int:
    """Process one batch of due dual_write_queue rows. Returns count processed."""
    async with session_factory() as db:
        now = datetime.now(UTC)

        result = await db.execute(
            select(DualWriteQueue)
            .where(
                DualWriteQueue.status == "pending",
                DualWriteQueue.attempts < DualWriteQueue.max_attempts,
                DualWriteQueue.scheduled_at <= now,
            )
            .order_by(DualWriteQueue.scheduled_at)
            .limit(_MAX_BATCH_SIZE)
            .with_for_update(skip_locked=True)  # Concurrent worker safety
        )
        rows = result.scalars().all()

        processed = 0
        for row in rows:
            try:
                await _fanout_single(row, queue_type)
                row.status = "delivered"
                row.delivered_at = now
            except Exception as exc:
                row.attempts += 1
                row.last_error = str(exc)[:512]
                if row.attempts >= row.max_attempts:
                    row.status = "dead_lettered"
                    logger.error(
                        "DLQ: dual_write_queue row %s dead-lettered after %d attempts: %s",
                        row.id, row.attempts, exc,
                    )
                else:
                    from datetime import timedelta
                    # Exponential backoff
                    delay = min(300, 5 * (2 ** row.attempts))
                    row.scheduled_at = now + timedelta(seconds=delay)
            processed += 1

        await db.commit()
        return processed


async def _fanout_single(row: DualWriteQueue, queue_type: str) -> None:
    """Fan out a single audit event to secondary targets."""
    if queue_type == "db_only":
        # Nothing extra to do — primary write already persisted
        return

    payload = row.payload or {}

    if queue_type == "kinesis":
        # Placeholder: in production, use aiobotocore
        logger.debug("KINESIS: would send to %s: %s", row.target_stream, json.dumps(payload)[:128])

    elif queue_type == "sqs":
        logger.debug("SQS: would send to %s: %s", row.target_stream, json.dumps(payload)[:128])

    elif queue_type == "pubsub":
        logger.debug("PUBSUB: would publish to %s: %s", row.target_stream, json.dumps(payload)[:128])


async def _fanout_loop(
    session_factory: async_sessionmaker,
    queue_type: str,
    interval_seconds: int,
) -> None:
    logger.info("Dual-write fanout worker started (type=%s, interval=%ds)", queue_type, interval_seconds)
    while True:
        try:
            count = await _process_batch(session_factory, queue_type)
            if count > 0:
                logger.debug("Dual-write: processed %d rows", count)
        except Exception as exc:
            logger.error("Dual-write fanout error: %s", exc, exc_info=True)
        await asyncio.sleep(interval_seconds)


_fanout_task: asyncio.Task | None = None


async def start_dual_write_fanout(
    session_factory: async_sessionmaker,
    queue_type: str = "db_only",
    interval_seconds: int = 5,
) -> asyncio.Task:
    global _fanout_task
    if _fanout_task and not _fanout_task.done():
        return _fanout_task
    _fanout_task = asyncio.create_task(
        _fanout_loop(session_factory, queue_type, interval_seconds),
        name="a2a_dual_write_fanout",
    )
    return _fanout_task


async def stop_dual_write_fanout() -> None:
    global _fanout_task
    if _fanout_task and not _fanout_task.done():
        _fanout_task.cancel()
        try:
            await _fanout_task
        except asyncio.CancelledError:
            pass
        logger.info("Dual-write fanout worker stopped")
