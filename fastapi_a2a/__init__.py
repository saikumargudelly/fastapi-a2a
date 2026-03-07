"""
fastapi-a2a: Expose FastAPI applications as A2A Protocol agents.

Zero boilerplate. Auto-discovers routes, derives typed skill schemas,
registers with a discovery registry, and provides production-grade security.
"""
from fastapi_a2a.bridge.config import FastApiA2AConfig, RegistryConfig
from fastapi_a2a.bridge.main import FastApiA2A
from fastapi_a2a.database import Base, get_db
from fastapi_a2a.domains.core_a2a.schemas import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    SkillSchema,
)
from fastapi_a2a.exceptions import (
    A2AError,
    A2AHTTPError,
    AccessDeniedError,
    CardNotFoundError,
    CardSignatureInvalidError,
    ConsentExpiredError,
    ConsentMissingError,
    DatabaseUnavailableError,
    SkillCircuitOpenError,
    SkillNotFoundError,
    SkillTimeoutError,
    TaskNotFoundError,
)
from fastapi_a2a.logging import get_logger
from fastapi_a2a.middleware import RequestIdMiddleware, SecurityHeadersMiddleware

__version__ = "0.6.0"
__all__ = [
    # Core
    "FastApiA2A",
    "RegistryConfig",
    "FastApiA2AConfig",
    # Schemas
    "AgentCard",
    "AgentSkill",
    "AgentCapabilities",
    "SkillSchema",
    # Database
    "Base",
    "get_db",
    # Exceptions
    "A2AError",
    "A2AHTTPError",
    "CardNotFoundError",
    "CardSignatureInvalidError",
    "ConsentMissingError",
    "ConsentExpiredError",
    "AccessDeniedError",
    "SkillCircuitOpenError",
    "SkillNotFoundError",
    "SkillTimeoutError",
    "TaskNotFoundError",
    "DatabaseUnavailableError",
    # Middleware
    "SecurityHeadersMiddleware",
    "RequestIdMiddleware",
    # Logging
    "get_logger",
]
