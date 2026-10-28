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
    InvalidStateTransitionError,
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
        max_queue_size: int | None = None,
    ) -> None:
        self._app = app
        self._adapter = adapter
        self._store = store
        self._skill_map = {s["id"]: s for s in skills}
        self._timeout = timeout_seconds
        self._max_concurrency = max(1, max_concurrency)
        self._max_queue_size = (
            max(0, max_queue_size) if max_queue_size is not None else self._max_concurrency
        )
        self._max_capacity = self._max_concurrency + self._max_queue_size
        self._semaphore = asyncio.Semaphore(self._max_concurrency)
        self._bg_tasks: set[asyncio.Task[Any]] = set()
        self._active_tasks: dict[str, asyncio.Task[Any]] = {}

    @property
    def max_concurrency(self) -> int:
        return self._max_concurrency

    @property
    def max_queue_size(self) -> int:
        return self._max_queue_size

    @property
    def max_capacity(self) -> int:
        return self._max_capacity

    @property
    def active_tasks(self) -> dict[str, asyncio.Task[Any]]:
        """Return a snapshot mapping of task_id to active asyncio.Task instances."""
        return dict(self._active_tasks)

    def get_active_task(self, task_id: str) -> asyncio.Task[Any] | None:
        """Return the active asyncio.Task for a task_id if currently scheduled or running."""
        return self._active_tasks.get(task_id)

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
        if len(self._bg_tasks) >= self._max_capacity:
            raise UnsupportedOperationError("Task queue overloaded. Please try again later.")

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
        self._bg_tasks.add(bg_task)
        self._active_tasks[task["id"]] = bg_task

        def _cleanup(t: asyncio.Task[Any]) -> None:
            self._bg_tasks.discard(t)
            self._active_tasks.pop(task["id"], None)

        bg_task.add_done_callback(_cleanup)
        return task

    async def get_task(self, task_id: str) -> Task:
        task = await self._store.get(task_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        return task

    async def cancel_task(self, task_id: str) -> Task:
        """
        Cancel a running or queued task.

        Updates the task state in TaskStore to 'canceled' and cancels the
        underlying asyncio.Task if active.

        Note on non-cooperative tasks: Cancellation relies on standard cooperative
        asyncio cancellation (yielding via await points). If an endpoint executes
        blocking synchronous calls without yielding or catches BaseException to suppress
        asyncio.CancelledError, the coroutine cannot be forcefully terminated by asyncio.
        However, TaskManager guarantees that its stored state remains 'canceled' and any
        subsequent artifacts or completion transitions from that coroutine are discarded.
        """
        task = await self._store.get(task_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        if task["status"]["state"] in TERMINAL_STATES:
            raise TaskNotCancelableError(task_id, task["status"]["state"])

        try:
            task = await self._store.update_status(task_id, "canceled")
        except InvalidStateTransitionError:
            latest = await self._store.get(task_id)
            if latest is not None and latest["status"]["state"] in TERMINAL_STATES:
                raise TaskNotCancelableError(task_id, latest["status"]["state"]) from None
            raise

        bg_task = self._active_tasks.get(task_id)
        if bg_task is not None and not bg_task.done():
            bg_task.cancel()

        return task

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
        Prevents overwriting terminal states (e.g. 'canceled').
        """
        try:
            async with self._semaphore:
                # 1. Check if task was already canceled while queued waiting for semaphore
                current = await self._store.get(task_id)
                if current is not None and current["status"]["state"] in TERMINAL_STATES:
                    return

                try:
                    await self._store.update_status(task_id, "working")
                    artifacts = await asyncio.wait_for(
                        self._call_skill(ctx),
                        timeout=self._timeout,
                    )

                    # 2. Check if task was canceled while executing the skill
                    current = await self._store.get(task_id)
                    if current is not None and current["status"]["state"] in TERMINAL_STATES:
                        return

                    for artifact in artifacts:
                        current = await self._store.get(task_id)
                        if current is not None and current["status"]["state"] in TERMINAL_STATES:
                            return
                        await self._store.add_artifact(task_id, artifact)

                    current = await self._store.get(task_id)
                    if current is not None and current["status"]["state"] in TERMINAL_STATES:
                        return
                    await self._store.update_status(task_id, "completed")

                except asyncio.CancelledError:
                    log.info("Task %s execution canceled", task_id)
                    raise

                except TimeoutError:
                    current = await self._store.get(task_id)
                    if current is not None and current["status"]["state"] in TERMINAL_STATES:
                        return
                    log.warning("Task %s timed out after %.1fs", task_id, self._timeout)
                    await self._store.update_status(task_id, "failed")

                except UnsupportedOperationError:
                    current = await self._store.get(task_id)
                    if current is not None and current["status"]["state"] in TERMINAL_STATES:
                        return
                    log.warning("Task %s rejected — unknown skill %r", task_id, ctx.skill_id)
                    await self._store.update_status(task_id, "rejected")

                except A2AError as exc:
                    current = await self._store.get(task_id)
                    if current is not None and current["status"]["state"] in TERMINAL_STATES:
                        return
                    log.warning("Task %s failed with A2AError: %s", task_id, exc)
                    await self._store.update_status(task_id, "failed")

                except Exception as exc:
                    current = await self._store.get(task_id)
                    if current is not None and current["status"]["state"] in TERMINAL_STATES:
                        return
                    # FIX B4: catch-all MUST log; silent swallow was a debugging nightmare.
                    log.exception("Task %s failed with unexpected error", task_id)
                    error_artifact: Artifact = {
                        "artifactId": str(uuid.uuid4()),
                        "name": "Internal Error",
                        "parts": [
                            {"kind": "text", "text": "Task failed due to an internal system error."}
                        ],
                        "metadata": {"errorType": type(exc).__name__},
                    }
                    with contextlib.suppress(Exception):
                        await self._store.add_artifact(task_id, error_artifact)
                    with contextlib.suppress(Exception):
                        await self._store.update_status(task_id, "failed")
        finally:
            self._active_tasks.pop(task_id, None)
            current_t = asyncio.current_task()
            if current_t is not None:
                self._bg_tasks.discard(current_t)

    async def _call_skill(self, ctx: RequestContext) -> list[Artifact]:
        skill = self._skill_map.get(ctx.skill_id or "")
        if skill is None:
            raise UnsupportedOperationError(
                f"Unknown skill: {ctx.skill_id!r}. Available: {list(self._skill_map)}"
            )
        payload = ctx.extract_payload()
        endpoint = skill.get("endpoint")
        if endpoint is None:
            raise UnsupportedOperationError(f"Skill {ctx.skill_id!r} has no 'endpoint' defined.")
        response = await self._adapter.call(
            self._app,
            endpoint,
            payload,
            ctx.auth_headers,
        )
        artifact: Artifact = {
            "artifactId": str(uuid.uuid4()),
            "name": f"{skill['name']} result",
            "parts": [{"kind": "data", "data": response}],
        }
        return [artifact]
