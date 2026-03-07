"""
SQLAlchemy ORM models for Safety & Reputation domain:
  - card_scan_result, sanitization_report,
    synthetic_check, synthetic_check_result,
    agent_reputation

Note: skill_query_log and nlp_analyzer_config belong to the
Dynamic Capability domain and live in
``fastapi_a2a.domains.dynamic_capability.models``.
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

# ── Safety & Reputation ────────────────────────────────────────────────────────

class CardScanResult(Base):
    """Static prompt-injection and content safety scan result per card version."""
    __tablename__ = "card_scan_result"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_card.id"), nullable=False, index=True
    )
    card_hash_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    scan_status: Mapped[str] = mapped_column(
        String(16), nullable=False, index=True,
        doc="queued | running | passed | flagged | failed"
    )
    scan_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    injection_patterns_found: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    fields_scanned: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)
    scan_engine_version: Mapped[str] = mapped_column(String(64), nullable=False)
    scan_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    requires_human_review: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(256))
    review_decision: Mapped[str | None] = mapped_column(String(16), doc="approved | rejected")
    review_notes: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fix_suggestions: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    scanned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )

    __table_args__ = (
        UniqueConstraint("agent_card_id", "card_hash_sha256", name="uq_card_scan_result"),
        CheckConstraint("scan_score BETWEEN 0.0 AND 1.0", name="ck_card_scan_score"),
    )


class SanitizationReport(Base):
    """Runtime sanitization result for a card version (v0.5.0)."""
    __tablename__ = "sanitization_report"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_card.id"), nullable=False, index=True
    )
    card_hash_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    trigger_surface: Mapped[str] = mapped_column(
        String(32), nullable=False,
        doc="card_serve | crawler_ingest | federation_sync | llm_prompt | extended_card | card_history | prompt_assembly | audit_summary | operator_quarantine"
    )
    aggregate_score: Mapped[float] = mapped_column(Float, nullable=False)
    fields_sanitized: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)
    field_results: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    rules_engine_version: Mapped[str] = mapped_column(String(64), nullable=False)
    total_redactions: Mapped[int] = mapped_column(Integer, nullable=False)
    approval_action_taken: Mapped[bool] = mapped_column(Boolean, nullable=False)
    sanitized_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )

    __table_args__ = (
        CheckConstraint("aggregate_score BETWEEN 0.0 AND 1.0", name="ck_sanitization_score"),
    )


class SyntheticCheck(Base):
    """Repeatable synthetic health check definition for a skill."""
    __tablename__ = "synthetic_check"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_card.id"), nullable=False, index=True
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_skill.id"), nullable=False, index=True
    )
    check_name: Mapped[str] = mapped_column(String(256), nullable=False)
    check_type: Mapped[str] = mapped_column(
        String(32), nullable=False,
        doc="schema_validate | response_present | response_contains | response_json_path | latency_sla | custom_script"
    )
    input_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    expected_value: Mapped[str | None] = mapped_column(Text)
    check_script: Mapped[str | None] = mapped_column(Text)
    schedule_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    max_latency_ms: Mapped[int | None] = mapped_column(Integer, default=5000)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str] = mapped_column(String(8), nullable=False, default="never")
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # v0.5.0 richer harness fields
    test_type: Mapped[str] = mapped_column(String(16), nullable=False, default="functional")
    input_template: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    template_vars: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    expected_output_pattern: Mapped[str | None] = mapped_column(Text)
    auth_mechanism: Mapped[str] = mapped_column(String(16), nullable=False, default="none")
    auth_token_ref: Mapped[str | None] = mapped_column(String(256))
    auth_config: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    auth_cert_ref: Mapped[str | None] = mapped_column(String(256))
    max_runtime_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=10000)
    retries: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    retry_delay_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=1000)
    smoke_concurrency: Mapped[int | None] = mapped_column(Integer)
    dependency_skill_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("agent_card_id", "skill_id", "check_name", name="uq_synthetic_check"),
        CheckConstraint("schedule_interval_seconds >= 60", name="ck_synthetic_check_interval"),
    )


class SyntheticCheckResult(Base):
    """Append-only result record for each synthetic check execution."""
    __tablename__ = "synthetic_check_result"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    check_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("synthetic_check.id"), nullable=False, index=True
    )
    agent_card_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    skill_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    pass_: Mapped[bool] = mapped_column("pass", Boolean, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    artifact_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ran_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )

    # v0.5.0 richer result fields
    failure_classification: Mapped[str | None] = mapped_column(String(32))
    repro_steps: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    attempts_made: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    retry_history: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    flap_detected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class AgentReputation(Base):
    """Computed trust and reliability score per agent (v0.4.0)."""
    __tablename__ = "agent_reputation"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_card.id"), nullable=False, unique=True
    )
    overall_score: Mapped[float] = mapped_column(Float, nullable=False)
    uptime_score: Mapped[float] = mapped_column(Float, nullable=False)
    synthetic_check_score: Mapped[float] = mapped_column(Float, nullable=False)
    security_score: Mapped[float] = mapped_column(Float, nullable=False)
    task_success_score: Mapped[float] = mapped_column(Float, nullable=False)
    community_review_score: Mapped[float | None] = mapped_column(Float)
    review_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    score_trend: Mapped[str] = mapped_column(
        String(16), nullable=False, default="stable",
        doc="improving | stable | declining"
    )
    flags: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    discovery_rank: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("overall_score BETWEEN 0.0 AND 1.0", name="ck_agent_reputation_overall"),
    )


