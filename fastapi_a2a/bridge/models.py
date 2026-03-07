"""
SQLAlchemy ORM models for the FastAPI Bridge domain:
  - route_mapping:           FastAPI route → A2A skill binding
  - fastapi_a2a_config_row:  DB-persisted plugin runtime configuration
  - startup_audit_log:       Immutable lifecycle event log
  - sdk_compatibility_matrix: SDK ↔ card schema version gates
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


class RouteMapping(Base):
    """Maps a discovered FastAPI route to an A2A skill (FastAPI Bridge domain)."""

    __tablename__ = "route_mapping"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_card.id"), nullable=False, index=True
    )
    http_method: Mapped[str] = mapped_column(String(16), nullable=False, doc="GET | POST | PUT | PATCH | DELETE")
    path: Mapped[str] = mapped_column(String(512), nullable=False, doc="FastAPI route path e.g. '/api/v1/analyze'")
    operation_id: Mapped[str | None] = mapped_column(String(256), doc="OpenAPI operationId if declared")
    summary: Mapped[str | None] = mapped_column(Text, doc="OpenAPI summary — used as skill description if not overridden")
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(String), doc="OpenAPI tags propagated to skill.tags")
    is_included: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, doc="False = excluded from A2A surface")
    exclude_reason: Mapped[str | None] = mapped_column(Text, doc="Why this route was excluded")
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("agent_card_id", "http_method", "path", name="uq_route_mapping_agent_method_path"),
    )


class FastApiA2AConfigRow(Base):
    """DB-persisted runtime configuration for the FastApiA2A plugin instance (1:1 with agent_card)."""

    __tablename__ = "fastapi_a2a_config_row"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_card.id"), nullable=False, unique=True, index=True,
        doc="FK to agent_card — exactly one config row per agent"
    )
    rpc_path: Mapped[str] = mapped_column(String(256), nullable=False, default="/", doc="JSON-RPC 2.0 endpoint path")
    well_known_path: Mapped[str] = mapped_column(
        String(256), nullable=False, default="/.well-known/agent.json", doc="Agent card endpoint path"
    )
    extended_card_path: Mapped[str | None] = mapped_column(String(256), doc="Auth-gated extended card — NULL = disabled")
    registry_url: Mapped[str | None] = mapped_column(String(512), doc="Discovery registry to self-register with; NULL = no registration")
    heartbeat_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    require_signed_cards: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    auto_discover_routes: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    include_patterns: Mapped[list[str] | None] = mapped_column(ARRAY(String), doc="Route glob patterns to include")
    exclude_patterns: Mapped[list[str] | None] = mapped_column(ARRAY(String), doc="Route glob patterns to exclude")
    enable_tracing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    enable_rate_limiting: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    enable_consent_check: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    jwks_endpoint: Mapped[str | None] = mapped_column(String(512), doc="Override URL for /.well-known/agent-jwks.json")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("heartbeat_interval_seconds > 0", name="ck_config_row_heartbeat_positive"),
    )


class StartupAuditLog(Base):
    """Append-only immutable log of every library lifecycle event (FastAPI Bridge domain)."""

    __tablename__ = "startup_audit_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_card.id"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True,
        doc=(
            "startup | shutdown | config_changed | routes_discovered | "
            "registration_succeeded | registration_failed | heartbeat_failed | "
            "incident_response | bootstrap_seed"
        ),
    )
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB, doc="Event-specific payload")
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    library_version: Mapped[str] = mapped_column(String(32), nullable=False, doc="fastapi-a2a version at event time")
    host: Mapped[str | None] = mapped_column(String(256), doc="Hostname or pod name for containerised deployments")


class SdkCompatibilityMatrix(Base):
    """SDK version ↔ card schema version compatibility gates (FastAPI Bridge domain)."""

    __tablename__ = "sdk_compatibility_matrix"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sdk_version: Mapped[str] = mapped_column(String(32), nullable=False, index=True, doc="fastapi-a2a client SDK version e.g. '1.2.0'")
    min_schema_version: Mapped[int] = mapped_column(Integer, nullable=False, doc="Minimum agent_card.schema_version this SDK can interact with")
    max_schema_version: Mapped[int | None] = mapped_column(Integer, doc="Maximum schema_version; NULL = supports all future versions")
    compatibility_level: Mapped[str] = mapped_column(
        String(16), nullable=False,
        doc="full | partial | deprecated | incompatible"
    )
    upgrade_guidance: Mapped[str | None] = mapped_column(Text, doc="Human-readable upgrade instruction returned to callers on deprecated/incompatible match")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("sdk_version", "min_schema_version", name="uq_sdk_compat_version_schema"),
        CheckConstraint(
            "compatibility_level IN ('full','partial','deprecated','incompatible')",
            name="ck_sdk_compat_level"
        ),
    )
