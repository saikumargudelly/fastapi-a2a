"""
Execution Policy Runtime Services (§18.5, §19.2):

1. Job Lease Reaper     — detect dead workers, reclaim their leases
2. Trace Compliance Runner — daily conformance scan of trace spans
3. SLO Alert Engine     — evaluate SLO breach rules, trigger oncall playbooks
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from fastapi_a2a.domains.execution_policy.models import (
    AlertRule,
    JobLease,
    OncallPlaybook,
    SloDefinition,
    TraceComplianceJob,
    TracePolicy,
)

logger = logging.getLogger("fastapi_a2a.execution_policy")

# ──────────────────────────────────────────────────────────────────────────────
# 1. Job Lease Reaper
# ──────────────────────────────────────────────────────────────────────────────

async def _reap_dead_leases(session_factory: async_sessionmaker) -> int:
    """
    Reclaim job leases from dead workers.
    A lease is 'dead' when heartbeat_expires_at < now AND status = 'held'.
    Returns count of reclaimed leases.
    """
    now = datetime.now(UTC)
    async with session_factory() as db:
        result = await db.execute(
            select(JobLease).where(
                JobLease.status == "held",
                JobLease.heartbeat_expires_at < now,
            )
        )
        dead_leases = result.scalars().all()

        for lease in dead_leases:
            lease.status = "available"
            lease.holder_worker_id = None
            lease.acquired_at = None
            lease.heartbeat_expires_at = None
            lease.reap_count = (lease.reap_count or 0) + 1
            logger.warning("Reclaimed dead lease: job_type=%s worker=%s", lease.job_type, lease.holder_worker_id)

        await db.commit()
        return len(dead_leases)


async def _lease_reaper_loop(session_factory: async_sessionmaker, interval: int) -> None:
    logger.info("Job lease reaper started (interval=%ds)", interval)
    while True:
        try:
            reaped = await _reap_dead_leases(session_factory)
            if reaped:
                logger.info("Lease reaper: reclaimed %d dead leases", reaped)
        except Exception as exc:
            logger.error("Lease reaper error: %s", exc, exc_info=True)
        await asyncio.sleep(interval)


# ──────────────────────────────────────────────────────────────────────────────
# 2. Trace Compliance Runner
# ──────────────────────────────────────────────────────────────────────────────

async def _run_compliance_job(job: TraceComplianceJob, session_factory: async_sessionmaker) -> None:
    """
    Execute one trace compliance scan job.
    Runs against TraceSpan rows in the window [job.window_start, job.window_end].
    Violations trigger retraction or incident creation per trace_policy.
    """
    now = datetime.now(UTC)
    async with session_factory() as db:
        job = await db.get(TraceComplianceJob, job.id)
        if job is None or job.status != "pending":
            return

        job.status = "running"
        job.started_at = now
        await db.commit()

    try:
        # Load trace policy
        async with session_factory() as db:
            policy_result = await db.execute(
                select(TracePolicy).where(TracePolicy.id == job.trace_policy_id)
            )
            policy = policy_result.scalar_one_or_none()
            if policy is None:
                raise ValueError(f"TracePolicy {job.trace_policy_id} not found")

            # Scan trace spans (simplified: count PII violations)
            from fastapi_a2a.domains.tracing.models import TraceSpan
            spans_result = await db.execute(
                select(TraceSpan).where(
                    TraceSpan.agent_card_id == job.agent_card_id,
                    TraceSpan.started_at >= job.window_start,
                    TraceSpan.started_at < job.window_end,
                )
            )
            spans = spans_result.scalars().all()

            violations = 0
            for span in spans:
                # Check for disallowed attributes (deny-by-default if allowlist_mode=enforce)
                if policy.allowlist_mode == "enforce" and span.attributes:
                    allowed = set(policy.allowlist_attributes or [])
                    found = set(span.attributes.keys())
                    disallowed = found - allowed
                    if disallowed:
                        violations += 1
                        if policy.retract_on_violation:
                            span.attributes = {k: v for k, v in span.attributes.items() if k in allowed}

            job.status = "completed"
            job.completed_at = datetime.now(UTC)
            job.violations_found = violations
            job.spans_scanned = len(spans)
            await db.commit()

            logger.info(
                "Compliance job %s: scanned %d spans, %d violations",
                job.id, len(spans), violations
            )

    except Exception as exc:
        async with session_factory() as db:
            job = await db.get(TraceComplianceJob, job.id)
            if job:
                job.status = "failed"
                job.error_message = str(exc)[:512]
                await db.commit()
        logger.error("Compliance job %s failed: %s", job.id, exc)


async def _compliance_loop(session_factory: async_sessionmaker, interval: int) -> None:
    logger.info("Trace compliance runner started (interval=%ds)", interval)
    while True:
        try:
            now = datetime.now(UTC)
            async with session_factory() as db:
                result = await db.execute(
                    select(TraceComplianceJob).where(
                        TraceComplianceJob.status == "pending",
                        TraceComplianceJob.scheduled_at <= now,
                    ).limit(10)
                )
                jobs = result.scalars().all()

            for job in jobs:
                await _run_compliance_job(job, session_factory)
        except Exception as exc:
            logger.error("Compliance runner error: %s", exc, exc_info=True)
        await asyncio.sleep(interval)


# ──────────────────────────────────────────────────────────────────────────────
# 3. SLO Alert Engine
# ──────────────────────────────────────────────────────────────────────────────

async def _evaluate_alert_rules(session_factory: async_sessionmaker) -> None:
    """
    Evaluate all active AlertRules against their SloDefinition.
    Fires OncallPlaybook webhook when threshold breached.
    """
    async with session_factory() as db:
        result = await db.execute(
            select(AlertRule, SloDefinition, OncallPlaybook)
            .join(SloDefinition, AlertRule.slo_definition_id == SloDefinition.id)
            .outerjoin(OncallPlaybook, AlertRule.oncall_playbook_id == OncallPlaybook.id)
            .where(AlertRule.is_active.is_(True))
        )
        rows = result.all()

    for alert_rule, slo, playbook in rows:
        try:
            await _check_and_fire(alert_rule, slo, playbook, session_factory)
        except Exception as exc:
            logger.warning("Alert rule %s evaluation error: %s", alert_rule.id, exc)


async def _check_and_fire(
    alert_rule: AlertRule,
    slo: SloDefinition,
    playbook: OncallPlaybook | None,
    session_factory: async_sessionmaker,
) -> None:
    """
    Evaluate one alert rule. If breach detected, fire playbook webhook.
    Metrics are computed from aggregated task/trace data (simplified here).
    """
    now = datetime.now(UTC)

    # Cooldown check — don't re-fire within cooldown_minutes
    if alert_rule.last_fired_at:
        cooldown = timedelta(minutes=alert_rule.cooldown_minutes or 15)
        if now - alert_rule.last_fired_at < cooldown:
            return

    # Simplified breach detection — real implementation queries task metrics
    # Here we use the SLO's threshold as a stub
    breached = False  # Would be: actual_metric > slo.threshold

    if breached and playbook:
        logger.warning(
            "SLO breach detected: slo=%s rule=%s → firing playbook=%s",
            slo.name, alert_rule.name, playbook.name,
        )
        await _fire_playbook(playbook, alert_rule, slo)
        async with session_factory() as db:
            rule = await db.get(AlertRule, alert_rule.id)
            if rule:
                rule.last_fired_at = now
                rule.fire_count = (rule.fire_count or 0) + 1
                await db.commit()


async def _fire_playbook(playbook: OncallPlaybook, alert_rule: AlertRule, slo: SloDefinition) -> None:
    """Send playbook webhook notification."""
    import httpx
    if not playbook.webhook_url:
        logger.debug("Playbook %s has no webhook_url — skipping", playbook.name)
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                playbook.webhook_url,
                json={
                    "alert_rule": alert_rule.name,
                    "slo": slo.name,
                    "playbook": playbook.name,
                    "severity": alert_rule.severity,
                    "fired_at": datetime.now(UTC).isoformat(),
                },
            )
    except Exception as exc:
        logger.warning("Playbook webhook %s failed: %s", playbook.webhook_url, exc)


async def _alert_engine_loop(session_factory: async_sessionmaker, interval: int) -> None:
    logger.info("SLO alert engine started (interval=%ds)", interval)
    while True:
        try:
            await _evaluate_alert_rules(session_factory)
        except Exception as exc:
            logger.error("Alert engine error: %s", exc, exc_info=True)
        await asyncio.sleep(interval)


# ──────────────────────────────────────────────────────────────────────────────
# Public start/stop API
# ──────────────────────────────────────────────────────────────────────────────

_reaper_task: asyncio.Task | None = None
_compliance_task: asyncio.Task | None = None
_alert_task: asyncio.Task | None = None


async def start_execution_policy_runtime(
    session_factory: async_sessionmaker,
    reaper_interval: int = 30,
    compliance_interval: int = 300,
    alert_interval: int = 60,
) -> None:
    global _reaper_task, _compliance_task, _alert_task
    _reaper_task = asyncio.create_task(
        _lease_reaper_loop(session_factory, reaper_interval), name="a2a_lease_reaper"
    )
    _compliance_task = asyncio.create_task(
        _compliance_loop(session_factory, compliance_interval), name="a2a_compliance_runner"
    )
    _alert_task = asyncio.create_task(
        _alert_engine_loop(session_factory, alert_interval), name="a2a_slo_alert_engine"
    )


async def stop_execution_policy_runtime() -> None:
    for task in [_reaper_task, _compliance_task, _alert_task]:
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    logger.info("Execution policy runtime stopped")
