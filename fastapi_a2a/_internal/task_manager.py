"""
TaskManager.

Owns the lifecycle of every incoming A2A task.
Knows about: TaskStore, BaseAdapter, RequestContext.
Does NOT know about: FastAPI, HTTP, JSON-RPC parsing.
Those concerns stop at the plugin's RPC handler.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from typing import Any

from fastapi_a2a._internal.constants import TERMINAL_STATES
from fastapi_a2a._internal.exceptions import (
    A2AError,
    TaskNotCancelableError,
    TaskNotFoundError,
    UnsupportedOperationError,
)
from fastapi_a2a._internal.schema import AgentSkill, Artifact, Message, Task
from fastapi_a2a.adapters.base import BaseAdapter
from fastapi_a2a.stores.base import TaskStore

log = logging.getLogger(__name__)


class RequestContext:
    """
    Carries everything needed to execute one task.
    Extracted from the incoming A2A Message at the RPC layer.
    Passed down to the adapter's call().
    """

    __slots__ = (
        "auth_headers",
        "context_id",
        "message",
        "metadata",
        "skill_id",
        "task_id",
    )

    def __init__(
        self,
        task_id: str,
        context_id: str,
        message: Message,
        skill_id: str | None,
        auth_headers: dict[str, str],
        metadata: dict[str, Any] | None,
    ) -> None:
        self.task_id = task_id
        self.context_id = context_id
        self.message = message
        self.skill_id = skill_id
        self.auth_headers = auth_headers
        self.metadata = metadata or {}

    def extract_payload(self) -> dict[str, Any]:
        """Pull structured payload from message parts. DataPart wins."""
        for part in self.message.get("parts", []):
            if part["kind"] == "data":
                return part["data"]
        for part in self.message.get("parts", []):
            if part["kind"] == "text":
                with contextlib.suppress(Exception):
                    return json.loads(part["text"])
        return {}


class TaskManager:
    def __init__(
        self,
        app: Any,
        adapter: BaseAdapter,
        store: TaskStore,
        skills: list[AgentSkill],
        timeout_seconds: float,
        max_concurrency: int,
    ) -> None:
        self._app = app
        self._adapter = adapter
        self._store = store
        self._skill_map = {s["id"]: s for s in skills}
        self._timeout = timeout_seconds
        self._semaphore = asyncio.Semaphore(max_concurrency)

    # ── Public dispatch ───────────────────────────────────────────────────────

    async def send_message(
        self,
        params: dict[str, Any],
        auth_headers: dict[str, str],
    ) -> Task:
        """
        Accept an incoming message, create a task, fire-and-forget execution.
        FIX D2: 'message' key validated by caller before reaching this method.
        """
        message: Message = params["message"]
        context_id = message.get("contextId") or str(uuid.uuid4())
        if not message.get("messageId"):
            message = {**message, "messageId": str(uuid.uuid4())}

        task = await self._store.create(context_id, message)
        ctx = RequestContext(
            task_id=task["id"],
            context_id=context_id,
            message=message,
            skill_id=(message.get("metadata") or {}).get("skillId"),
            auth_headers=auth_headers,
            metadata=params.get("metadata"),
        )
        # Fire and do not await. Caller gets 'submitted' state immediately.
        # Store strong reference to prevent garbage collection mid-execution (RUF006)
        bg_task = asyncio.create_task(self._execute(task["id"], ctx))
        if not hasattr(self, "_bg_tasks"):
            self._bg_tasks = set()
        self._bg_tasks.add(bg_task)
        bg_task.add_done_callback(self._bg_tasks.discard)
        return task

    async def get_task(self, task_id: str) -> Task:
        task = await self._store.get(task_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        return task

    async def cancel_task(self, task_id: str) -> Task:
        task = await self._store.get(task_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        if task["status"]["state"] in TERMINAL_STATES:
            raise TaskNotCancelableError(task_id, task["status"]["state"])
        return await self._store.update_status(task_id, "canceled")

    async def list_tasks(
        self,
        *,
        context_id: str | None = None,
        state: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[Task], str | None]:
        return await self._store.list(
            context_id=context_id,
            state=state,  # type: ignore[arg-type]
            limit=limit,
            cursor=cursor,
        )

    # ── Execution ─────────────────────────────────────────────────────────────

    async def _execute(self, task_id: str, ctx: RequestContext) -> None:
        """
        Full task lifecycle. Always runs in an asyncio.Task (background).
        Guarantees the task ends in a terminal state, no matter what.
        """
        async with self._semaphore:
            try:
                await self._store.update_status(task_id, "working")
                artifacts = await asyncio.wait_for(
                    self._call_skill(ctx),
                    timeout=self._timeout,
                )
                for artifact in artifacts:
                    await self._store.add_artifact(task_id, artifact)
                await self._store.update_status(task_id, "completed")

            except TimeoutError:
                log.warning("Task %s timed out after %.1fs", task_id, self._timeout)
                await self._store.update_status(task_id, "failed")
            except UnsupportedOperationError:
                log.warning("Task %s rejected — unknown skill %r", task_id, ctx.skill_id)
                await self._store.update_status(task_id, "rejected")
            except A2AError as exc:
                log.warning("Task %s failed with A2AError: %s", task_id, exc)
                await self._store.update_status(task_id, "failed")
            except Exception:
                # FIX B4: catch-all MUST log; silent swallow was a debugging nightmare.
                log.exception("Task %s failed with unexpected error", task_id)
                await self._store.update_status(task_id, "failed")

    async def _call_skill(self, ctx: RequestContext) -> list[Artifact]:
        skill = self._skill_map.get(ctx.skill_id or "")
        if skill is None:
            raise UnsupportedOperationError(
                f"Unknown skill: {ctx.skill_id!r}. Available: {list(self._skill_map)}"
            )
        payload = ctx.extract_payload()
        response = await self._adapter.call(
            self._app,
            skill["endpoint"],
            payload,
            ctx.auth_headers,
        )
        artifact: Artifact = {
            "artifactId": str(uuid.uuid4()),
            "name": f"{skill['name']} result",
            "parts": [{"kind": "data", "data": response}],
        }
        return [artifact]
