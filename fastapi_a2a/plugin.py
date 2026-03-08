"""
The heart of fastapi-a2a.

This module houses the `FastApiA2A` class, which acts as the main orchestration 
layer between the user's existing FastAPI application and the A2A protocol.

The design philosophy here is strictly "opt-in and non-destructive". When a developer 
calls `a2a.mount()`, we shouldn't mess with their existing route topology or middleware 
stack. Instead, we quietly slip in via the ASGI interface to handle the specific 
`/.well-known/agent.json` and `/a2a/rpc` endpoints.

This ensures that adopting the A2A spec feels like attaching a sidecar to the application,
rather than rewriting the core engine.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import FastAPI, Request, Response

from fastapi_a2a._internal.schema import (
    AgentCapabilities,
    AgentProvider,
    AgentSkill,
    rpc_request_adapter,
    task_adapter,
)
from fastapi_a2a._internal.task_manager import TaskManager
from fastapi_a2a._internal.card import AgentCardBuilder
from fastapi_a2a._internal.exceptions import A2AError, VersionNotSupportedError
from fastapi_a2a._internal.constants import (
    SUPPORTED_VERSIONS,
    PARSE_ERROR,
    METHOD_NOT_FOUND,
    INVALID_PARAMS,
    INTERNAL_ERROR,
)
from fastapi_a2a.stores.base import TaskStore
from fastapi_a2a.stores.memory import InMemoryTaskStore
from fastapi_a2a.adapters.fastapi import FastApiAdapter

log = logging.getLogger(__name__)


class FastApiA2A:
    """
    Integrates A2A protocol into an existing FastAPI application.

    Mounts:
      - ``GET /.well-known/agent.json``    — agent card for discovery
      - ``POST /a2a/rpc``                   — JSON-RPC 2.0 endpoint
    """

    def __init__(
        self,
        app: FastAPI,
        *,
        name: str,
        url: str,
        version: str = "1.0.0",
        description: str | None = None,
        provider: AgentProvider | None = None,
        store: TaskStore | None = None,
        # FIX A5: None sentinel — empty list [] is a valid explicit override
        skills: list[AgentSkill] | None = None,
        # Capability flags
        streaming: bool = False,
        push_notifications: bool = False,
        state_transition_history: bool = True,
        # Execution limits
        timeout_seconds: float = 120.0,
        max_concurrency: int = 100,
        # Protocol options
        prefix: str = "/a2a",
    ) -> None:
        self._app = app
        self._prefix = prefix.rstrip("/")
        self._store: TaskStore = store or InMemoryTaskStore()
        self._is_mounted = False

        # FIX B3: create adapter ONCE here; reuse in both __init__ and mount()
        self._adapter = FastApiAdapter()

        # FIX A5: `is not None` — avoids treating [] as "not supplied"
        self._skills = skills if skills is not None else self._adapter.scan(app)

        self._manager = TaskManager(
            app=app,
            adapter=self._adapter,
            store=self._store,
            skills=self._skills,
            timeout_seconds=timeout_seconds,
            max_concurrency=max_concurrency,
        )
        self._card_builder = AgentCardBuilder(
            name=name,
            url=url,
            version=version,
            description=description or name,
            provider=provider,
            skills=self._skills,
            capabilities=AgentCapabilities(
                streaming=streaming,
                pushNotifications=push_notifications,
                stateTransitionHistory=state_transition_history,
            ),
        )

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def card_builder(self) -> AgentCardBuilder:
        return self._card_builder

    @property
    def is_mounted(self) -> bool:
        return self._is_mounted

    # ── Mount ─────────────────────────────────────────────────────────────────

    def mount(self) -> None:
        """
        Attach A2A endpoints onto the existing FastAPI app.
        Call this AFTER all your routes are registered.
        Calling twice raises RuntimeError.
        """
        if self._is_mounted:
            raise RuntimeError(
                "FastApiA2A.mount() already called on this application."
            )

        # Register lifespan events
        self._app.add_event_handler("startup", self._on_startup)
        self._app.add_event_handler("shutdown", self._on_shutdown)

        # FIX B3: reuse self._adapter — do NOT construct a second instance
        self._adapter.mount(self._app, [
            ("/.well-known/agent.json", self._handle_card, ["GET"]),
            (f"{self._prefix}/rpc", self._handle_rpc, ["POST"]),
        ])
        self._is_mounted = True

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def _on_startup(self) -> None:
        # FIX B2: call unconditionally — no isinstance check.
        # TaskStore.start() is a no-op by default; InMemoryTaskStore overrides it.
        await self._store.start()

    async def _on_shutdown(self) -> None:
        # FIX B2: same — unconditional call.
        await self._store.stop()

    # ── Handlers ──────────────────────────────────────────────────────────────

    async def _handle_card(self, request: Request) -> Response:
        return Response(
            content=self._card_builder.build_bytes(),
            media_type="application/json",
            headers={"Cache-Control": "public, max-age=300"},
        )

    async def _handle_rpc(self, request: Request) -> Response:
        version = request.headers.get("A2A-Version", "0.3.0")
        if version not in SUPPORTED_VERSIONS:
            return self._error_response(
                None, INVALID_PARAMS, f"Unsupported A2A-Version: {version!r}"
            )

        body = await request.body()
        try:
            rpc = rpc_request_adapter.validate_json(body)
        except Exception as exc:
            return self._error_response(None, PARSE_ERROR, str(exc))

        rpc_id = rpc.get("id")
        method = rpc.get("method", "")
        params: dict[str, Any] = rpc.get("params") or {}
        headers = self._extract_auth(request)

        try:
            match method:
                case "message/send":
                    # FIX D2: validate required key before delegation
                    if "message" not in params:
                        return self._error_response(
                            rpc_id, INVALID_PARAMS,
                            "params.message is required for message/send",
                        )
                    task = await self._manager.send_message(params, headers)
                    return self._ok_response(
                        rpc_id,
                        task_adapter.dump_python(task, by_alias=True, exclude_none=True),
                    )

                case "tasks/get":
                    # FIX D1: validate 'id' — was bare KeyError → INTERNAL_ERROR
                    if "id" not in params or not isinstance(params["id"], str):
                        return self._error_response(
                            rpc_id, INVALID_PARAMS,
                            "params.id (string) is required for tasks/get",
                        )
                    task = await self._manager.get_task(params["id"])
                    return self._ok_response(
                        rpc_id,
                        task_adapter.dump_python(task, by_alias=True, exclude_none=True),
                    )

                case "tasks/cancel":
                    # FIX D1: same validation as tasks/get
                    if "id" not in params or not isinstance(params["id"], str):
                        return self._error_response(
                            rpc_id, INVALID_PARAMS,
                            "params.id (string) is required for tasks/cancel",
                        )
                    task = await self._manager.cancel_task(params["id"])
                    return self._ok_response(
                        rpc_id,
                        task_adapter.dump_python(task, by_alias=True, exclude_none=True),
                    )

                case "tasks/list":
                    # FIX B8: expose list() — was implemented on the store
                    # but unreachable via the protocol. Now properly routed.
                    limit = min(int(params.get("limit", 50)), 200)
                    tasks, next_cursor = await self._manager.list_tasks(
                        context_id=params.get("contextId"),
                        state=params.get("state"),
                        limit=limit,
                        cursor=params.get("cursor"),
                    )
                    result: dict[str, Any] = {
                        "tasks": [
                            task_adapter.dump_python(t, by_alias=True, exclude_none=True)
                            for t in tasks
                        ]
                    }
                    if next_cursor:
                        result["nextCursor"] = next_cursor
                    return self._ok_response(rpc_id, result)

                case _:
                    return self._error_response(
                        rpc_id, METHOD_NOT_FOUND, f"Method not found: {method!r}"
                    )

        except A2AError as exc:
            return self._error_response(rpc_id, exc.code, str(exc))
        except Exception as exc:
            log.exception("Unhandled error in RPC handler for method %r", method)
            return self._error_response(rpc_id, INTERNAL_ERROR, str(exc))

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _extract_auth(self, request: Request) -> dict[str, str]:
        headers: dict[str, str] = {}
        auth = request.headers.get("Authorization")
        if auth:
            headers["Authorization"] = auth
        return headers

    def _ok_response(self, rpc_id: Any, result: Any) -> Response:
        body = json.dumps({"jsonrpc": "2.0", "id": rpc_id, "result": result})
        return Response(content=body, media_type="application/json")

    def _error_response(self, rpc_id: Any, code: int, message: str) -> Response:
        body = json.dumps({
            "jsonrpc": "2.0",
            "id": rpc_id,
            "error": {"code": code, "message": message},
        })
        # JSON-RPC errors are always HTTP 200 — status is in the error body
        return Response(content=body, media_type="application/json", status_code=200)
