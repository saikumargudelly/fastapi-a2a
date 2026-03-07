"""
Reputation Engine Background Task (§18.9).

Computes AgentReputation scores from:
  - card_scan_result rows (safety scans)
  - synthetic_check_result rows (uptime/conformance checks)
  - heartbeat consecutive_failures (availability)
  - takedown_request status (trust signals)

Score formula (weighted average, range 0–1):
  safety_score     0.40  (clean scans → 1.0, violations → lower)
  availability     0.30  (uptime from synthetic checks)
  conformance      0.20  (synthetic check pass rate)
  trust_signal     0.10  (no active takedowns → 1.0)

Runs every `interval_seconds` for all active agent cards.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import Integer, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from fastapi_a2a.domains.core_a2a.models import AgentCard
from fastapi_a2a.domains.federation.models import TakedownRequest
from fastapi_a2a.domains.registry.models import Heartbeat
from fastapi_a2a.domains.safety.models import (
    AgentReputation,
    CardScanResult,
    SyntheticCheckResult,
)

logger = logging.getLogger("fastapi_a2a.reputation")

_SAFETY_WEIGHT = 0.40
_AVAILABILITY_WEIGHT = 0.30
_CONFORMANCE_WEIGHT = 0.20
_TRUST_WEIGHT = 0.10
_LOOKBACK_DAYS = 30


async def _compute_reputation(
    card: AgentCard,
    db,
) -> dict:
    """Compute all sub-scores for one agent card."""
    now = datetime.now(UTC)
    lookback = now - timedelta(days=_LOOKBACK_DAYS)

    # ── Safety score ──────────────────────────────────────────────────────────
    scan_result = await db.execute(
        select(
            func.count(CardScanResult.id).label("total"),
            func.sum(
                (CardScanResult.scan_status == "clean").cast(Integer)
            ).label("clean"),
        ).where(
            CardScanResult.agent_card_id == card.id,
            CardScanResult.scanned_at >= lookback,
        )
    )
    scan_row = scan_result.one()
    total_scans = scan_row.total or 0
    clean_scans = scan_row.clean or 0
    safety_score = (clean_scans / total_scans) if total_scans > 0 else 0.8  # default 0.8 if no scans

    # ── Availability score ────────────────────────────────────────────────────
    hb_result = await db.execute(
        select(Heartbeat).where(Heartbeat.agent_card_id == card.id)
    )
    heartbeat = hb_result.scalar_one_or_none()
    if heartbeat and heartbeat.total_checks:
        successful = heartbeat.total_checks - (heartbeat.consecutive_failures or 0)
        availability_score = successful / heartbeat.total_checks
    else:
        availability_score = 1.0  # No data → assume available

    # ── Conformance score (synthetic check results) ────────────────────────────
    synth_result = await db.execute(
        select(
            func.count(SyntheticCheckResult.id).label("total"),
            func.sum(
                (SyntheticCheckResult.status == "passed").cast(Integer)
            ).label("passed"),
        ).where(
            SyntheticCheckResult.agent_card_id == card.id,
            SyntheticCheckResult.executed_at >= lookback,
        )
    )
    synth_row = synth_result.one()
    total_synth = synth_row.total or 0
    passed_synth = synth_row.passed or 0
    conformance_score = (passed_synth / total_synth) if total_synth > 0 else 0.9  # default

    # ── Trust signal (active takedown requests lower score) ───────────────────
    takedown_result = await db.execute(
        select(func.count(TakedownRequest.id)).where(
            TakedownRequest.target_agent_url == card.url,
            TakedownRequest.status.in_(["submitted", "under_review"]),
        )
    )
    active_takedowns = takedown_result.scalar() or 0
    trust_score = max(0.0, 1.0 - (active_takedowns * 0.25))  # -0.25 per active takedown

    # ── Weighted aggregate ────────────────────────────────────────────────────
    composite = (
        safety_score * _SAFETY_WEIGHT
        + availability_score * _AVAILABILITY_WEIGHT
        + conformance_score * _CONFORMANCE_WEIGHT
        + trust_score * _TRUST_WEIGHT
    )

    return {
        "safety_score": round(safety_score, 4),
        "availability_score": round(availability_score, 4),
        "conformance_score": round(conformance_score, 4),
        "trust_score": round(trust_score, 4),
        "composite_score": round(composite, 4),
        "total_scans": total_scans,
        "total_synthetic_checks": total_synth,
        "active_takedowns": active_takedowns,
    }


async def _update_reputation_batch(session_factory: async_sessionmaker) -> int:
    """Update reputation for all active agent cards. Returns count updated."""
    updated = 0
    async with session_factory() as db:
        cards_result = await db.execute(
            select(AgentCard).where(AgentCard.is_active.is_(True))
        )
        cards = cards_result.scalars().all()

        for card in cards:
            scores = await _compute_reputation(card, db)

            # Upsert AgentReputation
            rep_result = await db.execute(
                select(AgentReputation).where(AgentReputation.agent_card_id == card.id)
            )
            rep = rep_result.scalar_one_or_none()

            now = datetime.now(UTC)
            if rep:
                rep.safety_score = scores["safety_score"]
                rep.availability_score = scores["availability_score"]
                rep.conformance_score = scores["conformance_score"]
                rep.trust_score = scores["trust_score"]
                rep.composite_score = scores["composite_score"]
                rep.total_scans = scores["total_scans"]
                rep.last_computed_at = now
            else:
                rep = AgentReputation(
                    agent_card_id=card.id,
                    safety_score=scores["safety_score"],
                    availability_score=scores["availability_score"],
                    conformance_score=scores["conformance_score"],
                    trust_score=scores["trust_score"],
                    composite_score=scores["composite_score"],
                    total_scans=scores["total_scans"],
                    last_computed_at=now,
                )
                db.add(rep)
            updated += 1

        await db.commit()
    return updated


async def _reputation_loop(session_factory: async_sessionmaker, interval: int) -> None:
    logger.info("Reputation engine started (interval=%ds)", interval)
    while True:
        try:
            count = await _update_reputation_batch(session_factory)
            logger.debug("Reputation engine: updated %d agent cards", count)
        except Exception as exc:
            logger.error("Reputation engine error: %s", exc, exc_info=True)
        await asyncio.sleep(interval)


_reputation_task: asyncio.Task | None = None


async def start_reputation_engine(
    session_factory: async_sessionmaker,
    interval_seconds: int = 300,  # Every 5 minutes
) -> asyncio.Task:
    global _reputation_task
    if _reputation_task and not _reputation_task.done():
        return _reputation_task
    _reputation_task = asyncio.create_task(
        _reputation_loop(session_factory, interval_seconds),
        name="a2a_reputation_engine",
    )
    return _reputation_task


async def stop_reputation_engine() -> None:
    global _reputation_task
    if _reputation_task and not _reputation_task.done():
        _reputation_task.cancel()
        try:
            await _reputation_task
        except asyncio.CancelledError:
            pass
        logger.info("Reputation engine stopped")
