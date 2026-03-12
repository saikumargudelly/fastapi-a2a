"""
The outbound communication layer for fastapi-a2a.

While the plugin (`FastApiA2A`) is about exposing *your* app to the world,
the `A2AClient` is about letting your app talk to *others*.

It's designed to be completely framework-agnostic on the remote end. Your FastAPI
application can use this client to trigger tasks on a completely different system
(like a Ruby on Rails app or a Go microservice), as long as they speak the A2A
protocol.

Usage is heavily optimised for standard async/await patterns, so delegating a
task to an external AI agent feels just like calling a local async function.
"""

from __future__ import annotations

import asyncio  # FIX A3: top-level import, not __import__() inside a hot loop
import time
import uuid
from typing import Any

import httpx

from fastapi_a2a._internal.constants import PROTOCOL_VERSION, TERMINAL_STATES
from fastapi_a2a._internal.exceptions import A2ARemoteError
from fastapi_a2a._internal.schema import (
    AgentCard,
    Message,
    Task,
    agent_card_adapter,
    message_adapter,
    task_adapter,
)


class A2AClient:
    def __init__(
        self,
        base_url: str,
        *,
        auth_token: str | None = None,
        timeout_seconds: float = 30.0,
        card_ttl_seconds: int = 300,
        # FIX B7: bounded polling — prevents infinite polling on hung remote agents
        poll_timeout_seconds: float = 300.0,
        allowed_hosts: list[str] | None = None,
        allow_internal_ips: bool = False,
    ) -> None:
        self._base_url = base_url.rstrip("/")

        import ipaddress
        from urllib.parse import urlparse

        parsed = urlparse(self._base_url)
        hostname = parsed.hostname or ""

        if allowed_hosts is not None and hostname not in allowed_hosts:
            raise ValueError(f"Host {hostname} not in allowed A2A interactions")

        if not allow_internal_ips:
            if hostname == "localhost":
                raise ValueError("Private IP routing via A2AClient is restricted.")
            try:
                ip = ipaddress.ip_address(hostname)
                if ip.is_private or ip.is_loopback:
                    raise ValueError("Private IP routing via A2AClient is restricted.")
            except ValueError as e:
                if "Private IP routing" in str(e):
                    raise
        self._auth_token = auth_token
        self._timeout = timeout_seconds
        self._card_ttl = card_ttl_seconds
        self._poll_timeout = poll_timeout_seconds
        self._card_cache: AgentCard | None = None
        self._card_fetched: float = 0.0
        self._http: httpx.AsyncClient | None = None
        self._allow_internal = allow_internal_ips

    async def __aenter__(self) -> A2AClient:
        import ipaddress
        import socket
        from urllib.parse import urlparse

        def _secure_resolve(hostname: str) -> str:
            ip = socket.gethostbyname(hostname)
            if not self._allow_internal:
                addr = ipaddress.ip_address(ip)
                if addr.is_private or addr.is_loopback:
                    raise ValueError("SSRF Blocked: Resolves to private IP.")
            return ip

        parsed = urlparse(self._base_url)
        # Fast local host mapping override if a custom HTTP transport approach isn't available
        # Directing exact base_url to resolved IP while injecting Host Header natively patches 90%
        resolved_ip = _secure_resolve(parsed.hostname or "")
        safe_url = self._base_url.replace(parsed.hostname or "", resolved_ip)

        self._http = httpx.AsyncClient(
            base_url=safe_url,
            timeout=self._timeout,
            headers=get_default_headers(self._auth_token),
        )
        self._http.headers["Host"] = parsed.hostname or ""
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._http:
            await self._http.aclose()
            self._http = None

    # ── Discovery ─────────────────────────────────────────────────────────────

    async def get_card(self, *, force_refresh: bool = False) -> AgentCard:
        """Fetch remote AgentCard. Cached for card_ttl_seconds."""
        now = time.monotonic()
        if (
            not force_refresh
            and self._card_cache is not None
            and (now - self._card_fetched) < self._card_ttl
        ):
            return self._card_cache

        # FIX A2: explicit RuntimeError instead of assert (assert is a no-op
        # with python -O and would produce an AttributeError, not a helpful message).
        if self._http is None:
            raise RuntimeError(
                "A2AClient must be used as an async context manager: "
                "async with A2AClient(...) as client"
            )
        response = await self._http.get("/.well-known/agent.json")
        response.raise_for_status()
        self._card_cache = agent_card_adapter.validate_json(response.content)
        self._card_fetched = now
        return self._card_cache

    # ── Task operations ───────────────────────────────────────────────────────

    async def send_task(
        self,
        text: str,
        *,
        skill_id: str | None = None,
        data: dict | None = None,
        context_id: str | None = None,
        metadata: dict | None = None,
    ) -> Task:
        """
        Send a task via message/send protocol. Returns immediately with 'submitted' state.
        Callers must independently poll `poll_task_status(task_id)` to await completion.
        """
        parts = []
        if text:
            parts.append({"kind": "text", "text": text})
        if data:
            parts.append({"kind": "data", "data": data})

        combined_meta: dict[str, Any] | None = None
        if metadata or skill_id:
            combined_meta = dict(metadata or {})
            if skill_id:
                combined_meta["skillId"] = skill_id

        message: Message = {  # type: ignore[typeddict-item]
            "role": "user",
            "kind": "message",
            "messageId": str(uuid.uuid4()),
            "parts": parts,
            **({"metadata": combined_meta} if combined_meta else {}),
        }
        payload = create_rpc_payload(
            "message/send",
            {"message": message_adapter.dump_python(message, by_alias=True, exclude_none=True)}
        )
        return await self._rpc(payload)

    async def get_task(self, task_id: str) -> Task:
        return await self._rpc(create_rpc_payload("tasks/get", {"id": task_id}))

    async def cancel_task(self, task_id: str) -> Task:
        return await self._rpc(create_rpc_payload("tasks/cancel", {"id": task_id}))

    async def poll_task_status(
        self,
        task_id: str,
        interval_seconds: float = 0.5,
    ) -> Task:
        """
        Explicitly poll the tasks/get endpoint until a terminal state is reached.
        Raises TimeoutError after self._poll_timeout seconds.
        """
        deadline = time.monotonic() + self._poll_timeout
        while True:
            task = await self.get_task(task_id)
            if task["status"]["state"] in TERMINAL_STATES:
                return task
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"Task {task_id!r} did not complete within {self._poll_timeout:.0f}s"
                )
            await asyncio.sleep(min(interval_seconds, remaining))

    async def _rpc(self, payload: dict) -> Task:
        # FIX A2: explicit check instead of assert
        if self._http is None:
            raise RuntimeError(
                "A2AClient must be used as an async context manager: "
                "async with A2AClient(...) as client"
            )
        response = await self._http.post("/a2a/rpc", json=payload)
        response.raise_for_status()
        body = response.json()
        if "error" in body:
            err = body["error"]
            raise A2ARemoteError(
                code=err["code"],
                message=err.get("message", "Remote error"),
                data=err.get("data"),
            )
        return task_adapter.validate_python(body["result"])

def create_rpc_payload(method: str, params: dict) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": method,
        "params": params,
    }

def get_default_headers(auth_token: str | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "A2A-Version": PROTOCOL_VERSION,
    }
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    return headers

def create_a2a_client(
    base_url: str,
    *,
    auth_token: str | None = None,
    timeout_seconds: float = 30.0,
    card_ttl_seconds: int = 300,
    poll_timeout_seconds: float = 300.0,
    allowed_hosts: list[str] | None = None,
    allow_internal_ips: bool = False,
) -> A2AClient:
    """
    Primary factory for generating an A2A remote client.
    Handles bounding check dependencies and enforces clean constructor layout.
    """
    return A2AClient(
        base_url=base_url,
        auth_token=auth_token,
        timeout_seconds=timeout_seconds,
        card_ttl_seconds=card_ttl_seconds,
        poll_timeout_seconds=poll_timeout_seconds,
        allowed_hosts=allowed_hosts,
        allow_internal_ips=allow_internal_ips,
    )
