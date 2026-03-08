"""
FastApiAdapter.

Implements BaseAdapter for FastAPI applications.
The only file in this package that imports from fastapi directly.
"""

from __future__ import annotations

import re
from typing import Any

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

            allowed_methods = {"GET", "POST", "PUT", "DELETE", "PATCH"}
            if not route.methods or not any(m in route.methods for m in allowed_methods):
                raise ValueError(
                    f"Route {route.path} decorated with @a2a_skill must support GET, POST, PUT, DELETE, or PATCH"
                )

            methods = [m for m in route.methods if m in allowed_methods]
            primary_method = methods[0] if methods else "POST"

            skill: AgentSkill = {
                "id": meta.get("id") or _slugify(route.name),
                "name": meta.get("name") or route.name,
                "description": meta.get("description") or "",
                "tags": list(meta.get("tags") or []),
                "examples": list(meta.get("examples") or []),
                "inputModes": ["application/json"],
                "outputModes": ["application/json"],
                "endpoint": f"{primary_method} {route.path}",  # internal — stripped before wire
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
        Call a FastAPI route via direct ASGI dispatch.
        No network. Bypasses outer middlewares to prevent double execution.
        """
        import json

        method = "POST"
        if " " in path:
            method, path = path.split(" ", 1)

        path_params = payload.pop("__path__", {})
        query_params = payload.pop("__query__", "")
        # If the requester used the structured format, the actual payload is in 'body'
        body_payload = payload.pop("body", payload)

        try:
            resolved_path = path.format(**path_params)
        except KeyError as e:
            raise A2AInternalError(f"Missing path parameter for route: {e}") from e

        from contextlib import AsyncExitStack

        drop_headers = {
            "connection",
            "transfer-encoding",
            "content-length",
            "keep-alive",
            "upgrade",
            "proxy-authenticate",
            "proxy-authorization",
        }

        scope = {
            "type": "http",
            "method": method,
            "path": resolved_path,
            "query_string": query_params.encode(),
            "headers": [
                (b"content-type", b"application/json"),
                *[
                    (k.lower().encode(), v.encode())
                    for k, v in headers.items()
                    if k.lower() not in drop_headers
                ],
            ],
            "client": ("127.0.0.1", 0),
            "server": ("127.0.0.1", 80),
        }

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": json.dumps(body_payload).encode()}

        response_body = []
        response_status = 200
        from starlette.types import Message

        async def send(message: Message) -> None:
            nonlocal response_status
            if message["type"] == "http.response.start":
                response_status = message["status"]
            elif message["type"] == "http.response.body":
                response_body.append(message.get("body", b""))

        async with AsyncExitStack() as stack:
            scope["fastapi_middleware_astack"] = stack
            await app.router(scope, receive, send)

        if response_status >= 500:
            raise A2AInternalError(
                f"Endpoint {path!r} returned {response_status}: {b''.join(response_body).decode()}"
            )
        if response_status >= 400:
            raise A2AInternalError(
                f"Endpoint {path!r} rejected request ({response_status}): {b''.join(response_body).decode()}"
            )
        return json.loads(b"".join(response_body).decode())

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
