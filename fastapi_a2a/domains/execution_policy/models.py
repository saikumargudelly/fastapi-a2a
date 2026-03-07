"""
SQLAlchemy ORM models for Execution Policy domain:
  - executor_policy, trace_policy, consent_cache, trace_compliance_job,
    slo_definition, alert_rule, oncall_playbook, job_lease
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
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
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from fastapi_a2a.database import Base


class ExecutorPolicy(Base):
    """Process/container isolation, resource limits, and circuit breakers."""
    __tablename__ = "executor_policy"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_card.id", ondelete="CASCADE"),
        nullable=False, unique=True, index=True
    )
    isolation_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="process",
        doc="process | container | vm | none"
    )
    max_memory_mb: Mapped[int | None] = mapped_column(Integer)
    max_cpu_millicores: Mapped[int | None] = mapped_column(Integer)
    max_task_duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    circuit_breaker_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    circuit_breaker_window_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    circuit_breaker_open: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    policy_config: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TracePolicy(Base):
    """Per-agent trace sampling rate and PII redaction rules (ERD Gap 6)."""
    __tablename__ = "trace_policy"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_card.id", ondelete="CASCADE"),
        nullable=False, unique=True, index=True
    )
    # Sampling
    trace_sample_rate: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.01,
        doc="Fraction 0.0–1.0 of spans to record. Default 1% for public agents."
    )
    # Attribute limits
    max_attribute_length: Mapped[int] = mapped_column(
        Integer, nullable=False, default=256,
        doc="Max character length of any attribute value; excess truncated with [TRUNCATED]"
    )
    max_export_size_bytes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1_048_576,
        doc="Max uncompressed bytes per export batch (default 1 MB); larger batches are split"
    )
    # Redaction rules — JSONB array of {name, pattern, replacement} objects
    redaction_rules: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        doc="Array of redaction objects: [{name, pattern (regex), replacement}]. Applied at INSERT time"
    )
    # Allowlist / blocklist (ERD: TEXT[] columns)
    attribute_allowlist: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list,
        doc="If non-empty, ONLY these attribute keys stored. Higher priority than blocklist"
    )
    attribute_blocklist: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list,
        doc="Attribute keys always dropped before storage (e.g. http.request.body, user.email)"
    )
    # PII hashing
    hash_identifiers: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        doc="If true, SHA-256 HMAC applied to PII-suggestive attribute values before storage"
    )
    hmac_key_ref: Mapped[str | None] = mapped_column(
        String(256), doc="KMS reference for HMAC key when hash_identifiers=true"
    )
    # Master kill switch
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True,
        doc="False disables all tracing for this agent (emergency kill switch)"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("trace_sample_rate BETWEEN 0.0 AND 1.0", name="ck_trace_policy_sample_rate"),
        CheckConstraint("max_attribute_length BETWEEN 32 AND 65536", name="ck_trace_policy_attr_length"),
        CheckConstraint(
            "max_export_size_bytes BETWEEN 1024 AND 104857600",
            name="ck_trace_policy_export_size"
        ),
    )


class ConsentCache(Base):
    """TTL cache of consent_service.check() results per caller/skill (ERD Gap 7)."""
    __tablename__ = "consent_cache"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_card.id"), nullable=False, index=True
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_skill.id"), nullable=False, index=True,
        doc="→ agent_skill.id — the skill whose consent was checked"
    )
    caller_identity: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    data_categories_hash: Mapped[str] = mapped_column(
        String(64), nullable=False,
        doc="SHA-256 of sorted JSON array of data_categories — cache key dimension"
    )
    purpose: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    result: Mapped[str] = mapped_column(
        String(8), nullable=False,
        doc="allow | warn | deny — cached consent check result"
    )
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        doc="When the live consent_record lookup was performed"
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True,
        doc="Cache TTL. Default checked_at + 300s. Deny results use 60s TTL only"
    )
    consent_record_ids: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list,
        doc="consent_record.id[] — which records contributed; used for precise cache invalidation"
    )

    __table_args__ = (
        UniqueConstraint(
            "agent_card_id", "skill_id", "caller_identity", "data_categories_hash", "purpose",
            name="uq_consent_cache_full_key"
        ),
        CheckConstraint("result IN ('allow','warn','deny')", name="ck_consent_cache_result"),
        CheckConstraint("expires_at > checked_at", name="ck_consent_cache_ttl"),
    )



class TraceComplianceJob(Base):
    """Nightly PII compliance scan against closed trace_span records (v0.5.0)."""
    __tablename__ = "trace_compliance_job"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_card.id"), nullable=False, index=True
    )
    scan_window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scan_window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    spans_in_window: Mapped[int] = mapped_column(Integer, nullable=False)
    spans_sampled: Mapped[int] = mapped_column(Integer, nullable=False)
    violation_count: Mapped[int] = mapped_column(Integer, nullable=False)
    violation_details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="running", index=True,
        doc="running | clean | violation_found | error"
    )
    retraction_triggered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    retraction_batch_ids: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    security_incident_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    ran_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_details: Mapped[str | None] = mapped_column(Text)


class SloDefinition(Base):
    """Machine-readable SLO definition (v0.4.0)."""
    __tablename__ = "slo_definition"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slo_name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    agent_card_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_card.id"), index=True
    )
    metric_query: Mapped[str] = mapped_column(Text, nullable=False)
    metric_type: Mapped[str] = mapped_column(
        String(32), nullable=False,
        doc="availability | latency_p95 | latency_p99 | error_rate | throughput | custom"
    )
    target_value: Mapped[float] = mapped_column(Float, nullable=False)
    target_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    measurement_window_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    breach_action: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_sli_value: Mapped[float | None] = mapped_column(Float)
    current_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="unknown",
        doc="ok | warning | breached | unknown"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AlertRule(Base):
    """Machine-readable alert trigger and response definition (v0.4.0)."""
    __tablename__ = "alert_rule"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rule_name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    slo_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("slo_definition.id")
    )
    trigger_condition: Mapped[str] = mapped_column(Text, nullable=False)
    trigger_window_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    notification_channels: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    immediate_response_steps: Mapped[str] = mapped_column(Text, nullable=False)
    auto_remediation_sql: Mapped[str | None] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(
        String(16), nullable=False,
        doc="critical | high | medium | low"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fire_count_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OncallPlaybook(Base):
    """Structured incident runbook linked to an alert_rule (v0.5.0)."""
    __tablename__ = "oncall_playbook"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    alert_rule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("alert_rule.id"), nullable=False, unique=True
    )
    playbook_name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    initial_response_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    resolution_target_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    steps: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    customer_notification_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    notification_template: Mapped[str | None] = mapped_column(Text)
    post_mortem_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "initial_response_minutes <= resolution_target_minutes",
            name="ck_oncall_playbook_response_time"
        ),
    )


class JobLease(Base):
    """Worker lease record with heartbeat for dead-worker detection (v0.6.0)."""
    __tablename__ = "job_lease"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_type: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True,
        doc="embedding_job | crawler_job | synthetic_check_result | trace_compliance_job | dual_write_fanout | embedding_migration_scheduler"
    )
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    worker_id: Mapped[str] = mapped_column(String(256), nullable=False)
    worker_region: Mapped[str | None] = mapped_column(String(64))
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_ttl_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    last_heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    heartbeat_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    heartbeat_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", index=True,
        doc="active | expired | released | stolen"
    )
    requeue_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_requeue_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "heartbeat_interval_seconds < lease_ttl_seconds / 2",
            name="ck_job_lease_heartbeat_interval"
        ),
        CheckConstraint("lease_ttl_seconds BETWEEN 30 AND 86400", name="ck_job_lease_ttl"),
    )
