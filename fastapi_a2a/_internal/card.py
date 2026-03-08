"""
AgentCardBuilder.

Builds the AgentCard from constructor parameters + scanned skills.
Caches the serialised bytes after the first build.
Strips endpoint from skills before serialisation — that is internal only.

FIX A6: removed dead import of TaskStore (was "only for type check" but
never actually referenced anywhere in this file).
"""
from __future__ import annotations

import logging

from fastapi_a2a._internal.constants import PROTOCOL_VERSION
from fastapi_a2a._internal.schema import (
    AgentCapabilities,
    AgentCard,
    AgentProvider,
    AgentSkill,
    agent_card_adapter,
)

log = logging.getLogger(__name__)


class AgentCardBuilder:

    def __init__(
        self,
        name: str,
        url: str,
        version: str,
        description: str,
        capabilities: AgentCapabilities,
        skills: list[AgentSkill],
        provider: AgentProvider | None,
    ) -> None:
        self._card = self._build(name, url, version, description,
                                 capabilities, skills, provider)
        self._bytes: bytes | None = None

    def _build(
        self,
        name: str,
        url: str,
        version: str,
        description: str,
        capabilities: AgentCapabilities,
        skills: list[AgentSkill],
        provider: AgentProvider | None,
    ) -> AgentCard:
        if not skills:
            log.warning(
                "AgentCard built with no skills — the agent will not be "
                "discoverable by other agents. Did you forget @a2a_skill?"
            )
        # Strip endpoint — it must never appear on the wire
        clean_skills: list[AgentSkill] = [
            {k: v for k, v in s.items() if k != "endpoint"}  # type: ignore[misc]
            for s in skills
        ]
        card: AgentCard = {
            "name": name,
            "url": url,
            "version": version,
            "protocolVersion": PROTOCOL_VERSION,
            "description": description,
            "capabilities": capabilities,
            "skills": clean_skills,
            "defaultInputModes": ["application/json"],
            "defaultOutputModes": ["application/json"],
        }
        if provider:
            card["provider"] = provider
        return card

    def build(self) -> AgentCard:
        """Return the AgentCard dict."""
        return self._card

    def build_bytes(self) -> bytes:
        """Serialised JSON bytes. Built once, cached forever."""
        if self._bytes is None:
            self._bytes = agent_card_adapter.dump_json(
                self._card, by_alias=True, exclude_none=True
            )
        return self._bytes

    def invalidate_cache(self) -> None:
        """Force rebuild on next build_bytes() call."""
        self._bytes = None
