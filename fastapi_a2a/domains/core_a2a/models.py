"""
SQLAlchemy ORM models for Core A2A domain:
  - agent_card, agent_capabilities, agent_skill, skill_schema, card_history
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fastapi_a2a.database import Base


class AgentCard(Base):
    """Core discovery document served at /.well-known/agent.json."""
    __tablename__ = "agent_card"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    documentation_url: Mapped[str | None] = mapped_column(String(512))
    provider_org: Mapped[str | None] = mapped_column(String(256))
    provider_url: Mapped[str | None] = mapped_column(String(512))
    data_region: Mapped[str | None] = mapped_column(String(64))

    # Capability flags (stored in AgentCapabilities but denormalised for perf)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    approval_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active",
        doc="active | pending | suspended | rejected | warning"
    )

    # Card signing
    jws_signature: Mapped[str | None] = mapped_column(Text)
    hash_sha256: Mapped[str | None] = mapped_column(String(64), index=True)

    # v0.6.0 quarantine fields
    quarantine_status: Mapped[str] = mapped_column(String(16), nullable=False, default="none")
    quarantined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    quarantine_reason: Mapped[str | None] = mapped_column(Text)
    quarantine_auto_release_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    quarantine_operator: Mapped[str | None] = mapped_column(String(256))
    quarantine_suppress_federation: Mapped[bool | None] = mapped_column(Boolean)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    capabilities: Mapped[AgentCapabilities | None] = relationship(
        "AgentCapabilities", back_populates="agent_card", uselist=False
    )
    skills: Mapped[list[AgentSkill]] = relationship("AgentSkill", back_populates="agent_card")
    card_history: Mapped[list[CardHistory]] = relationship("CardHistory", back_populates="agent_card")

    __table_args__ = (
        CheckConstraint("quarantine_status IN ('none','quarantined','released')", name="ck_agent_card_quarantine_status"),
        CheckConstraint("approval_status IN ('active','pending','suspended','rejected','warning')", name="ck_agent_card_approval_status"),
    )


class AgentCapabilities(Base):
    """Capability flags for an agent card."""
    __tablename__ = "agent_capabilities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_card.id", ondelete="CASCADE"),
        nullable=False, unique=True
    )
    streaming: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    push_notifications: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    state_transition_history: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    default_input_modes: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    default_output_modes: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    supports_auth_schemes: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)

    # Relationships
    agent_card: Mapped[AgentCard] = relationship("AgentCard", back_populates="capabilities")


class SkillSchema(Base):
    """Typed input/output JSON schemas for agent skills."""
    __tablename__ = "skill_schema"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    schema_type: Mapped[str] = mapped_column(String(16), nullable=False, doc="input or output")
    json_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    pydantic_model_source: Mapped[str | None] = mapped_column(Text)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False, default="1.0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    input_skills: Mapped[list[AgentSkill]] = relationship(
        "AgentSkill", foreign_keys="AgentSkill.input_schema_id", back_populates="input_schema"
    )
    output_skills: Mapped[list[AgentSkill]] = relationship(
        "AgentSkill", foreign_keys="AgentSkill.output_schema_id", back_populates="output_schema"
    )

    __table_args__ = (
        CheckConstraint("schema_type IN ('input','output')", name="ck_skill_schema_type"),
    )


class AgentSkill(Base):
    """Individual capability exposed by an agent."""
    __tablename__ = "agent_skill"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_card.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    skill_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    examples: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    input_modes: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    output_modes: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)

    # Typed schema links
    input_schema_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("skill_schema.id"), index=True
    )
    output_schema_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("skill_schema.id"), index=True
    )

    # Embedding link (set by embedding pipeline)
    current_embedding_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    agent_card: Mapped[AgentCard] = relationship("AgentCard", back_populates="skills")
    input_schema: Mapped[SkillSchema | None] = relationship(
        "SkillSchema", foreign_keys=[input_schema_id], back_populates="input_skills"
    )
    output_schema: Mapped[SkillSchema | None] = relationship(
        "SkillSchema", foreign_keys=[output_schema_id], back_populates="output_skills"
    )

    __table_args__ = (
        UniqueConstraint("agent_card_id", "skill_id", name="uq_agent_skill_card_skill"),
    )


class CardHistory(Base):
    """Append-only version history of agent_card changes."""
    __tablename__ = "card_history"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_card.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, doc="Full card JSON at this version")
    hash_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    changed_by: Mapped[str | None] = mapped_column(String(256))
    change_reason: Mapped[str | None] = mapped_column(Text)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    # Relationships
    agent_card: Mapped[AgentCard] = relationship("AgentCard", back_populates="card_history")

    __table_args__ = (
        UniqueConstraint("agent_card_id", "version_number", name="uq_card_history_version"),
    )
