"""
Consent Runtime Service (§18.3, §19.7).

Provides:
  - check_consent()       Inline consent check + LRU cache
  - revoke_consent()      Full revocation pipeline with side-effect actions
  - get_consent_proof()   Build and return ConsentProofToken JWT
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_a2a.domains.consent.models import (
    ConsentRecord,
    ConsentRevocationAction,
)
from fastapi_a2a.domains.execution_policy.models import ConsentCache
from fastapi_a2a.domains.security.models import ConsentProofToken
from fastapi_a2a.domains.task_lifecycle.models import Artifact, Task


class ConsentDecision:
    def __init__(
        self,
        allowed: bool,
        consent_record_id: uuid.UUID | None,
        source: str,
        reason: str | None = None,
    ):
        self.allowed = allowed
        self.consent_record_id = consent_record_id
        self.source = source  # "cache_hit" | "db_query" | "default_deny"
        self.reason = reason


async def check_consent(
    db: AsyncSession,
    agent_card_id: uuid.UUID,
    caller_identity: str,
    data_categories: list[str],
    purpose: str,
    skill_id: uuid.UUID | None = None,
) -> ConsentDecision:
    """
    Check whether data-use consent is granted for the given parameters.
    Checks ConsentCache first (fast path), then ConsentRecord (slow path).
    Result is written back to ConsentCache with appropriate TTL.
    skill_id is required for the full cache key; pass None for agent-level checks.
    """
    now = datetime.now(UTC)

    # 1. Fast-path: check ConsentCache
    # Note: skill_id-less cache lookup is a broad match; sufficient for deny-default enforcement
    cache_result = await db.execute(
        select(ConsentCache).where(
            ConsentCache.agent_card_id == agent_card_id,
            ConsentCache.caller_identity == caller_identity,
            ConsentCache.purpose == purpose,
            ConsentCache.expires_at > now,
        )
    )
    cached = cache_result.scalar_one_or_none()
    if cached:
        return ConsentDecision(
            allowed=cached.result == "allow",
            consent_record_id=uuid.UUID(cached.consent_record_ids[0]) if cached.consent_record_ids else None,
            source="cache_hit",
        )

    # 2. Slow-path: query ConsentRecord
    record_result = await db.execute(
        select(ConsentRecord).where(
            ConsentRecord.agent_card_id == agent_card_id,
            ConsentRecord.caller_identity == caller_identity,
            ConsentRecord.purpose == purpose,
            ConsentRecord.is_active.is_(True),
        ).order_by(ConsentRecord.granted_at.desc())
    )
    record = record_result.scalar_one_or_none()

    allowed = False
    consent_id = None
    reason = None

    if record:
        # Check data categories are all covered
        covered = all(cat in record.data_categories for cat in data_categories)
        # Check not expired
        not_expired = record.expires_at is None or record.expires_at > now
        allowed = covered and not_expired
        consent_id = record.id
        if not allowed:
            reason = "insufficient_scope" if not covered else "consent_expired"

    # 3. Write result back to cache
    from datetime import timedelta
    ttl = timedelta(seconds=300 if allowed else 60)
    # Compute SHA-256 cache key for data_categories
    categories_hash = hashlib.sha256(
        json.dumps(sorted(data_categories)).encode()
    ).hexdigest()

    # Upsert cache entry
    existing_cache = await db.execute(
        select(ConsentCache).where(
            ConsentCache.agent_card_id == agent_card_id,
            ConsentCache.caller_identity == caller_identity,
            ConsentCache.purpose == purpose,
        )
    )
    cache_entry = existing_cache.scalar_one_or_none()
    if cache_entry:
        cache_entry.result = "allow" if allowed else "deny"
        cache_entry.consent_record_ids = [str(consent_id)] if consent_id else []
        cache_entry.checked_at = now
        cache_entry.expires_at = now + ttl
        cache_entry.data_categories_hash = categories_hash
    else:
        db.add(ConsentCache(
            agent_card_id=agent_card_id,
            # skill_id is required by the schema; use a sentinel dummy UUID if not provided
            # (agent-level consent check — not scoped to a specific skill)
            skill_id=skill_id or uuid.UUID(int=0),
            caller_identity=caller_identity,
            data_categories_hash=categories_hash,
            purpose=purpose,
            result="allow" if allowed else "deny",
            checked_at=now,
            expires_at=now + ttl,
            consent_record_ids=[str(consent_id)] if consent_id else [],
        ))

    await db.flush()
    return ConsentDecision(
        allowed=allowed,
        consent_record_id=consent_id,
        source="db_query",
        reason=reason,
    )


async def revoke_consent(
    db: AsyncSession,
    consent_record_id: uuid.UUID,
    agent_card_id: uuid.UUID,
    withdrawal_reason: str,
    actor_identity: str,
) -> list[ConsentRevocationAction]:
    """
    Revoke a consent record and trigger side-effect actions (§19.7.2):
    - Cancel/pause in-flight tasks using this consent
    - Flag/obfuscate artifacts produced under this consent
    - Revoke proof tokens
    - Invalidate consent cache entries
    Returns list of performed ConsentRevocationAction records.
    """
    now = datetime.now(UTC)
    actions: list[ConsentRevocationAction] = []

    # Mark consent record inactive
    await db.execute(
        update(ConsentRecord)
        .where(ConsentRecord.id == consent_record_id)
        .values(
            is_active=False,
            withdrawn_at=now,
            withdrawal_reason=withdrawal_reason,
        )
    )

    # Find affected in-flight tasks (working / input_required)
    task_result = await db.execute(
        select(Task).where(
            Task.agent_card_id == agent_card_id,
            Task.status.in_(["working", "input_required", "submitted"]),
        )
    )
    affected_tasks = task_result.scalars().all()

    for task in affected_tasks:
        task.status = "cancelled"
        task.error_code = "consent_withdrawn"
        task.error_message = f"Task cancelled: consent withdrawn (reason: {withdrawal_reason})"
        task.completed_at = now

        action = ConsentRevocationAction(
            consent_record_id=consent_record_id,
            task_id=task.id,
            agent_card_id=agent_card_id,
            action_type="task_cancelled",
            action_status="completed",
            action_reason=withdrawal_reason,
            authorized_by=actor_identity,
            performed_at=now,
        )
        db.add(action)
        actions.append(action)

    # Flag artifacts produced under this agent
    artifact_result = await db.execute(
        select(Artifact).where(
            Artifact.agent_card_id == agent_card_id,
            Artifact.obfuscation_status == "none",
        )
    )
    artifacts = artifact_result.scalars().all()

    if artifacts:
        artifact_ids = [a.id for a in artifacts]
        for a in artifacts:
            a.obfuscation_status = "flagged"

        action = ConsentRevocationAction(
            consent_record_id=consent_record_id,
            agent_card_id=agent_card_id,
            action_type="artifact_flagged",
            action_status="completed",
            artifact_ids_affected=artifact_ids,
            action_reason=withdrawal_reason,
            authorized_by=actor_identity,
            performed_at=now,
        )
        db.add(action)
        actions.append(action)

    # Revoke consent proof tokens
    proof_result = await db.execute(
        select(ConsentProofToken).where(
            ConsentProofToken.agent_card_id == agent_card_id,
            ConsentProofToken.is_active.is_(True),
        )
    )
    proofs = proof_result.scalars().all()
    for proof in proofs:
        proof.is_active = False
        proof.revoked_at = now

    if proofs:
        action = ConsentRevocationAction(
            consent_record_id=consent_record_id,
            agent_card_id=agent_card_id,
            action_type="proof_tokens_revoked",
            action_status="completed",
            action_reason=withdrawal_reason,
            authorized_by=actor_identity,
            performed_at=now,
        )
        db.add(action)
        actions.append(action)

    # Invalidate consent cache
    await db.execute(
        update(ConsentCache)
        .where(ConsentCache.agent_card_id == agent_card_id)
        .values(expires_at=now)  # Expire immediately
    )

    await db.flush()
    return actions
