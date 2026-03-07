"""
SQLAlchemy ORM models for Token Hardening domain:
  - token_family, token_audit_log, token_rate_limit, token_rate_limit_shard,
    dual_write_queue
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from fastapi_a2a.database import Base


class TokenFamily(Base):
    """Token rotation lineage group."""
    __tablename__ = "token_family"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_card.id"), nullable=False, index=True
    )
    caller_identity: Mapped[str | None] = mapped_column(String(512), index=True)
    family_name: Mapped[str | None] = mapped_column(String(256), doc="Human-readable label e.g. 'prod-service-key'")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", index=True,
        doc="active | compromised | retired"
    )
    compromise_reason: Mapped[str | None] = mapped_column(
        Text, doc="Required when status=compromised — forensics trail"
    )
    compromise_score: Mapped[float | None] = mapped_column(
        Float,
        doc="Anomaly score 0.0-1.0 derived from token_audit_log; auto=1.0 on compromise; >0.7 triggers alert"
    )
    kms_key_ref: Mapped[str | None] = mapped_column(
        String(256),
        doc="KMS key reference for self-issued JWTs e.g. 'aws:kms:arn:...'; never store private key material"
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    parent_family_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    compromised_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_rotation_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), doc="Timestamp of most recent token rotation within this family"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "status IN ('active','compromised','retired')",
            name="ck_token_family_status"
        ),
        CheckConstraint(
            "(status != 'compromised') OR (compromise_reason IS NOT NULL)",
            name="ck_token_family_compromise_reason"
        ),
    )


class TokenAuditLog(Base):
    """Immutable append-only audit log for all token operations."""
    __tablename__ = "token_audit_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_token_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    family_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    agent_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_card.id"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True,
        doc="issued | used | rejected | revoked | rotated | family_compromised | brute_force_detected"
    )
    caller_identity: Mapped[str | None] = mapped_column(String(512), index=True)
    request_ip: Mapped[str | None] = mapped_column(String(64))
    request_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    logged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )


class TokenRateLimit(Base):
    """Per-token sliding-window rate limit state (Token Hardening domain)."""
    __tablename__ = "token_rate_limit"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_token_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_token.id"), nullable=False, unique=True, index=True
    )
    # Sliding-window state
    window_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), doc="Start of the current sliding time window"
    )
    window_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60, doc="Window duration in seconds")
    request_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, doc="Requests in current window; reset on window roll"
    )
    max_requests: Mapped[int] = mapped_column(Integer, nullable=False, doc="Upper bound before throttling activates")
    # Burst limiting
    burst_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, doc="Requests in last 1-second micro-window — burst spike detection"
    )
    max_burst: Mapped[int] = mapped_column(
        Integer, nullable=False, doc="Upper bound for burst_count; breach triggers immediate 429"
    )
    last_request_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), doc="Most recent request timestamp — used for window slide calculation"
    )
    throttled_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True,
        doc="If set, all requests rejected with 429 until this timestamp"
    )
    lifetime_request_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0,
        doc="Total cumulative requests ever made with this token — never reset"
    )
    # v0.6.0 Redis sharding fields
    use_redis: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    shard_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    redis_fallback_allow: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("window_seconds > 0 AND max_requests > 0 AND max_burst > 0", name="ck_token_rate_limit_positive"),
        CheckConstraint("request_count >= 0 AND burst_count >= 0 AND lifetime_request_count >= 0", name="ck_token_rate_limit_non_negative"),
        CheckConstraint("shard_count BETWEEN 1 AND 64", name="ck_token_rate_limit_shards"),
    )


class TokenRateLimitShard(Base):
    """Redis shard sync record for hot-path rate limiting (v0.6.0)."""
    __tablename__ = "token_rate_limit_shard"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token_rate_limit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("token_rate_limit.id"), nullable=False, index=True
    )
    agent_token_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    shard_index: Mapped[int] = mapped_column(Integer, nullable=False)
    shard_count: Mapped[int] = mapped_column(Integer, nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    redis_key: Mapped[str] = mapped_column(String(256), nullable=False)
    max_requests_per_shard: Mapped[int] = mapped_column(Integer, nullable=False)
    redis_counter_at_sync: Mapped[int | None] = mapped_column(Integer)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    redis_available_at_sync: Mapped[bool | None] = mapped_column(Boolean)
    denied_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("agent_token_id", "shard_index", "window_start", name="uq_token_rate_limit_shard"),
    )


class DualWriteQueue(Base):
    """Transactional outbox for cross-region audit durability (v0.6.0)."""
    __tablename__ = "dual_write_queue"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_table: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True,
        doc="token_audit_log | card_history | startup_audit_log | trace_compliance_job"
    )
    source_row_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    sequence_number: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    delivery_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", index=True,
        doc="pending | enqueued | delivered | failed | reconciled"
    )
    queue_message_id: Mapped[str | None] = mapped_column(String(256))
    enqueued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivery_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    remote_region: Mapped[str] = mapped_column(String(64), nullable=False)
    remote_checksum_received: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("source_table", "source_row_id", "remote_region", name="uq_dual_write_queue"),
        CheckConstraint("max_attempts BETWEEN 1 AND 100", name="ck_dual_write_queue_attempts"),
    )
