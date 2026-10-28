# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false
"""RedisTaskStore — distributed task store backed by redis.asyncio.

Requires the [redis] optional extra: pip install "fastapi-a2a[redis]"
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from fastapi_a2a._internal.constants import TERMINAL_STATES, VALID_TRANSITIONS, TaskState
from fastapi_a2a._internal.exceptions import InvalidStateTransitionError, TaskNotFoundError
from fastapi_a2a._internal.schema import (
    Artifact,
    Message,
    Task,
    TaskStatus,
    task_adapter,
)
from fastapi_a2a._internal.utils import utcnow
from fastapi_a2a.stores.base import TaskStore

if TYPE_CHECKING:
    import redis.asyncio as aioredis

try:
    import redis.asyncio as _aioredis
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "RedisTaskStore requires the [redis] extra. Install with: pip install 'fastapi-a2a[redis]'"
    ) from exc


class RedisTaskStore(TaskStore):
    """
    Distributed task store backed by Redis.

    Tasks are stored as JSON strings with EXPIRE TTL.
    Suitable for multi-process / multi-worker deployments.
    """

    def __init__(
        self,
        url: str | aioredis.Redis | Any = "redis://localhost:6379",
        ttl_seconds: int = 3600,
        *,
        client: aioredis.Redis | Any = None,
    ) -> None:
        self._ttl = ttl_seconds
        self._redis: aioredis.Redis | None
        if client is not None:
            self._redis = client
            self._url = url if isinstance(url, str) else "redis://localhost:6379"
        elif isinstance(url, str):
            self._redis = None
            self._url = url
        else:
            self._redis = url
            self._url = "redis://localhost:6379"

    async def _get_redis(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = await _aioredis.from_url(  # type: ignore[assignment]
                self._url, decode_responses=True
            )
        return self._redis

    def _key(self, task_id: str) -> str:
        return f"fastapi_a2a:task:{task_id}"

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Initialize Redis connection on application startup."""
        await self._get_redis()

    async def stop(self) -> None:
        """Close Redis connection and pool on application shutdown."""
        await self.aclose()

    async def aclose(self) -> None:
        """Close underlying Redis client and connection pool."""
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

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
        r = await self._get_redis()
        raw = task_adapter.dump_json(task, by_alias=True)
        await r.set(self._key(task_id), raw, ex=self._ttl)
        return task

    async def get(self, task_id: str) -> Task | None:
        r = await self._get_redis()
        raw = await r.get(self._key(task_id))
        if raw is None:
            return None
        return task_adapter.validate_json(raw)

    async def save(self, task: Task) -> None:
        r = await self._get_redis()
        raw = task_adapter.dump_json(task, by_alias=True)
        await r.set(self._key(task["id"]), raw, ex=self._ttl)

    async def update_status(
        self,
        task_id: str,
        state: TaskState,
        message: Message | None = None,
    ) -> Task:
        r = await self._get_redis()
        key = self._key(task_id)
        raw = await r.get(key)
        if raw is None:
            raise TaskNotFoundError(task_id)
        task = task_adapter.validate_json(raw)
        current = task["status"]["state"]
        allowed = VALID_TRANSITIONS.get(current, frozenset())
        if state not in allowed:
            raise InvalidStateTransitionError(current, state)
        now = utcnow()
        new_status: TaskStatus = {"state": state, "timestamp": now}
        if message is not None:
            new_status["message"] = message
        task["status"] = new_status
        if message is not None:
            task["history"].append(message)
        task["updatedAt"] = now
        await r.set(key, task_adapter.dump_json(task, by_alias=True), ex=self._ttl)
        return task

    async def add_artifact(self, task_id: str, artifact: Artifact) -> Task:
        r = await self._get_redis()
        key = self._key(task_id)
        raw = await r.get(key)
        if raw is None:
            raise TaskNotFoundError(task_id)
        task = task_adapter.validate_json(raw)
        current = task["status"]["state"]
        if current in TERMINAL_STATES:
            raise InvalidStateTransitionError(current, "artifact-updated")
        task["artifacts"].append(artifact)
        task["updatedAt"] = utcnow()
        await r.set(key, task_adapter.dump_json(task, by_alias=True), ex=self._ttl)
        return task

    async def list(
        self,
        *,
        context_id: str | None = None,
        state: TaskState | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[Task], str | None]:
        r = await self._get_redis()
        pattern = "fastapi_a2a:task:*"
        keys: list[str] = []
        async for key in r.scan_iter(pattern):
            keys.append(str(key) if not isinstance(key, str) else key)
        tasks: list[Task] = []
        for key in keys:
            raw = await r.get(key)
            if raw:
                task = task_adapter.validate_json(raw)
                if context_id is not None and task.get("contextId") != context_id:
                    continue
                if state is not None and task["status"]["state"] != state:
                    continue
                tasks.append(task)
        tasks.sort(key=lambda t: t["createdAt"])
        if cursor and cursor in [t["id"] for t in tasks]:
            idx = next(i for i, t in enumerate(tasks) if t["id"] == cursor)
            tasks = tasks[idx + 1 :]
        page = tasks[:limit]
        next_cursor = page[-1]["id"] if len(tasks) > limit else None
        return page, next_cursor
