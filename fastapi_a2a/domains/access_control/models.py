"""
SQLAlchemy ORM models for Access Control domain:
  - access_policy, role_assignment, acl_entry,
    policy_cache, policy_cache_invalidation_event
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


class AccessPolicy(Base):
    """RBAC access policy per agent/skill/caller."""
    __tablename__ = "access_policy"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_card.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    # Principal (one of these will be set)
    caller_identity: Mapped[str | None] = mapped_column(String(512), index=True)
    caller_role: Mapped[str | None] = mapped_column(String(128), index=True)
    caller_org: Mapped[str | None] = mapped_column(String(256))

    # v0.6.0 policy evaluation fields
    principal_type: Mapped[str] = mapped_column(
        String(16), nullable=False,
        doc="identity | role | org | wildcard"
    )
    specificity_rank: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True,
        doc="1=most specific, 8=least. Computed from principal_type + skill scope"
    )

    # Scope
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    skill_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_skill.id"), index=True
    )

    # Decision
    effect: Mapped[str] = mapped_column(String(8), nullable=False, doc="allow | deny")
    condition_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("effect IN ('allow','deny')", name="ck_access_policy_effect"),
        CheckConstraint("principal_type IN ('identity','role','org','wildcard')", name="ck_access_policy_principal"),
        CheckConstraint("specificity_rank BETWEEN 1 AND 8", name="ck_access_policy_rank"),
    )


class RoleAssignment(Base):
    """Assigns a role to a caller for a specific agent."""
    __tablename__ = "role_assignment"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_card.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    caller_identity: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(128), nullable=False)
    granted_by: Mapped[str | None] = mapped_column(String(256))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("agent_card_id", "caller_identity", "role", name="uq_role_assignment"),
    )


class AclEntry(Base):
    """Skill-level ACL entry for fine-grained access control."""
    __tablename__ = "acl_entry"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_card.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_skill.id"), nullable=False, index=True
    )
    caller_identity: Mapped[str] = mapped_column(String(512), nullable=False)
    effect: Mapped[str] = mapped_column(String(8), nullable=False, doc="allow | deny")
    reason: Mapped[str | None] = mapped_column(Text)
    granted_by: Mapped[str | None] = mapped_column(String(256))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PolicyCache(Base):
    """LRU policy evaluation cache entry with TTL and invalidation tracking (v0.5.0)."""
    __tablename__ = "policy_cache"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cache_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True, index=True)
    agent_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_card.id"), nullable=False, index=True
    )
    caller_identity: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    auth_scheme_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    skill_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    decision: Mapped[str] = mapped_column(String(8), nullable=False, doc="allow | deny")
    decision_basis: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)
    cached_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_hit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_invalidated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invalidated_by_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class PolicyCacheInvalidationEvent(Base):
    """Pub/sub invalidation event — DB-polling or Redis relay (v0.5.0)."""
    __tablename__ = "policy_cache_invalidation_event"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(
        String(32), nullable=False,
        doc="policy_changed | acl_changed | role_changed | bulk_invalidate"
    )
    affected_agent_card_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_card.id"), index=True
    )
    affected_caller_identity: Mapped[str | None] = mapped_column(String(512), index=True)
    affected_policy_ids: Mapped[list[uuid.UUID] | None] = mapped_column(ARRAY(UUID(as_uuid=True)))
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    consumed_by_instances: Mapped[list[str] | None] = mapped_column(ARRAY(String))


class PolicyEvaluationLog(Base):
    """Append-only log of every policy evaluation decision (v0.6.0)."""
    __tablename__ = "policy_evaluation_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_card.id"), nullable=False, index=True
    )
    caller_identity: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    skill_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    auth_scheme_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    decision: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    decision_source: Mapped[str] = mapped_column(
        String(32), nullable=False,
        doc="policy_cache_hit | full_evaluation | default_deny"
    )
    matched_policy_ids: Mapped[list[uuid.UUID] | None] = mapped_column(ARRAY(UUID(as_uuid=True)))
    winning_specificity_rank: Mapped[int | None] = mapped_column(Integer)
    candidate_count: Mapped[int | None] = mapped_column(Integer)
    evaluation_duration_us: Mapped[int | None] = mapped_column(Integer)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
