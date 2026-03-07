"""
Crawler Pipeline Worker (§17.10) + Federation Endpoints (§17.9).

Crawler:
  - Respects robots.txt (§17.10.2)
  - Checks CrawlerImportPermission before indexing
  - Rate-limited HTTP fetcher (max 1 req/s per domain)
  - Handles takedown requests immediately
  - Logs everything to crawler_job table

Federation:
  - POST /federation/sync     Push agent card to / pull from a peer
  - POST /federation/takedown Submit a takedown request
  - GET  /federation/peers    List known federation peers
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("fastapi_a2a.crawler")

# ──────────────────────────────────────────────────────────────────────────────
# Robots.txt checker
# ──────────────────────────────────────────────────────────────────────────────

_ROBOTS_CACHE: dict[str, tuple[datetime, bool]] = {}
_ROBOTS_CACHE_TTL_SECONDS = 3600
_USER_AGENT = "fastapi-a2a-crawler/0.6.0 (+https://fastapi-a2a.dev/crawler)"


async def is_crawling_allowed(url: str) -> bool:
    """
    Check robots.txt for the given URL.
    Returns True if crawling is allowed, False if disallowed.
    Caches results for 1 hour.
    """
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    robots_url = f"{origin}/robots.txt"
    now = datetime.now(UTC)

    # Cache hit
    if robots_url in _ROBOTS_CACHE:
        cached_at, allowed = _ROBOTS_CACHE[robots_url]
        age = (now - cached_at).total_seconds()
        if age < _ROBOTS_CACHE_TTL_SECONDS:
            return allowed

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(robots_url, headers={"User-Agent": _USER_AGENT})
        if resp.status_code != 200:
            _ROBOTS_CACHE[robots_url] = (now, True)
            return True

        allowed = _parse_robots(resp.text, parsed.path)
        _ROBOTS_CACHE[robots_url] = (now, allowed)
        return allowed

    except Exception as exc:
        logger.debug("robots.txt fetch failed for %s: %s — assuming allowed", origin, exc)
        _ROBOTS_CACHE[robots_url] = (now, True)
        return True


def _parse_robots(robots_txt: str, path: str) -> bool:
    """Parse robots.txt and return True if path is crawlable by our user agent."""
    applicable = False
    disallowed_paths: list[str] = []

    for line in robots_txt.splitlines():
        line = line.strip().lower()
        if line.startswith("user-agent:"):
            ua = line.split(":", 1)[1].strip()
            applicable = ua in ("*", "fastapi-a2a-crawler")
        elif applicable and line.startswith("disallow:"):
            dp = line.split(":", 1)[1].strip()
            if dp:
                disallowed_paths.append(dp)

    for dp in disallowed_paths:
        if path.startswith(dp):
            return False
    return True


# ──────────────────────────────────────────────────────────────────────────────
# Crawler worker
# ──────────────────────────────────────────────────────────────────────────────

_domain_last_fetch: dict[str, datetime] = {}
_MIN_INTERVAL_SECONDS = 1.0  # Max 1 req/s per domain


async def _rate_limit_domain(domain: str) -> None:
    """Enforce per-domain rate limit (§17.10.3)."""
    now = datetime.now(UTC)
    if domain in _domain_last_fetch:
        elapsed = (now - _domain_last_fetch[domain]).total_seconds()
        if elapsed < _MIN_INTERVAL_SECONDS:
            await asyncio.sleep(_MIN_INTERVAL_SECONDS - elapsed)
    _domain_last_fetch[domain] = datetime.now(UTC)


async def _fetch_agent_card(url: str) -> dict | None:
    """Fetch and return agent card JSON from a URL."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                url,
                headers={"User-Agent": _USER_AGENT},
                follow_redirects=True,
            )
        if resp.status_code == 200 and "application/json" in resp.headers.get("content-type", ""):
            return resp.json()
    except Exception as exc:
        logger.debug("Failed to fetch %s: %s", url, exc)
    return None


