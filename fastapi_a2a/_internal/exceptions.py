"""
Exception hierarchy. Every exception the package raises is an A2AError.
Each maps to a JSON-RPC error code. No bare Exception is ever raised.

COMPLETE hierarchy — every protocol error code has a corresponding class.
"""
from __future__ import annotations

from fastapi_a2a._internal import constants as c


class A2AError(Exception):
    """Base. All package exceptions inherit from this."""

    code: int = c.INTERNAL_ERROR

    def __init__(self, message: str, data: object = None) -> None:
        super().__init__(message)
        self.data = data

    def as_dict(self) -> dict:
        d: dict = {"code": self.code, "message": str(self)}
        if self.data is not None:
            d["data"] = self.data
        return d


class TaskNotFoundError(A2AError):
    code = c.TASK_NOT_FOUND

    def __init__(self, task_id: str) -> None:
        super().__init__(f"Task not found: {task_id!r}")
        self.task_id = task_id


class TaskNotCancelableError(A2AError):
    code = c.TASK_NOT_CANCELABLE

    def __init__(self, task_id: str = "", state: str = "terminal") -> None:
        super().__init__(f"Task {task_id!r} is in terminal state {state!r}")
        self.task_id = task_id
        self.state = state


class InvalidStateTransitionError(A2AError):
    code = c.INVALID_STATE_TRANSITION

    def __init__(self, from_state: str, to_state: str = "") -> None:
        super().__init__(f"Cannot transition {from_state!r} → {to_state!r}")
        self.from_state = from_state
        self.to_state = to_state


class UnsupportedOperationError(A2AError):
    code = c.UNSUPPORTED_OPERATION


class AuthRequiredError(A2AError):
    """
    Raised when authentication is required but not provided or invalid.
    FIX C1: was missing entirely. AUTH_REQUIRED (-32003) had no exception class.
    """
    code = c.AUTH_REQUIRED


class PushNotSupportedError(A2AError):
    """
    Raised when a push-notification request is made on an agent that
    does not support it.
    FIX C2: was missing entirely. PUSH_NOT_SUPPORTED (-32006) had no exception class.
    """
    code = c.PUSH_NOT_SUPPORTED


class VersionNotSupportedError(A2AError):
    """FIX C3: was missing from public exports."""
    code = c.VERSION_NOT_SUPPORTED

    def __init__(self, version: str = "") -> None:
        super().__init__(f"A2A version not supported: {version!r}")
        self.version = version


class A2AInternalError(A2AError):
    """Raised when an underlying endpoint returns an unexpected status."""
    code = c.INTERNAL_ERROR


class A2ARemoteError(A2AError):
    """Raised by A2AClient when the remote agent returns a JSON-RPC error."""

    def __init__(self, error_dict: dict | None = None, code: int = c.INTERNAL_ERROR,
                 message: str = "", data: object = None) -> None:
        if error_dict is not None:
            code = error_dict.get("code", c.INTERNAL_ERROR)
            message = error_dict.get("message", "Remote error")
            data = error_dict.get("data")
        super().__init__(message, data)
        self.code = code
