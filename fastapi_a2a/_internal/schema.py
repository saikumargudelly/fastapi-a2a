"""
A2A protocol schema — TypedDict models + Pydantic TypeAdapters.

Design rules:
- All models are TypedDict. No Pydantic BaseModel.
- Fields use camelCase matching wire format — no alias transformer needed.
- NotRequired[X] for optional fields (Python >=3.11 native).
- TypeAdapter instances at module level — built once, reused everywhere.
- Part discriminated union on 'kind' field.
- FIX A4: FileWithBytes.content (was bytes — shadowed the built-in).
- FIX E1: SecurityScheme and OAuthFlows are now fully typed TypedDicts.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, NotRequired, TypedDict

from pydantic import Field, TypeAdapter

from fastapi_a2a._internal.constants import TaskState

# ── Agent identity ─────────────────────────────────────────────────────────────


class AgentProvider(TypedDict):
    organization: str
    url: NotRequired[str]


class AgentCapabilities(TypedDict, total=False):
    streaming: bool
    pushNotifications: bool
    stateTransitionHistory: bool


class AgentSkill(TypedDict):
    id: str
    name: str
    description: str
    inputModes: list[str]
    outputModes: list[str]
    tags: NotRequired[list[str]]
    examples: NotRequired[list[str]]
    endpoint: NotRequired[str]  # internal routing — stripped before wire


# FIX E1: OAuthFlows and SecurityScheme are now fully typed TypedDicts
# (were previously `Any`, removing all type safety from auth config).


class OAuthFlows(TypedDict, total=False):
    implicit: dict[str, Any]  # {authorizationUrl, scopes}
    password: dict[str, Any]  # {tokenUrl, scopes}
    clientCredentials: dict[str, Any]  # {tokenUrl, scopes}
    authorizationCode: dict[str, Any]  # {authorizationUrl, tokenUrl, scopes}


class SecurityScheme(TypedDict):
    type: Literal["apiKey", "http", "oauth2", "openIdConnect"]
    description: NotRequired[str]
    # apiKey fields
    name: NotRequired[str]
    in_: NotRequired[Literal["query", "header", "cookie"]]
    # http fields
    scheme: NotRequired[str]
    bearerFormat: NotRequired[str]
    # oauth2 fields
    flows: NotRequired[OAuthFlows]
    # openIdConnect fields
    openIdConnectUrl: NotRequired[str]


class AgentCard(TypedDict):
    name: str
    url: str
    version: str
    protocolVersion: str
    capabilities: AgentCapabilities
    skills: list[AgentSkill]
    defaultInputModes: list[str]
    defaultOutputModes: list[str]
    description: NotRequired[str]
    provider: NotRequired[AgentProvider]
    documentationUrl: NotRequired[str]
    iconUrl: NotRequired[str]
    security: NotRequired[list[dict[str, list[str]]]]
    securitySchemes: NotRequired[dict[str, SecurityScheme]]


# ── Content parts ──────────────────────────────────────────────────────────────


class FileWithBytes(TypedDict):
    # FIX A4: field was named `bytes` — shadowed the Python built-in.
    content: str
    mimeType: NotRequired[str]


class FileWithUri(TypedDict):
    uri: str
    mimeType: NotRequired[str]


class TextPart(TypedDict):
    kind: Literal["text"]
    text: str
    metadata: NotRequired[dict[str, Any]]


class FilePart(TypedDict):
    kind: Literal["file"]
    file: FileWithBytes | FileWithUri
    metadata: NotRequired[dict[str, Any]]


class DataPart(TypedDict):
    kind: Literal["data"]
    data: dict[str, Any]
    metadata: NotRequired[dict[str, Any]]


Part = Annotated[
    TextPart | FilePart | DataPart,
    Field(discriminator="kind"),
]


# ── Message & Artifact ─────────────────────────────────────────────────────────


class Message(TypedDict):
    role: Literal["user", "agent"]
    kind: Literal["message"]
    parts: list[Part]
    messageId: str
    contextId: NotRequired[str]
    taskId: NotRequired[str]
    metadata: NotRequired[dict[str, Any]]


class Artifact(TypedDict):
    artifactId: str
    parts: list[Part]
    name: NotRequired[str]
    description: NotRequired[str]
    index: NotRequired[int]
    append: NotRequired[bool]
    lastChunk: NotRequired[bool]
    metadata: NotRequired[dict[str, Any]]


# ── Task ───────────────────────────────────────────────────────────────────────


class TaskStatus(TypedDict):
    state: TaskState
    timestamp: str
    message: NotRequired[Message]


class Task(TypedDict):
    id: str
    contextId: str
    kind: Literal["task"]
    status: TaskStatus
    history: list[Message]
    artifacts: list[Artifact]
    createdAt: str
    updatedAt: str
    metadata: NotRequired[dict[str, Any]]


# ── Pagination ─────────────────────────────────────────────────────────────────


class TaskListResult(TypedDict):
    tasks: list[Task]
    nextCursor: NotRequired[str]


# ── JSON-RPC envelope ──────────────────────────────────────────────────────────


class JSONRPCError(TypedDict):
    code: int
    message: str
    data: NotRequired[Any]


class JSONRPCRequest(TypedDict):
    jsonrpc: Literal["2.0"]
    id: int | str | None
    method: str
    params: NotRequired[dict[str, Any]]


class JSONRPCSuccessResponse(TypedDict):
    jsonrpc: Literal["2.0"]
    id: int | str | None
    result: Any


class JSONRPCErrorResponse(TypedDict):
    jsonrpc: Literal["2.0"]
    id: int | str | None
    error: JSONRPCError


# ── Module-level TypeAdapters ──────────────────────────────────────────────────
# Built once at import time. Never call TypeAdapter(...) inside a request handler.

task_adapter = TypeAdapter(Task)
task_list_adapter = TypeAdapter(TaskListResult)
message_adapter = TypeAdapter(Message)
part_adapter = TypeAdapter(Part)
agent_card_adapter = TypeAdapter(AgentCard)
rpc_request_adapter = TypeAdapter(JSONRPCRequest)
