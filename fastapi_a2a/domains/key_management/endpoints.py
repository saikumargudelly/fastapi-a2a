"""
Key Management endpoints:
  - GET  /.well-known/agent-jwks.json    JWKS discovery
  - GET  /.well-known/agent-crl.json     Certificate Revocation List
  - POST /admin/keys/rotate              Rotate signing key
  - POST /admin/keys/{kid}/revoke        Revoke a key by kid
"""
from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_a2a.domains.core_a2a.schemas import JwksKey, JwksResponse
from fastapi_a2a.domains.key_management.models import CardSigningEvent, CardSigningKey

router = APIRouter(tags=["Key Management"])

_JWKS_POLL_INTERVAL_SECONDS = 300  # 5 minutes default


@router.get("/.well-known/agent-jwks.json", response_model=JwksResponse)
async def get_jwks(request: Request) -> JwksResponse:
    """
    Serves the agent's JSON Web Key Set.
    Includes active keys + retired keys still within their grace period.
    Returns cache-bust token and successor key hint for cooperative clients.
    """
    db: AsyncSession = request.state.db
    card_id: uuid.UUID = request.app.state.agent_card_id

    now = datetime.now(UTC)

    result = await db.execute(
        select(CardSigningKey).where(
            CardSigningKey.agent_card_id == card_id,
            CardSigningKey.status.in_(["active", "retired"]),
        ).order_by(CardSigningKey.published_at.desc())
    )
    keys = result.scalars().all()

    jwks_keys = []
    for key in keys:
        # Determine grace expiry for retired keys (keep 24h after retirement)
        grace_expires = None
        if key.retired_at:
            grace_expires = key.retired_at + timedelta(hours=24)
            if now > grace_expires:
                continue  # Grace period expired — omit from JWKS

        jwk_entry = JwksKey(
            kid=key.kid,
            kty=key.public_jwk.get("kty", "EC"),
            crv=key.public_jwk.get("crv"),
            use="sig",
            alg=key.algorithm,
            x=key.public_jwk.get("x"),
            y=key.public_jwk.get("y"),
            n=key.public_jwk.get("n"),
            e=key.public_jwk.get("e"),
            status="active" if key.status == "active" else "retired",
            published_at=key.published_at,
            expires_at=key.expires_at,
            grace_expires_at=grace_expires,
            rotation_successor_kid=key.rotation_successor_kid,
        )
        jwks_keys.append(jwk_entry)

    # Determine next poll time
    next_poll = now + timedelta(seconds=_JWKS_POLL_INTERVAL_SECONDS)

    return JwksResponse(
        keys=jwks_keys,
        crl_url=f"{request.base_url}.well-known/agent-crl.json",
        jwks_version=now,
        next_poll_after=next_poll,
    )


@router.get("/.well-known/agent-crl.json")
async def get_crl(request: Request) -> dict[str, Any]:
    """
    Serves the Certificate Revocation List — lists revoked key IDs and reason.
    """
    db: AsyncSession = request.state.db
    card_id: uuid.UUID = request.app.state.agent_card_id

    result = await db.execute(
        select(CardSigningKey).where(
            CardSigningKey.agent_card_id == card_id,
            CardSigningKey.status == "revoked",
        ).order_by(CardSigningKey.revoked_at.desc())
    )
    revoked_keys = result.scalars().all()

    return {
        "revoked_keys": [
            {
                "kid": key.kid,
                "revoked_at": key.revoked_at.isoformat() if key.revoked_at else None,
                "reason": key.revoke_reason,
            }
            for key in revoked_keys
        ],
        "generated_at": datetime.now(UTC).isoformat(),
    }


@router.post("/admin/keys/rotate")
async def rotate_signing_key(request: Request) -> dict[str, Any]:
    """
    Rotate the active signing key:
    1. Retire the current active key
    2. Create a new active key (caller must supply new public_jwk + algorithm)
    3. Record CardSigningEvent for both operations
    """
    db: AsyncSession = request.state.db
    card_id: uuid.UUID = request.app.state.agent_card_id
    body = await request.json()

    new_public_jwk: dict | None = body.get("public_jwk")
    algorithm: str = body.get("algorithm", "ES256")
    expires_in_days: int = body.get("expires_in_days", 365)
    actor: str = body.get("actor_identity", "system")

    if not new_public_jwk:
        raise HTTPException(status_code=422, detail="public_jwk is required")
    if algorithm not in ("ES256", "RS256", "EdDSA"):
        raise HTTPException(status_code=422, detail="Unsupported algorithm")

    now = datetime.now(UTC)

    # Retire current active key
    active_result = await db.execute(
        select(CardSigningKey).where(
            CardSigningKey.agent_card_id == card_id,
            CardSigningKey.status == "active",
        )
    )
    old_key = active_result.scalar_one_or_none()
    old_kid = None

    if old_key:
        old_kid = old_key.kid
        old_key.status = "retired"
        old_key.retired_at = now
        db.add(CardSigningEvent(
            card_signing_key_id=old_key.id,
            agent_card_id=card_id,
            event_type="rotated",
            prior_kid=None,
            details={"new_kid": "pending"},
            actor_identity=actor,
        ))

    # Create new active key
    new_kid = secrets.token_urlsafe(16)
    new_key = CardSigningKey(
        agent_card_id=card_id,
        kid=new_kid,
        algorithm=algorithm,
        public_jwk=new_public_jwk,
        status="active",
        expires_at=now + timedelta(days=expires_in_days),
        published_at=now,
        jwks_cache_bust_token=secrets.token_hex(8),
    )
    if old_key:
        old_key.rotation_successor_kid = new_kid
    db.add(new_key)
    await db.flush()

    db.add(CardSigningEvent(
        card_signing_key_id=new_key.id,
        agent_card_id=card_id,
        event_type="created",
        prior_kid=old_kid,
        details={"algorithm": algorithm, "expires_in_days": expires_in_days},
        actor_identity=actor,
    ))

    await db.commit()

    return {
        "new_kid": new_kid,
        "retired_kid": old_kid,
        "algorithm": algorithm,
        "expires_at": new_key.expires_at.isoformat() if new_key.expires_at else None,
        "jwks_cache_bust_token": new_key.jwks_cache_bust_token,
        "status": "rotated",
    }


@router.post("/admin/keys/{kid}/revoke")
async def revoke_signing_key(kid: str, request: Request) -> dict[str, Any]:
    """Revoke a signing key by kid — moves it to CRL immediately."""
    db: AsyncSession = request.state.db
    card_id: uuid.UUID = request.app.state.agent_card_id
    body = await request.json()
    reason: str = body.get("reason", "unspecified")
    actor: str = body.get("actor_identity", "system")

    result = await db.execute(
        select(CardSigningKey).where(
            CardSigningKey.kid == kid,
            CardSigningKey.agent_card_id == card_id,
        )
    )
    key = result.scalar_one_or_none()
    if key is None:
        raise HTTPException(status_code=404, detail=f"Key '{kid}' not found")
    if key.status == "revoked":
        raise HTTPException(status_code=409, detail="Key already revoked")

    now = datetime.now(UTC)
    key.status = "revoked"
    key.revoked_at = now
    key.revoke_reason = reason

    db.add(CardSigningEvent(
        card_signing_key_id=key.id,
        agent_card_id=card_id,
        event_type="revoked",
        details={"reason": reason},
        actor_identity=actor,
    ))

    await db.commit()
    return {"kid": kid, "status": "revoked", "revoked_at": now.isoformat(), "reason": reason}
