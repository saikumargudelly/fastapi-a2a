"""
SQLAlchemy ORM models for the Dynamic Capability domain:
  - skill_query_log:    Append-only log of every QuerySkill RPC invocation
  - nlp_analyzer_config: Per-agent NLP-based offline skill match scorer config
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from fastapi_a2a.database import Base


class SkillQueryLog(Base):
    """Append-only log of every QuerySkill RPC call (Dynamic Capability domain)."""

    __tablename__ = "skill_query_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    skill_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_skill.id"), nullable=False, index=True
    )
    agent_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_card.id"), nullable=False, index=True,
        doc="Denormalised for per-agent query analytics"
    )
    caller_identity: Mapped[str | None] = mapped_column(String(512), index=True, doc="NULL for anonymous callers")
    can_handle_result: Mapped[bool] = mapped_column(Boolean, nullable=False, doc="Result returned to caller")
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False)
    match_score: Mapped[float | None] = mapped_column(Float, doc="NLP match score if free_text_intent was provided")
    input_sample_hash: Mapped[str | None] = mapped_column(
        String(64), doc="SHA-256 of input_sample JSON for dedup analysis; raw input never stored"
    )
    free_text_provided: Mapped[bool] = mapped_column(Boolean, nullable=False, doc="Whether caller provided free_text_intent")
    missing_fields_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    transformation_hints_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    schema_version_used: Mapped[int] = mapped_column(Integer, nullable=False, doc="skill_schema.version used to evaluate the query")
    queried_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )


class NlpAnalyzerConfig(Base):
    """Per-agent NLP-based offline skill match scorer configuration (Dynamic Capability domain)."""

    __tablename__ = "nlp_analyzer_config"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_card.id"), nullable=False, unique=True, index=True,
        doc="One NLP config per agent"
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, doc="Master switch; false = match_score=null in QuerySkill")
    model_ref: Mapped[str] = mapped_column(
        String(256), nullable=False,
        doc="Embedding model reference e.g. 'openai:text-embedding-3-small'; must match embedding_config dimensions"
    )
    similarity_threshold: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.75,
        doc="Minimum cosine similarity to return can_handle=true via NLP path"
    )
    skill_text_template: Mapped[str] = mapped_column(
        Text, nullable=False, default="{name}: {description}. Examples: {examples}",
        doc="Template for building per-skill reference text for embedding"
    )
    recompute_on_skill_change: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_recomputed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cache_ttl_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=3600)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("similarity_threshold BETWEEN 0.0 AND 1.0", name="ck_nlp_similarity_threshold"),
        CheckConstraint("cache_ttl_seconds BETWEEN 60 AND 86400", name="ck_nlp_cache_ttl"),
    )
