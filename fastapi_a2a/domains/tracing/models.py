"""
SQLAlchemy ORM models for Tracing domain:
  - trace_span, trace_context
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from fastapi_a2a.database import Base


class TraceSpan(Base):
    """OpenTelemetry span record per task execution step."""
    __tablename__ = "trace_span"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_card.id"), nullable=False, index=True
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    span_id: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    parent_span_id: Mapped[str | None] = mapped_column(String(16))
    operation_name: Mapped[str] = mapped_column(String(256), nullable=False)
    service_name: Mapped[str] = mapped_column(String(128), nullable=False)
    attributes: Mapped[dict[str, Any] | None] = mapped_column(JSONB, doc="Sanitized span attributes")
    status_code: Mapped[str | None] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    exported: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    exported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TraceContext(Base):
    """W3C trace context propagated per task."""
    __tablename__ = "trace_context"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, unique=True, index=True)
    traceparent: Mapped[str] = mapped_column(String(64), nullable=False)
    tracestate: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
