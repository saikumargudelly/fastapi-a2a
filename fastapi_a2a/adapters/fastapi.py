"""
FastApiAdapter.

Implements BaseAdapter for FastAPI applications.
The only file in this package that imports from fastapi directly.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.routing import BaseRoute, Mount

try:
    from fastapi.routing import iter_route_contexts  # type: ignore
except ImportError:
    iter_route_contexts = None  # type: ignore[assignment]

from fastapi_a2a._internal.exceptions import A2AInternalError
from fastapi_a2a._internal.schema import AgentSkill
from fastapi_a2a.adapters.base import BaseAdapter


class FastApiAdapter(BaseAdapter):
    @staticmethod
    def _iter_routes(app: FastAPI) -> Iterator[tuple[APIRoute, str]]:
        if iter_route_contexts is not None:
            ctx_iter = iter_route_contexts

            def _walk_contexts(
                routes: Sequence[BaseRoute], prefix: str = ""
            ) -> Iterator[tuple[APIRoute, str]]:
                for rc in ctx_iter(routes):
                    orig = getattr(rc, "original_route", None) or getattr(rc, "route", None)
                    if isinstance(orig, APIRoute):
                        path = getattr(rc, "path", None) or orig.path
                        full_path = f"{prefix.rstrip('/')}/{path.lstrip('/')}"
                        yield orig, full_path if full_path.startswith("/") else f"/{full_path}"
                    elif isinstance(orig, Mount):
                        sub_app = getattr(orig, "app", None)
                        sub_routes = getattr(sub_app, "routes", None)
                        if isinstance(sub_routes, Sequence):
                            mount_path = getattr(rc, "path", None) or getattr(orig, "path", "")
                            sub_prefix = f"{prefix.rstrip('/')}/{mount_path.lstrip('/')}"
                            yield from _walk_contexts(sub_routes, sub_prefix)

            yield from _walk_contexts(app.routes)
        else:

            def _walk(
                routes: Sequence[BaseRoute], prefix: str = ""
            ) -> Iterator[tuple[APIRoute, str]]:
                for r in routes:
                    if isinstance(r, APIRoute):
                        full_path = f"{prefix.rstrip('/')}/{r.path.lstrip('/')}"
                        yield r, full_path if full_path.startswith("/") else f"/{full_path}"
                    elif isinstance(r, Mount):
                        sub_app = getattr(r, "app", None)
                        sub_routes = getattr(sub_app, "routes", None)
                        if isinstance(sub_routes, Sequence):
                            sub_prefix = f"{prefix.rstrip('/')}/{r.path.lstrip('/')}"
                            yield from _walk(sub_routes, sub_prefix)

            yield from _walk(app.routes)

    def scan(self, app: FastAPI) -> list[AgentSkill]:  # type: ignore[override]
        """
        Walk the FastAPI route tree.
        Only APIRoute instances with _a2a_skill or openapi_extra metadata are returned.
        WebSocket routes and undecorated routes are ignored.
        """
        skills: list[AgentSkill] = []
        for route, full_path in self._iter_routes(app):
            meta: dict | None = getattr(route.endpoint, "_a2a_skill", None)

            # FastAPI Routers wrap endpoints in Dependants. We must inspect the underlying call.
            if meta is None and hasattr(route, "dependant"):
                if getattr(route.dependant, "call", None):
                    meta = getattr(route.dependant.call, "_a2a_skill", None)

            # Support native openapi_extra={"a2a": {...}}
            if meta is None and getattr(route, "openapi_extra", None):
                extra = route.openapi_extra
                if isinstance(extra, dict):
                    if "a2a" in extra and isinstance(extra["a2a"], dict):
                        meta = extra["a2a"]
                    elif "a2a_skill" in extra and isinstance(extra["a2a_skill"], dict):
                        meta = extra["a2a_skill"]

            if meta is None:
                continue

            allowed_methods = {"GET", "POST", "PUT", "DELETE", "PATCH"}
            if not route.methods or not any(m in route.methods for m in allowed_methods):
                raise ValueError(
                    f"Route {full_path} decorated with @a2a_skill must support GET, "
                    "POST, PUT, DELETE, or PATCH"
                )

            methods = [m for m in route.methods if m in allowed_methods]
            primary_method = methods[0] if methods else "POST"

            out_schema = None
            if getattr(route, "response_model", None):
                try:
                    from pydantic import TypeAdapter

                    out_schema = TypeAdapter(route.response_model).json_schema()
                except Exception:
                    pass

            skill: AgentSkill = {
                "id": meta.get("id") or _slugify(route.name),
                "name": meta.get("name") or route.name,
                "description": meta.get("description") or "",
                "tags": list(meta.get("tags") or []),
                "examples": list(meta.get("examples") or []),
                "inputModes": ["application/json"],
                "outputModes": ["application/json"],
                "endpoint": f"{primary_method} {full_path}",
            }
            if out_schema:
                skill["outputSchema"] = out_schema

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
            msg = b"".join(response_body).decode()
            raise A2AInternalError(f"Endpoint {path!r} rejected request ({response_status}): {msg}")
        return json.loads(b"".join(response_body).decode())

    def mount(  # type: ignore[override]
        self,
        app: FastAPI,
        routes: list[tuple[str, Any, list[str]]],
        *,
        lifespan: Any = None,
    ) -> None:
        from fastapi.routing import APIRouter

        router = APIRouter(lifespan=lifespan)
        for path, handler, methods in routes:
            router.add_api_route(path, handler, methods=methods, include_in_schema=True)
        app.include_router(router)


def _slugify(name: str) -> str:
    """Convert a Python function name to a kebab-case skill id."""
    name = name.lstrip("_")
    name = re.sub(r"[^a-z0-9]+", "-", name.lower())
    return name.strip("-")
