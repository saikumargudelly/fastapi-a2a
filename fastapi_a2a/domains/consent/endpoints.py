"""
Consent & Governance Admin Endpoints (§19.7.4):
  - POST /admin/consent/revoke         Revoke a consent + trigger side-effects
  - POST /admin/consent/recovery        Emergency re-allow with full audit trail
  - GET  /admin/consent/{record_id}     Get consent record + revocation actions
  - POST /admin/consent/grant           Grant a new consent record
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_a2a.domains.consent.models import ConsentRecord, ConsentRevocationAction
from fastapi_a2a.domains.consent.runtime import revoke_consent

router = APIRouter(tags=["Consent Admin"], prefix="/admin/consent")


@router.post("/grant")
async def grant_consent(request: Request) -> dict[str, Any]:
    """Grant a new consent record for a caller."""
    db: AsyncSession = request.state.db
    body = await request.json()

    agent_card_id: uuid.UUID = request.app.state.agent_card_id
    caller_identity: str = body.get("caller_identity", "")
    data_categories: list[str] = body.get("data_categories", [])
    purpose: str = body.get("purpose", "")
    data_region: str | None = body.get("data_region")

    if not caller_identity or not purpose or not data_categories:
        raise HTTPException(status_code=422, detail="caller_identity, purpose, and data_categories are required")

    from datetime import timedelta
    expires_in_days: int | None = body.get("expires_in_days")

    record = ConsentRecord(
        agent_card_id=agent_card_id,
        data_subject_identity=body.get("data_subject_identity", caller_identity),
        caller_identity=caller_identity,
        data_categories=data_categories,
        purpose=purpose,
        is_active=True,
        granted_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=expires_in_days) if expires_in_days else None,
        data_region=data_region,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)

    return {
        "consent_record_id": str(record.id),
        "status": "granted",
        "caller_identity": caller_identity,
        "purpose": purpose,
        "data_categories": data_categories,
        "granted_at": record.granted_at.isoformat(),
        "expires_at": record.expires_at.isoformat() if record.expires_at else None,
    }


@router.post("/revoke")
async def revoke_consent_endpoint(request: Request) -> dict[str, Any]:
    """
    Revoke a consent record and trigger all side-effect actions:
    cancel tasks, flag artifacts, revoke proof tokens.
    """
    db: AsyncSession = request.state.db
    agent_card_id: uuid.UUID = request.app.state.agent_card_id
    body = await request.json()

    consent_record_id: str | None = body.get("consent_record_id")
    withdrawal_reason: str = body.get("reason", "data_subject_request")
    actor: str = body.get("actor_identity", "admin")

    if not consent_record_id:
        raise HTTPException(status_code=422, detail="consent_record_id required")

    cid = uuid.UUID(consent_record_id)
    result = await db.execute(select(ConsentRecord).where(ConsentRecord.id == cid))
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="Consent record not found")
    if not record.is_active:
        raise HTTPException(status_code=409, detail="Consent already revoked")

    actions = await revoke_consent(db, cid, agent_card_id, withdrawal_reason, actor)
    await db.commit()

    return {
        "consent_record_id": consent_record_id,
        "status": "revoked",
        "actions_performed": [
            {"action_type": a.action_type, "action_status": a.action_status}
            for a in actions
        ],
        "withdrawn_at": datetime.now(UTC).isoformat(),
    }


@router.post("/recovery")
async def consent_emergency_recovery(request: Request) -> dict[str, Any]:
    """
    Emergency re-allowance of a task that was stopped mid-flight due to consent
    withdrawal. Requires legal_basis + authorized_by for full audit trail (§19.7.5).
    """
    db: AsyncSession = request.state.db
    agent_card_id: uuid.UUID = request.app.state.agent_card_id
    body = await request.json()

    task_id: str | None = body.get("task_id")
    consent_record_id: str | None = body.get("consent_record_id")
    authorized_by: str | None = body.get("authorized_by")
    emergency_reason: str | None = body.get("emergency_reason")
    legal_basis: str | None = body.get("legal_basis")

    if not all([task_id, authorized_by, emergency_reason, legal_basis]):
        raise HTTPException(
            status_code=422,
            detail="task_id, authorized_by, emergency_reason, and legal_basis are all required"
        )

    action = ConsentRevocationAction(
        consent_record_id=uuid.UUID(consent_record_id) if consent_record_id else uuid.uuid4(),
        task_id=uuid.UUID(task_id),
        agent_card_id=agent_card_id,
        action_type="task_allowed_emergency",
        action_status="completed",
        authorized_by=authorized_by,
        emergency_reason=emergency_reason,
        legal_basis=legal_basis,
        performed_at=datetime.now(UTC),
    )
    db.add(action)
    await db.commit()

    return {
        "task_id": task_id,
        "status": "emergency_recovery_logged",
        "authorized_by": authorized_by,
        "legal_basis": legal_basis,
        "action_id": str(action.id),
    }


@router.get("/{record_id}")
async def get_consent_record(record_id: uuid.UUID, request: Request) -> dict[str, Any]:
    """Get a consent record and its revocation action history."""
    db: AsyncSession = request.state.db

    result = await db.execute(select(ConsentRecord).where(ConsentRecord.id == record_id))
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="Consent record not found")

    actions_result = await db.execute(
        select(ConsentRevocationAction).where(
            ConsentRevocationAction.consent_record_id == record_id
        ).order_by(ConsentRevocationAction.performed_at)
    )
    actions = actions_result.scalars().all()

    return {
        "id": str(record.id),
        "caller_identity": record.caller_identity,
        "data_subject_identity": record.data_subject_identity,
        "data_categories": record.data_categories,
        "purpose": record.purpose,
        "is_active": record.is_active,
        "granted_at": record.granted_at.isoformat(),
        "expires_at": record.expires_at.isoformat() if record.expires_at else None,
        "withdrawn_at": record.withdrawn_at.isoformat() if record.withdrawn_at else None,
        "withdrawal_reason": record.withdrawal_reason,
        "data_region": record.data_region,
        "revocation_actions": [
            {
                "action_type": a.action_type,
                "action_status": a.action_status,
                "performed_at": a.performed_at.isoformat(),
            }
            for a in actions
        ],
    }
