"""
SQLAlchemy ORM models for Embedding Pipeline domain:
  - embedding_config, embedding_job, embedding_version,
    schema_version, embedding_migration_plan
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


class EmbeddingConfig(Base):
    """Configuration for an embedding model used for semantic search."""
    __tablename__ = "embedding_config"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    external_collection: Mapped[str | None] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="inactive",
        doc="inactive | seeding | active | deprecated | archived"
    )
    vector_db_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pgvector",
        doc="pgvector | weaviate | pinecone | faiss | qdrant | custom"
    )
    api_key_ref: Mapped[str | None] = mapped_column(String(256), doc="KMS ref for model API key")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EmbeddingJob(Base):
    """Async job for generating/regenerating embeddings."""
    __tablename__ = "embedding_job"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    registry_entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("registry_entry.id"), nullable=False, index=True
    )
    embedding_config_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("embedding_config.id"), nullable=False, index=True
    )
    job_type: Mapped[str] = mapped_column(
        String(16), nullable=False,
        doc="generate | regenerate | delete"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="queued", index=True,
        doc="queued | running | completed | failed"
    )
    priority: Mapped[str] = mapped_column(String(8), nullable=False, default="normal")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    error_message: Mapped[str | None] = mapped_column(Text)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EmbeddingVersion(Base):
    """Versioned vector embedding for a registry entry."""
    __tablename__ = "embedding_version"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    registry_entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("registry_entry.id"), nullable=False, index=True
    )
    embedding_config_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("embedding_config.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    # v0.6.0 external vector DB fields
    external_vector_db: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pgvector"
    )
    external_vector_id: Mapped[str | None] = mapped_column(String(512), index=True)
    external_collection_name: Mapped[str | None] = mapped_column(String(256))
    vector_stored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    vector_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    __table_args__ = (
        CheckConstraint(
            "external_vector_db = 'pgvector' OR (external_vector_id IS NOT NULL AND external_collection_name IS NOT NULL)",
            name="ck_embedding_version_external"
        ),
    )


class SchemaVersion(Base):
    """Tracks schema versions for SDK compatibility."""
    __tablename__ = "schema_version"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    component: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    deployed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    release_notes: Mapped[str | None] = mapped_column(Text)


class EmbeddingMigrationPlan(Base):
    """Control plane for embedding model/dimension migration (v0.5.0 + v0.6.0 extensions)."""
    __tablename__ = "embedding_migration_plan"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    old_embedding_config_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("embedding_config.id"), nullable=False, index=True
    )
    new_embedding_config_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("embedding_config.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="created", index=True,
        doc="created | seeding | cutover_ready | cutover_in_progress | validating | completed | rolled_back | failed"
    )
    total_registry_entries: Mapped[int] = mapped_column(Integer, nullable=False)
    seeded_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cutover_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rate_limit_jobs_per_minute: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    max_concurrent_jobs: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    cutover_gate_percent: Mapped[float] = mapped_column(Float, nullable=False, default=0.99)
    max_failure_rate_threshold: Mapped[float] = mapped_column(Float, nullable=False, default=0.05)
    ab_test_sample_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.05)
    ab_test_duration_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=72)
    ab_test_quality_threshold: Mapped[float] = mapped_column(Float, nullable=False, default=0.8)

    # v0.6.0 external vector DB + backpressure fields
    target_vector_db: Mapped[str] = mapped_column(String(16), nullable=False, default="pgvector")
    target_collection_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    backpressure_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    backpressure_queue_depth_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=5000)
    backpressure_retry_after_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    atomic_cutover_batch_size: Mapped[int] = mapped_column(Integer, nullable=False, default=500)
    atomic_cutover_sleep_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    old_vectors_retain_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rollback_window_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=72)
    cross_backend_transfer: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    validation_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validation_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cutover_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cutover_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rollback_reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("old_embedding_config_id != new_embedding_config_id", name="ck_embedding_migration_diff_configs"),
        CheckConstraint("cutover_gate_percent BETWEEN 0.5 AND 1.0", name="ck_embedding_migration_gate"),
    )