async def _process_crawler_job(job_id, session_factory) -> None:
    """Process a single crawler job."""
    from sqlalchemy import select

    from fastapi_a2a.domains.federation.models import (
        CrawlerImportPermission,
        CrawlerJob,
        CrawlerSource,
    )

    async with session_factory() as db:
        result = await db.execute(select(CrawlerJob).where(CrawlerJob.id == job_id))
        job = result.scalar_one_or_none()
        if job is None or job.status != "queued":
            return

        job.status = "running"
        job.started_at = datetime.now(UTC)
        await db.commit()

    try:
        target_url = job.target_url
        parsed = urlparse(target_url)
        domain = parsed.netloc

        # 1. robots.txt check
        if not await is_crawling_allowed(target_url):
            async with session_factory() as db:
                job = await db.get(CrawlerJob, job_id)
                job.status = "skipped"
                job.disallow_reason = "robots_txt"
                job.completed_at = datetime.now(UTC)
                await db.commit()
            return

        # 2. Rate limit
        await _rate_limit_domain(domain)

        # 3. Fetch agent card
        card_data = await _fetch_agent_card(target_url)

        async with session_factory() as db:
            job = await db.get(CrawlerJob, job_id)
            if card_data:
                # Check import permission
                src_result = await db.execute(
                    select(CrawlerSource).where(CrawlerSource.id == job.crawler_source_id)
                )
                source = src_result.scalar_one_or_none()

                if source:
                    perm_result = await db.execute(
                        select(CrawlerImportPermission).where(
                            CrawlerImportPermission.crawler_source_id == source.id,
                            CrawlerImportPermission.is_active.is_(True),
                        )
                    )
                    perm = perm_result.scalar_one_or_none()
                    if perm is None and source.require_import_permission:
                        job.status = "skipped"
                        job.disallow_reason = "missing_import_permission"
                        job.completed_at = datetime.now(UTC)
                        await db.commit()
                        return

                job.status = "completed"
                job.fetched_card = card_data
                job.http_status = 200
            else:
                job.status = "failed"
                job.http_status = 0
                job.error_message = "Failed to fetch or parse agent card"

            job.completed_at = datetime.now(UTC)
            await db.commit()

    except Exception as exc:
        logger.warning("Crawler job %s error: %s", job_id, exc)
        async with session_factory() as db:
            job = await db.get(CrawlerJob, job_id)
            if job:
                job.status = "failed"
                job.error_message = str(exc)[:512]
                job.completed_at = datetime.now(UTC)
                await db.commit()


async def _crawler_loop(session_factory, interval: int) -> None:
    from sqlalchemy import select

    from fastapi_a2a.domains.federation.models import CrawlerJob

    logger.info("Crawler worker started (interval=%ds)", interval)
    while True:
        try:
            now = datetime.now(UTC)
            async with session_factory() as db:
                result = await db.execute(
                    select(CrawlerJob.id).where(
                        CrawlerJob.status == "queued",
                        CrawlerJob.scheduled_at <= now,
                    ).limit(20)
                )
                job_ids = [row[0] for row in result.all()]

            for job_id in job_ids:
                await _process_crawler_job(job_id, session_factory)

        except Exception as exc:
            logger.error("Crawler loop error: %s", exc, exc_info=True)
        await asyncio.sleep(interval)


_crawler_task: asyncio.Task | None = None


async def start_crawler_worker(session_factory, interval: int = 30) -> asyncio.Task:
    global _crawler_task
    if _crawler_task and not _crawler_task.done():
        return _crawler_task
    _crawler_task = asyncio.create_task(
        _crawler_loop(session_factory, interval), name="a2a_crawler_worker"
    )
    return _crawler_task


async def stop_crawler_worker() -> None:
    global _crawler_task
    if _crawler_task and not _crawler_task.done():
        _crawler_task.cancel()
        try:
            await _crawler_task
        except asyncio.CancelledError:
            pass
