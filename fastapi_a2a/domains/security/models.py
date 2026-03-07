"""
SQLAlchemy ORM models for Security domain:
  - auth_scheme, agent_token, push_notification_config,
    card_key_revocation_log, consent_proof_token
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from fastapi_a2a.database import Base


class AuthScheme(Base):
    """Authentication scheme supported by an agent."""
    __tablename__ = "auth_scheme"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_card.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    scheme_type: Mapped[str] = mapped_column(
        String(32), nullable=False,
        doc="bearer | apikey | oauth2 | openid | mtls | none"
    )
    config: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentToken(Base):
    """An issued authentication token for an agent."""
    __tablename__ = "agent_token"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_card.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    auth_scheme_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("auth_scheme.id"), nullable=False, index=True
    )
    family_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, doc="SHA-256 hash")
    caller_identity: Mapped[str | None] = mapped_column(String(512), index=True)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoke_reason: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB)


class PushNotificationConfig(Base):
    """Configuration for push notifications by an agent."""
    __tablename__ = "push_notification_config"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_card.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    push_url: Mapped[str] = mapped_column(String(512), nullable=False)
    auth_type: Mapped[str | None] = mapped_column(String(32))
    auth_token_hash: Mapped[str | None] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CardKeyRevocationLog(Base):
    """Append-only revocation feed for card signing keys (v0.5.0)."""
    __tablename__ = "card_key_revocation_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kid: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    agent_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_card.id"), nullable=False, index=True
    )
    card_signing_key_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    revoke_reason: Mapped[str] = mapped_column(
        String(32), nullable=False,
        doc="key_compromise | rotation | operator_request | expiry | legal"
    )
    revoked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    issuer_identity: Mapped[str] = mapped_column(String(256), nullable=False)
    effective_immediately: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    grace_period_seconds: Mapped[int | None] = mapped_column(Integer)
    grace_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    notified_registries: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    notification_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_notification_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "effective_immediately = false OR grace_period_seconds IS NULL",
            name="ck_card_key_revocation_grace"
        ),
        CheckConstraint(
            "grace_period_seconds IS NULL OR (grace_period_seconds >= 0 AND grace_period_seconds <= 604800)",
            name="ck_card_key_revocation_grace_range"
        ),
    )


class ConsentProofToken(Base):
    """Cryptographically signed transitive consent proof for chained agent calls (v0.5.0)."""
    __tablename__ = "consent_proof_token"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    consent_record_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    grantor_identity: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    original_caller_identity: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    allowed_data_categories: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)
    allowed_purpose: Mapped[str] = mapped_column(String(128), nullable=False)
    allowed_skill_ids: Mapped[list[uuid.UUID] | None] = mapped_column(ARRAY(UUID(as_uuid=True)))
    chain_depth_limit: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3,
        doc="Max hops. CHECK BETWEEN 1 AND 10"
    )
    current_depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    token_jwt: Mapped[str] = mapped_column(Text, nullable=False)
    signing_kid: Mapped[str] = mapped_column(String(64), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    max_uses: Mapped[int | None] = mapped_column(Integer)
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoke_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("chain_depth_limit BETWEEN 1 AND 10", name="ck_consent_proof_chain_depth"),
        CheckConstraint("expires_at > issued_at", name="ck_consent_proof_expiry"),
        CheckConstraint("max_uses IS NULL OR max_uses >= 1", name="ck_consent_proof_max_uses"),
    )
