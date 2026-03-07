"""
SQLAlchemy ORM models for Key Management domain:
  - card_signing_key, card_signing_event
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from fastapi_a2a.database import Base


class CardSigningKey(Base):
    """Card signing key lifecycle record with KMS integration."""
    __tablename__ = "card_signing_key"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_card.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    kid: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    algorithm: Mapped[str] = mapped_column(
        String(16), nullable=False, default="ES256",
        doc="ES256 | RS256 | EdDSA"
    )
    kms_key_ref: Mapped[str | None] = mapped_column(String(512), doc="KMS ARN or reference")
    public_jwk: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", index=True,
        doc="active | retired | revoked | archived"
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoke_reason: Mapped[str | None] = mapped_column(String(32))

    # v0.6.0 distributed cache fields
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    rotation_successor_kid: Mapped[str | None] = mapped_column(String(64))
    jwks_cache_bust_token: Mapped[str | None] = mapped_column(String(64))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CardSigningEvent(Base):
    """Audit log for all card signing key lifecycle events."""
    __tablename__ = "card_signing_event"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    card_signing_key_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("card_signing_key.id"), nullable=False, index=True
    )
    agent_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_card.id"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(
        String(32), nullable=False,
        doc="created | rotated | retired | revoked | archived | verification_success | verification_failure | revoked_detected"
    )
    prior_kid: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    actor_identity: Mapped[str | None] = mapped_column(String(256))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
