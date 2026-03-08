"""
FastApiAdapter.

Implements BaseAdapter for FastAPI applications.
The only file in this package that imports from fastapi directly.
"""

from __future__ import annotations

import re
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.routing import APIRoute

from fastapi_a2a._internal.exceptions import A2AInternalError
from fastapi_a2a._internal.schema import AgentSkill
from fastapi_a2a.adapters.base import BaseAdapter


class FastApiAdapter(BaseAdapter):
    def scan(self, app: FastAPI) -> list[AgentSkill]:  # type: ignore[override]
        """
        Walk the FastAPI route tree.
        Only APIRoute instances with _a2a_skill metadata are returned.
        Mounted sub-applications and WebSocket routes are ignored.
        """
        skills: list[AgentSkill] = []
        for route in app.routes:
            if not isinstance(route, APIRoute):
                continue
            meta: dict | None = getattr(route.endpoint, "_a2a_skill", None)
            if meta is None:
                continue
            skill: AgentSkill = {
                "id": meta.get("id") or _slugify(route.name),
                "name": meta.get("name") or route.name,
                "description": meta.get("description") or "",
                "tags": list(meta.get("tags") or []),
                "examples": list(meta.get("examples") or []),
                "inputModes": ["application/json"],
                "outputModes": ["application/json"],
                "endpoint": route.path,  # internal — stripped before wire
            }
            skills.append(skill)
        return skills

    async def call(  # type: ignore[override]
        self,
        app: FastAPI,
        path: str,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        """
        Call a FastAPI route via ASGI transport.
        No network. No port. Direct ASGI call.
        """
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            response = await client.post(path, json=payload, headers=headers)

        if response.status_code >= 500:
            raise A2AInternalError(
                f"Endpoint {path!r} returned {response.status_code}: {response.text}"
            )
        if response.status_code >= 400:
            raise A2AInternalError(
                f"Endpoint {path!r} rejected request ({response.status_code}): {response.text}"
            )
        return response.json()  # type: ignore[no-any-return]

    def mount(  # type: ignore[override]
        self,
        app: FastAPI,
        routes: list[tuple[str, Any, list[str]]],
    ) -> None:
        from fastapi.routing import APIRouter

        router = APIRouter()
        for path, handler, methods in routes:
            router.add_api_route(path, handler, methods=methods, include_in_schema=True)
        app.include_router(router)


def _slugify(name: str) -> str:
    """Convert a Python function name to a kebab-case skill id."""
    name = name.lstrip("_")
    name = re.sub(r"[^a-z0-9]+", "-", name.lower())
    return name.strip("-")
