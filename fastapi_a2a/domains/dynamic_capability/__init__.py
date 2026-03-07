"""
fastapi_a2a.domains.dynamic_capability — Dynamic Capability domain models.

Library consumers who need to extend or query skill query logs or NLP config can import from here:

    from fastapi_a2a.domains.dynamic_capability.models import (
        SkillQueryLog,
        NlpAnalyzerConfig,
    )
"""
from fastapi_a2a.domains.dynamic_capability.models import NlpAnalyzerConfig, SkillQueryLog

__all__ = [
    "SkillQueryLog",
    "NlpAnalyzerConfig",
]
