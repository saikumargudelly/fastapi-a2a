"""
fastapi_a2a.exceptions — Typed A2A error hierarchy.

All domain errors raised by the library are subclasses of ``A2AError``.
HTTP-facing errors are subclasses of ``A2AHTTPError`` and carry an
``error_code`` from the A2A spec error-code namespace.

Usage::

    from fastapi_a2a.exceptions import ConsentMissingError, SkillCircuitOpenError

    try:
        result = await invoke_skill(...)
    except ConsentMissingError as exc:
        # exc.status_code == 403, exc.error_code == "4020"
        raise HTTPException(exc.status_code, detail=exc.detail)
"""
from __future__ import annotations


class A2AError(Exception):
    """Base class for all fastapi-a2a errors."""


# ---------------------------------------------------------------------------
# HTTP-facing errors (carry status_code + A2A error_code)
# ---------------------------------------------------------------------------

class A2AHTTPError(A2AError):
    """An error that maps to an HTTP response with an A2A error code."""

    status_code: int = 500
    error_code: str = "5000"

    def __init__(self, detail: str | None = None) -> None:
        self.detail = detail or type(self).__doc__ or "Internal A2A error"
        super().__init__(self.detail)


# ── Card errors (4000–4009) ─────────────────────────────────────────────────

class CardNotFoundError(A2AHTTPError):
    """Agent card not found."""
    status_code = 404
    error_code = "4000"


class CardNotInitialisedError(A2AHTTPError):
    """Agent card not yet initialised — call startup() first."""
    status_code = 503
    error_code = "4001"


class CardSignatureInvalidError(A2AHTTPError):
    """Agent card JWS signature verification failed."""
    status_code = 401
    error_code = "4010"


# ── Consent errors (4020–4022) ───────────────────────────────────────────────

class ConsentMissingError(A2AHTTPError):
    """No active consent record found for this caller/purpose."""
    status_code = 403
    error_code = "4020"


class ConsentExpiredError(A2AHTTPError):
    """Consent record exists but has expired."""
    status_code = 403
    error_code = "4021"


class ConsentRegionViolationError(A2AHTTPError):
    """Data residency constraint violated — consent not valid in this region."""
    status_code = 403
    error_code = "4022"


# ── Skill errors (4030–4033) ─────────────────────────────────────────────────

class SkillTimeoutError(A2AHTTPError):
    """Skill execution exceeded max_task_duration_seconds."""
    status_code = 504
    error_code = "4030"


class SkillMemoryExceededError(A2AHTTPError):
    """Skill execution exceeded max_memory_mb."""
    status_code = 507
    error_code = "4031"


class SkillCircuitOpenError(A2AHTTPError):
    """Circuit breaker is open — skill temporarily unavailable."""
    status_code = 503
    error_code = "4032"


class SkillNotFoundError(A2AHTTPError):
    """No skill matching the requested skill_id."""
    status_code = 404
    error_code = "4033"


# ── Access errors (4040–4041) ────────────────────────────────────────────────

class AccessDeniedError(A2AHTTPError):
    """Caller does not have permission for this operation."""
    status_code = 403
    error_code = "4040"


class RoleMissingError(A2AHTTPError):
    """Required role not present in caller identity."""
    status_code = 403
    error_code = "4041"


# ── Rate-limit errors (4050–4051) ────────────────────────────────────────────

class RateLimitWindowExceededError(A2AHTTPError):
    """Rolling-window request limit exceeded."""
    status_code = 429
    error_code = "4050"


class RateLimitBurstExceededError(A2AHTTPError):
    """Micro-window burst limit exceeded."""
    status_code = 429
    error_code = "4051"


# ── Governance errors (4060–4061) ─────────────────────────────────────────────

class DataResidencyViolationError(A2AHTTPError):
    """Governance policy blocked — data residency constraint violated."""
    status_code = 403
    error_code = "4060"


class SkillBlockedByGovernanceError(A2AHTTPError):
    """Skill invocation blocked by active governance policy."""
    status_code = 403
    error_code = "4061"


# ── Task errors (4070–4072) ───────────────────────────────────────────────────

class TaskNotFoundError(A2AHTTPError):
    """Task ID does not exist."""
    status_code = 404
    error_code = "4070"


class TaskAlreadyCancelledError(A2AHTTPError):
    """Task has already been cancelled or completed."""
    status_code = 409
    error_code = "4071"


class TaskNotCancellableError(A2AHTTPError):
    """Task is in a terminal state and cannot be cancelled."""
    status_code = 409
    error_code = "4072"


# ── Platform errors (5000–5002) ───────────────────────────────────────────────

class PlatformInternalError(A2AHTTPError):
    """Unexpected internal error."""
    status_code = 500
    error_code = "5000"


class DatabaseUnavailableError(A2AHTTPError):
    """Database is unreachable or returned an unexpected error."""
    status_code = 503
    error_code = "5001"


class RegistryUnavailableError(A2AHTTPError):
    """Discovery registry is unreachable."""
    status_code = 503
    error_code = "5002"


# ---------------------------------------------------------------------------
# Non-HTTP library errors (configuration / internal logic)
# ---------------------------------------------------------------------------

class ConfigurationError(A2AError):
    """Invalid library configuration supplied by the consumer."""


class StartupError(A2AError):
    """Raised when startup() fails (e.g. DB unreachable, bad config)."""
