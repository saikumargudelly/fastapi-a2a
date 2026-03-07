"""
SQLAlchemy ORM models for Consent & Governance domain:
  - consent_record, governance_policy, workflow_step,
    workflow_assignment, approver_delegation, consent_revocation_action
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


class ConsentRecord(Base):
    """Data-use consent record from a data subject."""
    __tablename__ = "consent_record"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_card.id"), nullable=False, index=True
    )
    data_subject_identity: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    caller_identity: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    data_categories: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)
    purpose: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    withdrawal_reason: Mapped[str | None] = mapped_column(Text)
    data_region: Mapped[str | None] = mapped_column(String(64))
    governance_policy_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GovernancePolicy(Base):
    """Organizational governance policy for data handling."""
    __tablename__ = "governance_policy"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_namespace: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    policy_name: Mapped[str] = mapped_column(String(128), nullable=False)
    allowed_regions: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    prohibited_data_categories: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    max_retention_days: Mapped[int | None] = mapped_column(Integer)
    requires_explicit_consent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    policy_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WorkflowStep(Base):
    """Multi-step approval workflow step definition."""
    __tablename__ = "workflow_step"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_card.id"), nullable=False, index=True
    )
    workflow_name: Mapped[str] = mapped_column(String(128), nullable=False)
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    step_type: Mapped[str] = mapped_column(String(32), nullable=False)
    required_role: Mapped[str | None] = mapped_column(String(128))
    sla_hours: Mapped[int | None] = mapped_column(Integer)
    auto_approve: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class WorkflowAssignment(Base):
    """Assignment of a workflow instance to approvers."""
    __tablename__ = "workflow_assignment"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_step_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_step.id"), nullable=False, index=True
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    assignee_identity: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending",
        doc="pending | approved | rejected | delegated | expired"
    )
    decision: Mapped[str | None] = mapped_column(String(16))
    decision_notes: Mapped[str | None] = mapped_column(Text)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sla_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApproverDelegation(Base):
    """Delegation of approval authority to another identity."""
    __tablename__ = "approver_delegation"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    delegator_identity: Mapped[str] = mapped_column(String(512), nullable=False)
    delegate_identity: Mapped[str] = mapped_column(String(512), nullable=False)
    workflow_step_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    reason: Mapped[str | None] = mapped_column(Text)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConsentRevocationAction(Base):
    """Audit record of in-flight task side-effect actions on consent withdrawal (v0.6.0)."""
    __tablename__ = "consent_revocation_action"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    consent_record_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    agent_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_card.id"), nullable=False, index=True
    )
    action_type: Mapped[str] = mapped_column(
        String(32), nullable=False,
        doc="task_cancelled | task_paused | artifact_flagged | artifact_obfuscated | artifact_deleted | task_allowed_emergency | proof_tokens_revoked"
    )
    action_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", index=True,
        doc="pending | completed | failed | skipped"
    )
    action_reason: Mapped[str | None] = mapped_column(Text)
    task_abort_point: Mapped[str | None] = mapped_column(String(256))
    artifact_ids_affected: Mapped[list[uuid.UUID] | None] = mapped_column(ARRAY(UUID(as_uuid=True)))
    authorized_by: Mapped[str | None] = mapped_column(String(256))
    emergency_reason: Mapped[str | None] = mapped_column(Text)
    legal_basis: Mapped[str | None] = mapped_column(String(128))
    performed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )

    __table_args__ = (
        CheckConstraint(
            "(action_type != 'task_allowed_emergency') OR (authorized_by IS NOT NULL AND emergency_reason IS NOT NULL AND legal_basis IS NOT NULL)",
            name="ck_consent_revocation_emergency"
        ),
    )
