"""
SQLAlchemy ORM models for Consent & Governance domain:
  - consent_record, governance_policy, approval_workflow, workflow_step,
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
    UniqueConstraint,
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


class ApprovalWorkflow(Base):
    """Multi-step approval workflow for registry entry lifecycle (Consent & Governance domain)."""
    __tablename__ = "approval_workflow"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    registry_entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("registry_entry.id"), nullable=False, index=True,
        doc="FK to registry_entry — workflow governs this entry"
    )
    parent_workflow_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("approval_workflow.id"),
        doc="Re-review chains to parent workflow"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", index=True,
        doc="pending | in_review | approved | rejected | escalated | withdrawn"
    )
    current_step: Mapped[int] = mapped_column(Integer, nullable=False, default=0, doc="Current step index (0-based)")
    requested_by: Mapped[str] = mapped_column(String(256), nullable=False, doc="Identity requesting registry approval")
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[str | None] = mapped_column(String(256))
    notes: Mapped[str | None] = mapped_column(Text, doc="Reviewer notes on final decision")
    sla_deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True,
        doc="Overall SLA deadline; auto-escalate if not decided by then"
    )

    __table_args__ = (
        CheckConstraint("current_step >= 0", name="ck_approval_workflow_step_non_negative"),
        CheckConstraint(
            "status IN ('pending','in_review','approved','rejected','escalated','withdrawn')",
            name="ck_approval_workflow_status"
        ),
    )


class WorkflowStep(Base):
    """Ordered approval step within an approval_workflow (Consent & Governance domain)."""
    __tablename__ = "workflow_step"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("approval_workflow.id"), nullable=False, index=True,
        doc="FK to approval_workflow — step belongs to this workflow"
    )
    step_order: Mapped[int] = mapped_column(Integer, nullable=False, doc="Step sequence index 0-based")
    step_name: Mapped[str] = mapped_column(String(128), nullable=False, doc="Human-readable step label")
    approver_role: Mapped[str] = mapped_column(
        String(128), nullable=False,
        doc="Role name from role_assignment — all active holders may approve"
    )
    required_approvals: Mapped[int] = mapped_column(Integer, nullable=False, default=1, doc="Number of distinct approvers required")
    sla_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=86400, doc="SLA for this step; exceeded → auto-escalate")
    escalation_policy: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False,
        doc="{escalate_to_role, notify_email, auto_approve_on_no_response_seconds}"
    )
    auto_escalate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_parallel: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        doc="If true, runs concurrently with step_order N+1 instead of blocking it"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("workflow_id", "step_order", name="uq_workflow_step_order"),
        CheckConstraint("step_order >= 0", name="ck_workflow_step_order_non_negative"),
        CheckConstraint("required_approvals >= 1", name="ck_workflow_step_min_approvals"),
        CheckConstraint("sla_seconds > 0", name="ck_workflow_step_sla_positive"),
    )


class WorkflowAssignment(Base):
    """Individual reviewer assignment and decision for a workflow step (Consent & Governance domain)."""
    __tablename__ = "workflow_assignment"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    step_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_step.id"), nullable=False, index=True
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("approval_workflow.id"), nullable=False, index=True,
        doc="Denormalised for workflow-scoped queries"
    )
    reviewer_identity: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    assigned_by: Mapped[str] = mapped_column(String(256), nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    decision: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", index=True,
        doc="pending | approved | rejected | delegated | abstained"
    )
    decision_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text, doc="Required when decision=rejected")
    is_escalated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_delegation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("approver_delegation.id"),
        doc="If this assignment was created via delegation"
    )

    __table_args__ = (
        CheckConstraint(
            "decision IN ('pending','approved','rejected','delegated','abstained')",
            name="ck_workflow_assignment_decision"
        ),
    )


class ApproverDelegation(Base):
    """Constrained delegation of approval authority for a workflow step (Consent & Governance domain)."""
    __tablename__ = "approver_delegation"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    step_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_step.id"), nullable=False, index=True,
        doc="Delegation scoped to this step only"
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("approval_workflow.id"), nullable=False, index=True,
        doc="Denormalised for workflow queries"
    )
    delegator_identity: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    delegate_identity: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    delegated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        doc="NULL = valid until step decision; auto-expires after this timestamp"
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False, doc="Mandatory reason for audit trail")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        UniqueConstraint("step_id", "delegator_identity", name="uq_approver_delegation_active"),
        CheckConstraint(
            "delegator_identity != delegate_identity",
            name="ck_approver_delegation_no_self_delegation"
        ),
    )


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
