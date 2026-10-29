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
from typing import Any

from fastapi_a2a._internal.constants import PROTOCOL_VERSION
from fastapi_a2a._internal.schema import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
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
        supported_interfaces: list[AgentInterface] | None = None,
    ) -> None:
        self._name = name
        self._url = url
        self._version = version
        self._description = description or name
        self._capabilities = capabilities
        self._skills = skills
        self._provider = provider
        self._supported_interfaces: list[AgentInterface] = list(supported_interfaces or [])
        self._card = self._build()
        self._bytes: bytes | None = None

    def _build(self) -> AgentCard:
        if not self._skills:
            log.warning(
                "AgentCard built with no skills — the agent will not be "
                "discoverable by other agents. Did you forget @a2a_skill?"
            )
        # Strip endpoint — it must never appear on the wire, ensure tags/examples present
        clean_skills: list[AgentSkill] = []
        for s in self._skills:
            skill_dict: dict[str, Any] = {k: v for k, v in s.items() if k != "endpoint"}
            if "tags" not in skill_dict or skill_dict["tags"] is None:
                skill_dict["tags"] = []
            if "examples" not in skill_dict or skill_dict["examples"] is None:
                skill_dict["examples"] = []
            clean_skills.append(skill_dict)  # type: ignore[arg-type]

        # Standard A2A 1.0 capabilities structure
        caps: AgentCapabilities = {
            "streaming": self._capabilities.get("streaming", False),
            "pushNotifications": self._capabilities.get("pushNotifications", False),
            "extendedAgentCard": self._capabilities.get("extendedAgentCard", False),
            "extensions": list(self._capabilities.get("extensions", [])),
        }
        if "stateTransitionHistory" in self._capabilities:
            caps["stateTransitionHistory"] = self._capabilities["stateTransitionHistory"]

        card: AgentCard = {
            "name": self._name,
            "description": self._description,
            "version": self._version,
            "supportedInterfaces": list(self._supported_interfaces),
            "capabilities": caps,
            "skills": clean_skills,
            "defaultInputModes": ["application/json"],
            "defaultOutputModes": ["application/json"],
            # Backward-compatible fields for v0.x clients
            "url": self._url,
            "protocolVersion": PROTOCOL_VERSION,
        }
        if self._provider:
            card["provider"] = self._provider
        return card

    def set_supported_interfaces(self, interfaces: list[AgentInterface]) -> None:
        """Update supportedInterfaces once protocol dispatcher is mounted and functional."""
        self._supported_interfaces = list(interfaces)
        self._card["supportedInterfaces"] = list(interfaces)
        self._bytes = None

    def build(self) -> AgentCard:
        """Return the AgentCard dict."""
        return self._card

    def build_bytes(self) -> bytes:
        """Serialised JSON bytes. Built once, cached forever."""
        if self._bytes is None:
            self._bytes = agent_card_adapter.dump_json(self._card, by_alias=True, exclude_none=True)
        return self._bytes

    def invalidate_cache(self) -> None:
        """Force rebuild on next build_bytes() call."""
        self._bytes = None
