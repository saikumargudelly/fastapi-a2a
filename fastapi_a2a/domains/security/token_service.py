"""
Token Issuance & Family Rotation Service (§17.7).

Provides:
  - issue_token()         Issue a new JWT bound to a token family
  - rotate_token()        Family-rotation: revoke old token, issue successor
  - revoke_family()       Revoke entire family on breach detection
  - audit_token_event()   Append to token_audit_log (dual-write safe)
"""
from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from jose import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_a2a.domains.security.models import AgentToken
from fastapi_a2a.domains.token_hardening.models import TokenAuditLog, TokenFamily

_DEFAULT_ACCESS_TTL_SECONDS = 900       # 15 min
_DEFAULT_REFRESH_TTL_SECONDS = 86400    # 24 hr


async def issue_token(
    db: AsyncSession,
    agent_card_id: uuid.UUID,
    subject: str,
    signing_key_pem: str,
    algorithm: str = "ES256",
    kid: str | None = None,
    token_type: str = "access",  # noqa: S107
    ttl_seconds: int | None = None,
    scope: list[str] | None = None,
    audience: str | None = None,
    issuer: str | None = None,
    extra_claims: dict[str, Any] | None = None,
    family_id: uuid.UUID | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    """
    Issue a new JWT and record it in the token_hardening domain.
    Returns: {"access_token": str, "token_type": "Bearer", "expires_in": int, "jti": str}
    """
    now = datetime.now(UTC)
    ttl = ttl_seconds or (
        _DEFAULT_ACCESS_TTL_SECONDS if token_type == "access" else _DEFAULT_REFRESH_TTL_SECONDS  # noqa: S105
    )
    expires_at = now + timedelta(seconds=ttl)
    jti = secrets.token_urlsafe(24)

    # Resolve or create token family
    if family_id is None:
        family = TokenFamily(
            agent_card_id=agent_card_id,
            root_token_jti=jti,
            subject=subject,
            status="active",
        )
        db.add(family)
        await db.flush()
        family_id = family.id
    else:
        family_result = await db.execute(
            select(TokenFamily).where(TokenFamily.id == family_id)
        )
        family = family_result.scalar_one_or_none()
        if family is None or family.status == "revoked":
            raise ValueError(f"Token family {family_id} is invalid or revoked")

    # Build JWT claims
    claims = {
        "sub": subject,
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "token_type": token_type,
        "agent_card_id": str(agent_card_id),
        "family_id": str(family_id),
    }
    if audience:
        claims["aud"] = audience
    if issuer:
        claims["iss"] = issuer
    if scope:
        claims["scope"] = " ".join(scope)
    if extra_claims:
        claims.update(extra_claims)

    headers = {}
    if kid:
        headers["kid"] = kid

    token_str = jwt.encode(claims, signing_key_pem, algorithm=algorithm, headers=headers or None)

    # Persist AgentToken record
    agent_token = AgentToken(
        agent_card_id=agent_card_id,
        jti=jti,
        family_id=family_id,
        subject=subject,
        token_type=token_type,
        algorithm=algorithm,
        issued_at=now,
        expires_at=expires_at,
        scope=scope or [],
        audience=audience,
        issuer=issuer,
    )
    db.add(agent_token)

    # Audit log entry
    db.add(TokenAuditLog(
        agent_token_jti=jti,
        family_id=family_id,
        event_type="issued",
        subject=subject,
        ip_address=ip_address,
        user_agent=user_agent,
        occurred_at=now,
    ))

    await db.flush()

    return {
        "access_token": token_str,
        "token_type": "Bearer",
        "expires_in": ttl,
        "jti": jti,
        "family_id": str(family_id),
    }


async def rotate_token(
    db: AsyncSession,
    old_jti: str,
    signing_key_pem: str,
    algorithm: str = "ES256",
    kid: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    """
    Family-rotation: revoke old token, issue successor.
    Detects reuse attacks (refresh token already rotated) → revoke entire family.
    """
    now = datetime.now(UTC)

    # Load old token
    result = await db.execute(
        select(AgentToken).where(AgentToken.jti == old_jti)
    )
    old_token = result.scalar_one_or_none()
    if old_token is None:
        raise ValueError(f"Token '{old_jti}' not found")

    if old_token.is_revoked:
        # Reuse attack detected — revoke entire family
        if old_token.family_id is not None:
            await revoke_family(db, old_token.family_id, reason="reuse_attack_detected")
        raise ValueError("Token already rotated — possible reuse attack; family revoked")

    # Revoke old token
    old_token.is_revoked = True
    old_token.revoked_at = now
    old_token.revoke_reason = "rotated"

    db.add(TokenAuditLog(
        agent_token_jti=old_jti,
        family_id=old_token.family_id,
        event_type="rotated",
        subject=old_token.subject,
        ip_address=ip_address,
        user_agent=user_agent,
        occurred_at=now,
    ))

    # Issue successor token in same family
    return await issue_token(
        db=db,
        agent_card_id=old_token.agent_card_id,
        subject=old_token.subject,
        signing_key_pem=signing_key_pem,
        algorithm=algorithm,
        kid=kid,
        token_type=old_token.token_type,
        scope=old_token.scope,
        audience=old_token.audience,
        issuer=old_token.issuer,
        family_id=old_token.family_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )


async def revoke_family(
    db: AsyncSession,
    family_id: uuid.UUID,
    reason: str = "explicit_revoke",
) -> int:
    """
    Revoke entire token family and all its member tokens.
    Returns count of tokens revoked.
    """
    now = datetime.now(UTC)

    # Revoke family record
    family_result = await db.execute(
        select(TokenFamily).where(TokenFamily.id == family_id)
    )
    family = family_result.scalar_one_or_none()
    if family:
        family.status = "revoked"
        family.revoked_at = now
        family.revoke_reason = reason

    # Revoke all member tokens
    tokens_result = await db.execute(
        select(AgentToken).where(
            AgentToken.family_id == family_id,
            AgentToken.is_revoked.is_(False),
        )
    )
    tokens = tokens_result.scalars().all()
    for token in tokens:
        token.is_revoked = True
        token.revoked_at = now
        token.revoke_reason = f"family_revoked:{reason}"

        db.add(TokenAuditLog(
            agent_token_jti=token.jti,
            family_id=family_id,
            event_type="family_revoked",
            subject=token.subject,
            details={"reason": reason},
            occurred_at=now,
        ))

    await db.flush()
    return len(tokens)
