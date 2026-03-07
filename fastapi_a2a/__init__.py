"""
fastapi-a2a: Expose FastAPI applications as A2A Protocol agents.

Zero boilerplate. Auto-discovers routes, derives typed skill schemas,
registers with a discovery registry, and provides production-grade security.
"""
from fastapi_a2a.bridge.config import FastApiA2AConfig, RegistryConfig
from fastapi_a2a.bridge.main import FastApiA2A
from fastapi_a2a.domains.core_a2a.schemas import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    SkillSchema,
)

__version__ = "0.6.0"
__all__ = [
    "FastApiA2A",
    "RegistryConfig",
    "FastApiA2AConfig",
    "AgentCard",
    "AgentSkill",
    "AgentCapabilities",
    "SkillSchema",
]
