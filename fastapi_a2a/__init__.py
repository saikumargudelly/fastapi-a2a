"""
fastapi_a2a — public API.

Import only from here. Never from fastapi_a2a._internal.*

Everything below is stable public surface. All other sub-modules are
implementation details that may change in any release.
"""

from fastapi_a2a._internal.constants import (
    PROTOCOL_VERSION,
    TERMINAL_STATES,
    VALID_TRANSITIONS,
    TaskState,
)
from fastapi_a2a._internal.exceptions import (
    A2AError,
    A2AInternalError,
    A2ARemoteError,
    AuthRequiredError,  # FIX C1: was missing
    InvalidStateTransitionError,
    PushNotSupportedError,  # FIX C2: was missing
    TaskNotCancelableError,
    TaskNotFoundError,
    UnsupportedOperationError,
    VersionNotSupportedError,  # FIX C3: was missing from exports
)
from fastapi_a2a._internal.schema import (
    AgentCapabilities,
    AgentCard,
    AgentProvider,
    AgentSkill,
    Artifact,
    DataPart,
    FilePart,
    FileWithBytes,
    FileWithUri,
    Message,
    OAuthFlows,
    Part,
    SecurityScheme,
    Task,
    TaskListResult,
    TaskStatus,
    TextPart,
)

from fastapi_a2a.client import A2AClient
from fastapi_a2a.decorators import a2a_skill
from fastapi_a2a.plugin import FastApiA2A

__version__ = "1.0.0"

__all__ = [
    "PROTOCOL_VERSION",
    "TERMINAL_STATES",
    "VALID_TRANSITIONS",
    "A2AClient",
    "A2AError",
    "A2AInternalError",
    "A2ARemoteError",
    "AgentCapabilities",
    "AgentCard",
    "AgentProvider",
    "AgentSkill",
    "Artifact",
    "AuthRequiredError",
    "DataPart",
    "FastApiA2A",
    "FilePart",
    "FileWithBytes",
    "FileWithUri",
    "InvalidStateTransitionError",
    "Message",
    "OAuthFlows",
    "Part",
    "PushNotSupportedError",
    "SecurityScheme",
    "Task",
    "TaskListResult",
    "TaskNotCancelableError",
    "TaskNotFoundError",
    "TaskState",
    "TaskStatus",
    "TextPart",
    "UnsupportedOperationError",
    "VersionNotSupportedError",
    "__version__",
    "a2a_skill",
]
