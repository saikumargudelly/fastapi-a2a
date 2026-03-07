"""
SQLAlchemy ORM models for Task Lifecycle domain:
  - task, message, message_part, artifact, session
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
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fastapi_a2a.database import Base


class TaskSession(Base):
    """Groups related tasks into a conversational context."""
    __tablename__ = "session"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_card.id"), nullable=False, index=True
    )
    caller_identity: Mapped[str | None] = mapped_column(String(512), index=True)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSONB, doc="Caller-supplied context e.g. user_id, locale, conversation_id"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now(),
        doc="Updated on every new task in session — used for session expiry"
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True,
        doc="Session auto-expires; tasks after expiry must start new session"
    )

    # Relationships
    tasks: Mapped[list[Task]] = relationship("Task", back_populates="session")

    __table_args__ = (
        CheckConstraint("expires_at IS NULL OR expires_at > created_at", name="ck_session_expiry"),
    )


class Task(Base):
    """Core task entity — the fundamental unit of work in A2A."""
    __tablename__ = "task"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("session.id"), index=True
    )
    agent_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_card.id"), nullable=False, index=True
    )
    skill_id: Mapped[str | None] = mapped_column(String(128), index=True)
    caller_identity: Mapped[str | None] = mapped_column(String(512))
    caller_agent_url: Mapped[str | None] = mapped_column(
        String(512), doc="URL of calling agent — enables agent-to-agent call graph"
    )
    auth_scheme_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)

    # Idempotency
    idempotency_key: Mapped[str | None] = mapped_column(
        String(256), unique=True, index=True,
        doc="Client-supplied key; prevents duplicate task creation"
    )

    # State machine
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="submitted", index=True,
        doc="submitted | working | input_required | artifact_updated | completed | failed | cancelled"
    )
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)

    # Push webhook
    push_webhook_url: Mapped[str | None] = mapped_column(
        String(512), doc="Client endpoint to POST task state-change notifications"
    )

    # TTL
    ttl_seconds: Mapped[int | None] = mapped_column(
        Integer, doc="Auto-cancel incomplete tasks after this duration; NULL = no auto-cancel"
    )

    # Consent
    consent_record_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    consent_revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    consent_revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Metadata
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB)
    data_region: Mapped[str | None] = mapped_column(String(64))

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    session: Mapped[TaskSession | None] = relationship("TaskSession", back_populates="tasks")
    messages: Mapped[list[Message]] = relationship("Message", back_populates="task")
    artifacts: Mapped[list[Artifact]] = relationship("Artifact", back_populates="task")

    __table_args__ = (
        CheckConstraint(
            "status IN ('submitted','working','input_required','artifact_updated','completed','failed','cancelled')",
            name="ck_task_status"
        ),
    )


class Message(Base):
    """A2A message — part of a task conversation thread."""
    __tablename__ = "message"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False, doc="user | agent")
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    # Relationships
    task: Mapped[Task] = relationship("Task", back_populates="messages")
    parts: Mapped[list[MessagePart]] = relationship("MessagePart", back_populates="message")

    __table_args__ = (
        CheckConstraint("role IN ('user','agent')", name="ck_message_role"),
        UniqueConstraint("task_id", "sequence_number", name="uq_message_sequence"),
    )


class MessagePart(Base):
    """Individual content part within a message (text, file, data)."""
    __tablename__ = "message_part"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("message.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    part_type: Mapped[str] = mapped_column(
        String(16), nullable=False, doc="text | file | data"
    )
    # text part
    content_text: Mapped[str | None] = mapped_column(
        Text, doc="Used when part_type=text. Store in object store if >1MB; keep only object_key ref here"
    )
    # data part
    content_data: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, doc="Structured JSON payload — used when part_type=data"
    )
    # file part
    file_name: Mapped[str | None] = mapped_column(String(256), doc="Original filename — used when part_type=file")
    file_mime_type: Mapped[str | None] = mapped_column(
        String(128), doc="MIME type of the file — required when part_type=file"
    )
    file_url: Mapped[str | None] = mapped_column(
        String(1024),
        doc="Pre-signed URL or inline data URI. Files >10MB must be object store URL — never store binary inline"
    )
    file_size_bytes: Mapped[int | None] = mapped_column(
        BigInteger, doc="File size; used to enforce object-store offload for files >10MB"
    )
    # shared
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB)
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, doc="Part ordering within message (0-based)"
    )

    # Relationships
    message: Mapped[Message] = relationship("Message", back_populates="parts")

    __table_args__ = (
        CheckConstraint("part_type IN ('text','file','data')", name="ck_message_part_type"),
        CheckConstraint(
            "part_type != 'text' OR content_text IS NOT NULL",
            name="ck_message_part_text_required"
        ),
        CheckConstraint(
            "part_type != 'file' OR file_mime_type IS NOT NULL",
            name="ck_message_part_file_mime_required"
        ),
        CheckConstraint(
            "part_type != 'data' OR content_data IS NOT NULL",
            name="ck_message_part_data_required"
        ),
        CheckConstraint(
            "file_size_bytes IS NULL OR file_size_bytes > 0",
            name="ck_message_part_file_size"
        ),
    )


class Artifact(Base):
    """Output produced by a task — primary delivery vehicle for agent results."""
    __tablename__ = "artifact"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    # A2A-stable client-facing ID within task scope
    artifact_id: Mapped[str] = mapped_column(
        String(128), nullable=False,
        doc="Stable A2A artifact ID within task scope — client-facing; short slug e.g. 'report-v1'"
    )
    name: Mapped[str | None] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text)
    artifact_type: Mapped[str] = mapped_column(
        String(128), nullable=False,
        doc="MIME type of the artifact e.g. text/plain, application/pdf"
    )

    # Content: A2A Part[] array (binary parts reference object store URLs)
    parts: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=list,
        doc="Array of Part objects matching A2A artifact schema. Binary content references object store URLs only"
    )

    # Streaming append chain
    index: Mapped[int] = mapped_column(
        Integer, nullable=False, doc="Artifact ordering within task (0-based)"
    )
    append_to_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifact.id"),
        doc="For streaming: this chunk appends to that artifact"
    )
    is_partial: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        doc="True = streaming chunk; False = complete artifact"
    )
    last_chunk: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        doc="True = this is the final chunk in the append chain"
    )

    # v0.6.0 consent revocation obfuscation
    obfuscation_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="none", index=True,
        doc="none | flagged_for_review | obfuscation_scheduled | obfuscated | deletion_scheduled | deleted"
    )
    obfuscation_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    task: Mapped[Task] = relationship("Task", back_populates="artifacts")

    __table_args__ = (
        UniqueConstraint("task_id", "artifact_id", name="uq_artifact_task_artifact_id"),
        CheckConstraint(
            "NOT (is_partial = false AND append_to_id IS NOT NULL AND index > 0)",
            name="ck_artifact_partial_chain"
        ),
        CheckConstraint(
            "NOT (last_chunk = true AND is_partial = false)",
            name="ck_artifact_last_chunk_must_be_partial"
        ),
    )
