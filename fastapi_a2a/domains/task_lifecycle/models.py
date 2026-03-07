"""
SQLAlchemy ORM models for Task Lifecycle domain:
  - task, message, message_part, artifact, session
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
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fastapi_a2a.database import Base


class TaskSession(Base):
    """Groups related tasks together."""
    __tablename__ = "session"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_card.id"), nullable=False, index=True
    )
    caller_identity: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    tasks: Mapped[list[Task]] = relationship("Task", back_populates="session")


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
    auth_scheme_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)

    # State machine
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="submitted", index=True,
        doc="submitted | working | input_required | completed | failed | cancelled"
    )
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)

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
            "status IN ('submitted','working','input_required','completed','failed','cancelled')",
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
    """Individual part within a message (text, file, data)."""
    __tablename__ = "message_part"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("message.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    part_type: Mapped[str] = mapped_column(
        String(16), nullable=False, doc="text | file | data"
    )
    content_text: Mapped[str | None] = mapped_column(Text)
    content_url: Mapped[str | None] = mapped_column(String(512))
    content_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    mime_type: Mapped[str | None] = mapped_column(String(128))
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB)
    part_index: Mapped[int] = mapped_column(Integer, nullable=False)

    # Relationships
    message: Mapped[Message] = relationship("Message", back_populates="parts")

    __table_args__ = (
        CheckConstraint("part_type IN ('text','file','data')", name="ck_message_part_type"),
    )


class Artifact(Base):
    """Output produced by a completed task."""
    __tablename__ = "artifact"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str | None] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text)
    mime_type: Mapped[str | None] = mapped_column(String(128))

    # Content (one of: inline text, URL, or structured data)
    content_text: Mapped[str | None] = mapped_column(Text)
    content_url: Mapped[str | None] = mapped_column(String(512))
    content_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

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
