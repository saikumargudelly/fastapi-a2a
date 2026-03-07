"""
Registry Heartbeat Background Task — periodic liveness checks.

Runs every `check_interval_seconds` (default: 60s) for each registered agent.
Updates heartbeat.is_reachable, last_http_status, consecutive_failures.
Marks registry_entry.approval_status = 'suspended' after max_failures.

Usage:
    from fastapi_a2a.domains.registry.heartbeat import start_heartbeat_scheduler
    await start_heartbeat_scheduler(session_factory, app_state)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fastapi_a2a.domains.core_a2a.models import AgentCard
from fastapi_a2a.domains.registry.models import Heartbeat, RegistryEntry

logger = logging.getLogger("fastapi_a2a.heartbeat")

MAX_CONSECUTIVE_FAILURES = 5
DEFAULT_REQUEST_TIMEOUT = 10.0
_scheduler_task: asyncio.Task | None = None


async def _check_agent(
    card: AgentCard,
    heartbeat: Heartbeat,
    entry: RegistryEntry,
    session: AsyncSession,
) -> None:
    """Perform a single liveness check for one agent card."""
    now = datetime.now(UTC)
    target_url = f"{card.url.rstrip('/')}/.well-known/agent.json"

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_REQUEST_TIMEOUT) as client:
            resp = await client.get(target_url, follow_redirects=True)

        status = resp.status_code
        reachable = 200 <= status < 300

        heartbeat.last_http_status = status
        heartbeat.is_reachable = reachable
        heartbeat.last_seen_at = now if reachable else heartbeat.last_seen_at
        heartbeat.consecutive_failures = 0 if reachable else (heartbeat.consecutive_failures or 0) + 1
        heartbeat.total_checks = (heartbeat.total_checks or 0) + 1
        heartbeat.last_checked_at = now

    except Exception as exc:
        logger.warning("Heartbeat failure for %s: %s", card.url, exc)
        heartbeat.is_reachable = False
        heartbeat.consecutive_failures = (heartbeat.consecutive_failures or 0) + 1
        heartbeat.total_checks = (heartbeat.total_checks or 0) + 1
        heartbeat.last_checked_at = now
        heartbeat.error_details = str(exc)[:512]

    # Suspend after too many consecutive failures
    if (heartbeat.consecutive_failures or 0) >= MAX_CONSECUTIVE_FAILURES:
        if entry.approval_status == "active":
            entry.approval_status = "suspended"
            logger.warning(
                "Agent %s suspended after %d consecutive heartbeat failures",
                card.url,
                heartbeat.consecutive_failures,
            )
    elif heartbeat.is_reachable and entry.approval_status == "suspended":
        # Auto-recover when reachable again
        entry.approval_status = "active"
        logger.info("Agent %s recovered — approval_status restored to active", card.url)


async def _run_heartbeat_cycle(session_factory: async_sessionmaker) -> None:
    """Run one full heartbeat cycle across all registered agents."""
    async with session_factory() as session:
        result = await session.execute(
            select(RegistryEntry, AgentCard, Heartbeat)
            .join(AgentCard, RegistryEntry.agent_card_id == AgentCard.id)
            .outerjoin(Heartbeat, Heartbeat.agent_card_id == AgentCard.id)
            .where(
                AgentCard.is_active.is_(True),
                RegistryEntry.approval_status.in_(["active", "suspended"]),
            )
        )
        rows = result.all()

        for entry, card, heartbeat in rows:
            if heartbeat is None:
                heartbeat = Heartbeat(
                    agent_card_id=card.id,
                    check_interval_seconds=60,
                )
                session.add(heartbeat)
                await session.flush()

            await _check_agent(card, heartbeat, entry, session)

        await session.commit()


async def _scheduler_loop(
    session_factory: async_sessionmaker,
    interval_seconds: int,
) -> None:
    """Main loop — runs checks every `interval_seconds`."""
    logger.info("Heartbeat scheduler started (interval=%ds)", interval_seconds)
    while True:
        try:
            await _run_heartbeat_cycle(session_factory)
        except Exception as exc:
            logger.error("Heartbeat cycle error: %s", exc, exc_info=True)
        await asyncio.sleep(interval_seconds)


async def start_heartbeat_scheduler(
    session_factory: async_sessionmaker,
    interval_seconds: int = 60,
) -> asyncio.Task:
    """
    Start the heartbeat background task.
    Returns the Task so callers can cancel it on shutdown.
    """
    global _scheduler_task
    if _scheduler_task and not _scheduler_task.done():
        return _scheduler_task
    _scheduler_task = asyncio.create_task(
        _scheduler_loop(session_factory, interval_seconds),
        name="a2a_heartbeat_scheduler",
    )
    return _scheduler_task


async def stop_heartbeat_scheduler() -> None:
    """Cancel the heartbeat background task gracefully."""
    global _scheduler_task
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
        try:
            await _scheduler_task
        except asyncio.CancelledError:
            pass
        logger.info("Heartbeat scheduler stopped")
