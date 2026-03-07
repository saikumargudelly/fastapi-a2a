"""
fastapi_a2a.bridge — Public ORM models for the FastAPI Bridge domain.

Library consumers who need to query or extend these tables directly can import from here:

    from fastapi_a2a.bridge.models import (
        RouteMapping,
        FastApiA2AConfigRow,
        StartupAuditLog,
        SdkCompatibilityMatrix,
    )
"""
from fastapi_a2a.bridge.models import (
    FastApiA2AConfigRow,
    RouteMapping,
    SdkCompatibilityMatrix,
    StartupAuditLog,
)

__all__ = [
    "RouteMapping",
    "FastApiA2AConfigRow",
    "StartupAuditLog",
    "SdkCompatibilityMatrix",
]
