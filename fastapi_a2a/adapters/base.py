"""
BaseAdapter ABC.

Three responsibilities, three methods. Nothing more.

scan()  — read the existing app's routes, return AgentSkills
call()  — call one of those routes internally (no network hop)
mount() — attach A2A endpoints onto the existing app

One file per framework. Each file is ~100 lines.
Protocol logic never lives here.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from fastapi_a2a._internal.schema import AgentSkill


class BaseAdapter(ABC):
    @abstractmethod
    def scan(self, app: Any) -> list[AgentSkill]:
        """
        Inspect the existing app's route registry.
        Return one AgentSkill per route decorated with @a2a_skill.
        The skill's endpoint field must be the exact route path.
        Routes without @a2a_skill metadata are silently ignored.
        """

    @abstractmethod
    async def call(
        self,
        app: Any,
        path: str,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        """
        Call an existing route handler internally.
        No network socket. No DNS. In-process only.
        Return the response body as a Python dict.
        Raise A2AError on non-2xx status.
        """

    @abstractmethod
    def mount(
        self,
        app: Any,
        routes: list[tuple[str, Any, list[str]]],
    ) -> None:
        """
        Attach new routes onto the existing app.
        routes: list of (path, handler_coroutine, http_methods)
        Called once by FastApiA2A.mount().
        """
