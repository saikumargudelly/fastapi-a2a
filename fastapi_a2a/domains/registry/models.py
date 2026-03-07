"""
SQLAlchemy ORM models for Registry & Discovery domain:
  - registry_entry, heartbeat, agent_dependency
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from fastapi_a2a.database import Base


class RegistryEntry(Base):
    """An agent registered in the discovery index."""
    __tablename__ = "registry_entry"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_card.id"), nullable=False, unique=True, index=True
    )
    org_namespace: Mapped[str | None] = mapped_column(String(256), index=True)
    visibility: Mapped[str] = mapped_column(
        String(16), nullable=False, default="public",
        doc="public | private | partner"
    )
    approval_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active",
        doc="active | pending | suspended | rejected | warning"
    )
    primary_region: Mapped[str | None] = mapped_column(String(64))
    replica_regions: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)

    # Embedding
    current_embedding_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    embedding_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    # Import provenance (v0.5.0)
    import_source_type: Mapped[str | None] = mapped_column(
        String(32),
        doc="self_registered | federation_import | crawler_import | manual_bootstrap"
    )
    import_source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    import_permission_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    import_robots_txt_checked: Mapped[bool | None] = mapped_column(Boolean)
    import_user_agent: Mapped[str | None] = mapped_column(String(256))

    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("visibility IN ('public','private','partner')", name="ck_registry_visibility"),
    )


class Heartbeat(Base):
    """Agent liveness heartbeat check."""
    __tablename__ = "heartbeat"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_card.id"), nullable=False, unique=True, index=True
    )
    is_reachable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    check_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skill_statuses: Mapped[dict[str, str] | None] = mapped_column(JSONB, doc="skill_id → ok|degraded|down")
    last_http_status: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    region: Mapped[str | None] = mapped_column(
        String(16),
        doc="Region this heartbeat came from (ERD Gap 1 §16.1.3) — used to detect active region during failover"
    )
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AgentDependency(Base):
    """Records that one agent depends on another."""
    __tablename__ = "agent_dependency"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_card.id"), nullable=False, index=True
    )
    dependency_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_card.id"), nullable=False, index=True
    )
    dependency_type: Mapped[str] = mapped_column(String(32), nullable=False, default="runtime")
    is_critical: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    push_webhook_url: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("agent_card_id", "dependency_card_id", name="uq_agent_dependency"),
    )
