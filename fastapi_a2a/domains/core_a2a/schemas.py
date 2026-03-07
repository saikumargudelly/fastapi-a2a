"""
Pydantic v2 schemas for Core A2A domain objects.
These are the wire-format models for the A2A Protocol endpoints.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# ── Core A2A Protocol Schemas ──────────────────────────────────────────────────

class SkillSchemaOut(BaseModel):
    schema_type: Literal["input", "output"]
    json_schema: dict[str, Any]
    schema_version: str = "1.0"


class AgentSkillOut(BaseModel):
    id: uuid.UUID
    skill_id: str
    name: str
    description: str
    tags: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    input_modes: list[str] = Field(default_factory=list)
    output_modes: list[str] = Field(default_factory=list)
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None


class AgentCapabilitiesOut(BaseModel):
    streaming: bool = False
    push_notifications: bool = False
    state_transition_history: bool = False
    default_input_modes: list[str] = Field(default_factory=list)
    default_output_modes: list[str] = Field(default_factory=list)
    supports_auth_schemes: list[str] = Field(default_factory=list)


class AgentCardOut(BaseModel):
    """Wire format for /.well-known/agent.json"""
    name: str
    description: str
    url: str
    version: str
    documentation_url: str | None = None
    provider: dict[str, str | None] | None = None
    capabilities: AgentCapabilitiesOut | None = None
    skills: list[AgentSkillOut] = Field(default_factory=list)
    default_input_modes: list[str] = Field(default_factory=list)
    default_output_modes: list[str] = Field(default_factory=list)

    # A2A Protocol required fields
    protocol_version: str = "1.0"


# Match aliases for the spec naming convention
AgentCard = AgentCardOut
AgentSkill = AgentSkillOut
AgentCapabilities = AgentCapabilitiesOut
SkillSchema = SkillSchemaOut


# ── Task Lifecycle Schemas ──────────────────────────────────────────────────────

class MessagePartIn(BaseModel):
    type: Literal["text", "file", "data"]
    text: str | None = None
    url: str | None = None
    data: dict[str, Any] | None = None
    mime_type: str | None = None
    metadata: dict[str, Any] | None = None


class MessageIn(BaseModel):
    role: Literal["user", "agent"]
    parts: list[MessagePartIn]
    metadata: dict[str, Any] | None = None


class TaskSendParams(BaseModel):
    """Parameters for tasks/send JSON-RPC method."""
    id: str | None = None  # Client-provided idempotency ID
    session_id: str | None = None
    skill_id: str | None = None
    message: MessageIn
    metadata: dict[str, Any] | None = None


class TaskOut(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID | None = None
    status: str
    error_code: str | None = None
    error_message: str | None = None
    messages: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] | None = None


class ArtifactOut(BaseModel):
    id: uuid.UUID
    artifact_type: str
    name: str | None = None
    description: str | None = None
    mime_type: str | None = None
    content_text: str | None = None
    content_url: str | None = None
    content_data: dict[str, Any] | None = None
    obfuscation_status: str = "none"
    created_at: datetime


# ── JSON-RPC Envelope ─────────────────────────────────────────────────────────

class JsonRpcRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: str | int | None = None
    method: str
    params: dict[str, Any] | None = None


class JsonRpcError(BaseModel):
    code: int
    message: str
    data: Any = None


class JsonRpcResponse(BaseModel):
    jsonrpc: str = "2.0"
    id: str | int | None = None
    result: Any = None
    error: JsonRpcError | None = None


# ── Registry Schemas ───────────────────────────────────────────────────────────

class RegistryAgentListOut(BaseModel):
    agents: list[dict[str, Any]]
    total: int
    page: int = 1
    page_size: int = 20


class RegisterRequest(BaseModel):
    card_url: str
    org_namespace: str | None = None
    visibility: Literal["public", "private", "partner"] = "public"
    region: str | None = None


class RegisterResponse(BaseModel):
    registry_entry_id: uuid.UUID
    agent_card_id: uuid.UUID
    status: str
    message: str


# ── JWKS Schemas ───────────────────────────────────────────────────────────────

class JwksKey(BaseModel):
    kid: str
    kty: str
    crv: str | None = None
    use: str = "sig"
    alg: str
    x: str | None = None
    y: str | None = None
    n: str | None = None
    e: str | None = None
    status: Literal["active", "retired"] = "active"
    published_at: datetime
    expires_at: datetime | None = None
    grace_expires_at: datetime | None = None
    rotation_successor_kid: str | None = None


class JwksResponse(BaseModel):
    keys: list[JwksKey]
    crl_url: str
    jwks_version: datetime
    next_poll_after: datetime


# ── Sanitizer Schemas ──────────────────────────────────────────────────────────

class SanitizationReportOut(BaseModel):
    """Returned when a card is sanitized before serving."""
    agent_card_id: uuid.UUID
    aggregate_score: float
    fields_sanitized: list[str]
    total_redactions: int
    approval_action_taken: bool
    sanitized_at: datetime
