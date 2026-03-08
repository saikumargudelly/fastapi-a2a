"""
InMemoryTaskStore.

Suitable for: development, tests, single-process production.
Not suitable for: multiple processes or instances sharing state.

Thread-safety: asyncio.Lock per task_id.
Eviction: TTL-based background loop (default 1 hour).
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import defaultdict

from fastapi_a2a.stores.base import TaskStore
from fastapi_a2a._internal.schema import Artifact, Message, Task, TaskStatus
from fastapi_a2a._internal.constants import VALID_TRANSITIONS, TERMINAL_STATES, TaskState
from fastapi_a2a._internal.exceptions import (
    InvalidStateTransitionError,
    TaskNotFoundError,
)
from fastapi_a2a._internal.utils import utcnow  # FIX F1: shared utility

log = logging.getLogger(__name__)


class InMemoryTaskStore(TaskStore):

    def __init__(self, ttl_seconds: int = 3600) -> None:
        self._tasks: dict[str, Task] = {}
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._expires: dict[str, float] = {}
        self._ttl = ttl_seconds
        self._evictor: asyncio.Task | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start TTL eviction loop. Called by FastApiA2A on app startup."""
        self._evictor = asyncio.create_task(self._evict_loop())

    async def stop(self) -> None:
        """Cancel eviction loop. Called by FastApiA2A on app shutdown."""
        if self._evictor:
            self._evictor.cancel()
            try:
                await self._evictor
            except asyncio.CancelledError:
                pass

    async def _evict_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            now = time.monotonic()
            stale = [tid for tid, exp in list(self._expires.items()) if exp < now]
            for tid in stale:
                self._tasks.pop(tid, None)
                self._locks.pop(tid, None)
                self._expires.pop(tid, None)
            if stale:
                log.debug("InMemoryTaskStore evicted %d expired tasks", len(stale))

    # ── TaskStore ABC ─────────────────────────────────────────────────────────

    async def create(self, context_id: str, message: Message) -> Task:
        now = utcnow()
        task_id = str(uuid.uuid4())
        task: Task = {
            "id": task_id,
            "contextId": context_id,
            "kind": "task",
            "status": {"state": "submitted", "timestamp": now},
            "history": [message],
            "artifacts": [],
            "createdAt": now,
            "updatedAt": now,
        }
        self._tasks[task_id] = task
        self._expires[task_id] = time.monotonic() + self._ttl
        return task

    async def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    async def update_status(
        self,
        task_id: str,
        state: TaskState,
        message: Message | None = None,
    ) -> Task:
        async with self._locks[task_id]:
            task = self._tasks.get(task_id)
            if task is None:
                raise TaskNotFoundError(task_id)
            current = task["status"]["state"]
            if state not in VALID_TRANSITIONS.get(current, frozenset()):
                raise InvalidStateTransitionError(current, state)
            now = utcnow()
            new_status: TaskStatus = {"state": state, "timestamp": now}
            if message is not None:
                new_status["message"] = message
            task["status"] = new_status
            # FIX B5: append status message to full history so no message is ever lost
            if message is not None:
                task["history"].append(message)
            task["updatedAt"] = now
            self._expires[task_id] = time.monotonic() + self._ttl
            return task

    async def add_artifact(self, task_id: str, artifact: Artifact) -> Task:
        async with self._locks[task_id]:
            task = self._tasks.get(task_id)
            if task is None:
                raise TaskNotFoundError(task_id)
            # FIX B6: guard against appending to a dead task
            current = task["status"]["state"]
            if current in TERMINAL_STATES:
                raise InvalidStateTransitionError(current, "artifact-updated")
            task["artifacts"].append(artifact)
            task["updatedAt"] = utcnow()
            return task

    async def list(
        self,
        *,
        context_id: str | None = None,
        state: TaskState | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[Task], str | None]:
        tasks = list(self._tasks.values())
        if context_id:
            tasks = [t for t in tasks if t["contextId"] == context_id]
        if state:
            tasks = [t for t in tasks if t["status"]["state"] == state]
        # ISO 8601 UTC strings sort lexicographically — safe because utcnow()
        # always produces the same fixed format.
        tasks.sort(key=lambda t: t["createdAt"])
        if cursor and cursor in [t["id"] for t in tasks]:
            idx = next(i for i, t in enumerate(tasks) if t["id"] == cursor)
            tasks = tasks[idx + 1:]
        page = tasks[:limit]
        next_cur = page[-1]["id"] if len(tasks) > limit else None
        return page, next_cur
