"""
fastapi_a2a — public API.

Import only from here. Never from fastapi_a2a._internal.*

Everything below is stable public surface. All other sub-modules are
implementation details that may change in any release.
"""
from fastapi_a2a.plugin import FastApiA2A
from fastapi_a2a.client import A2AClient
from fastapi_a2a.decorators import a2a_skill

from fastapi_a2a.stores.base import TaskStore
from fastapi_a2a.stores.memory import InMemoryTaskStore

from fastapi_a2a.adapters.base import BaseAdapter
from fastapi_a2a.adapters.fastapi import FastApiAdapter

from fastapi_a2a._internal.schema import (
    AgentCard,
    AgentCapabilities,
    AgentProvider,
    AgentSkill,
    OAuthFlows,
    SecurityScheme,
    Task,
    TaskStatus,
    TaskListResult,
    Message,
    Part,
    TextPart,
    FilePart,
    DataPart,
    FileWithBytes,
    FileWithUri,
    Artifact,
)
from fastapi_a2a._internal.constants import (
    TaskState,
    TERMINAL_STATES,
    VALID_TRANSITIONS,
    PROTOCOL_VERSION,
)
from fastapi_a2a._internal.exceptions import (
    A2AError,
    A2AInternalError,
    A2ARemoteError,
    AuthRequiredError,          # FIX C1: was missing
    InvalidStateTransitionError,
    PushNotSupportedError,      # FIX C2: was missing
    TaskNotCancelableError,
    TaskNotFoundError,
    UnsupportedOperationError,
    VersionNotSupportedError,   # FIX C3: was missing from exports
)
# RequestContext — useful for type hints in route handlers
from fastapi_a2a._internal.task_manager import RequestContext

__version__ = "0.1.0"

__all__ = [
    # ── Entry points ──────────────────────────────────────────────────────────
    "FastApiA2A",
    "A2AClient",
    "a2a_skill",
    # ── Stores ────────────────────────────────────────────────────────────────
    "TaskStore",
    "InMemoryTaskStore",
    # ── Adapters ──────────────────────────────────────────────────────────────
    "BaseAdapter",
    "FastApiAdapter",
    # ── Schema ────────────────────────────────────────────────────────────────
    "AgentCard",
    "AgentCapabilities",
    "AgentProvider",
    "AgentSkill",
    "OAuthFlows",
    "SecurityScheme",
    "Task",
    "TaskStatus",
    "TaskState",
    "TaskListResult",
    "Message",
    "Part",
    "TextPart",
    "FilePart",
    "DataPart",
    "FileWithBytes",
    "FileWithUri",
    "Artifact",
    # ── Constants ─────────────────────────────────────────────────────────────
    "TERMINAL_STATES",
    "VALID_TRANSITIONS",
    "PROTOCOL_VERSION",
    # ── Exceptions ────────────────────────────────────────────────────────────
    "A2AError",
    "A2AInternalError",
    "A2ARemoteError",
    "AuthRequiredError",
    "InvalidStateTransitionError",
    "PushNotSupportedError",
    "TaskNotCancelableError",
    "TaskNotFoundError",
    "UnsupportedOperationError",
    "VersionNotSupportedError",
    # ── Utilities ─────────────────────────────────────────────────────────────
    "RequestContext",
    "__version__",
]
