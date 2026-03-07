"""
SQLAlchemy ORM models for Federation & Crawler domain:
  - federation_peer, crawler_job, crawler_source,
    crawler_import_permission, takedown_request,
    crawler_ownership_proof, crawler_takedown_request
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
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from fastapi_a2a.database import Base


class FederationPeer(Base):
    """Cross-registry federation peer configuration."""
    __tablename__ = "federation_peer"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    peer_url: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    display_name: Mapped[str | None] = mapped_column(String(256))
    auth_type: Mapped[str] = mapped_column(String(32), nullable=False, default="bearer")
    auth_config: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    sync_policy: Mapped[str] = mapped_column(
        String(8), nullable=False, default="pull",
        doc="pull | push | bidirectional"
    )
    trust_level: Mapped[str] = mapped_column(
        String(8), nullable=False, default="partial",
        doc="none | partial | full"
    )
    push_inbound_endpoint: Mapped[str | None] = mapped_column(String(512))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CrawlerSource(Base):
    """Strategy for discovering agent cards (GitHub, HTTP, DNS)."""
    __tablename__ = "crawler_source"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_type: Mapped[str] = mapped_column(
        String(32), nullable=False,
        doc="github_code_search | http_directory | dns_enumeration | manual"
    )
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    crawl_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=3600)
    user_agent: Mapped[str | None] = mapped_column(String(256))
    robots_txt_respect: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    ethical_approval_note: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_crawled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CrawlerJob(Base):
    """Individual crawl execution run tracking."""
    __tablename__ = "crawler_job"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crawler_source.id"), index=True
    )
    federation_peer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("federation_peer.id"), index=True
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="running", index=True,
        doc="running | completed | failed | cancelled"
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    discovered_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    imported_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_log: Mapped[list[str] | None] = mapped_column(ARRAY(Text))


class CrawlerImportPermission(Base):
    """Governs whether a discovered agent card can be imported."""
    __tablename__ = "crawler_import_permission"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    match_type: Mapped[str] = mapped_column(
        String(16), nullable=False,
        doc="domain | url_prefix | exact | org"
    )
    match_value: Mapped[str] = mapped_column(String(512), nullable=False)
    effect: Mapped[str] = mapped_column(String(8), nullable=False, doc="allow | deny")
    reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(256))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TakedownRequest(Base):
    """Request to remove an agent card from the registry."""
    __tablename__ = "takedown_request"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_card_url: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    requester_identity: Mapped[str] = mapped_column(String(512), nullable=False)
    reason_type: Mapped[str] = mapped_column(
        String(16), nullable=False,
        doc="opt_out | legal | safety | duplicate | other"
    )
    reason_details: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", index=True,
        doc="pending | actioned | rejected"
    )
    sla_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actioned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actioned_by: Mapped[str | None] = mapped_column(String(256))
    registry_entry_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CrawlerOwnershipProof(Base):
    """Proof-of-ownership evidence authorizing crawler import (v0.5.0)."""
    __tablename__ = "crawler_ownership_proof"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_card_url: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    domain: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    proof_method: Mapped[str] = mapped_column(
        String(32), nullable=False,
        doc="dns_txt | signed_token | admin_email | federation_delegation"
    )
    challenge_token: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", index=True,
        doc="pending | verified | failed | expired | revoked"
    )
    challenge_issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    challenge_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification_evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    verifier_identity: Mapped[str | None] = mapped_column(String(256))
    registry_entry_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    proof_scope: Mapped[str] = mapped_column(
        String(16), nullable=False,
        doc="single_url | domain_prefix | org_wide"
    )
    revoke_reason: Mapped[str | None] = mapped_column(Text)

    # v0.6.0 opt-out UX fields
    removal_link_url: Mapped[str | None] = mapped_column(String(512))
    robot_readme_url: Mapped[str | None] = mapped_column(String(512))
    opt_out_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    opt_out_signal_found: Mapped[bool | None] = mapped_column(Boolean)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CrawlerTakedownRequest(Base):
    """Crawler-specific card removal with opt-out and re-crawl suppression (v0.5.0)."""
    __tablename__ = "crawler_takedown_request"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_card_url: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    requester_identity: Mapped[str] = mapped_column(String(512), nullable=False)
    requester_proof: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    reason_type: Mapped[str] = mapped_column(
        String(16), nullable=False,
        doc="opt_out | legal | safety | impersonation | duplicate"
    )
    reason_details: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", index=True,
        doc="pending | verified | actioned | rejected"
    )
    suppress_re_crawl: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    suppress_federation_sync: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    flag_as_opted_out: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sla_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actioned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actioned_by: Mapped[str | None] = mapped_column(String(256))
    registry_entry_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    parent_takedown_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
