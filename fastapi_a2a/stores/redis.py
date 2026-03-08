"""RedisTaskStore — distributed task store backed by redis.asyncio.  # pragma: no cover

Requires the [redis] optional extra: pip install "fastapi-a2a[redis]"
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fastapi_a2a.exceptions import InvalidStateTransitionError, TaskNotFoundError
from fastapi_a2a.schema import (
    VALID_TRANSITIONS,
    Artifact,
    Message,
    Task,
    TaskState,
    TaskStatus,
    task_ta,
)
from fastapi_a2a.taskstore import TaskStore

if TYPE_CHECKING:
    import redis.asyncio as aioredis

try:
    import redis.asyncio as _aioredis
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "RedisTaskStore requires the [redis] extra. "
        "Install with: pip install 'fastapi-a2a[redis]'"
    ) from exc


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class RedisTaskStore(TaskStore):
    """
    Distributed task store backed by Redis.

    Tasks are stored as JSON strings with EXPIRE TTL.
    Suitable for multi-process / multi-worker deployments.
    """

    def __init__(self, url: str = "redis://localhost:6379", ttl_seconds: int = 3600) -> None:
        self._url = url
        self._ttl = ttl_seconds
        self._redis: aioredis.Redis[Any] | None = None

    async def _get_redis(self) -> aioredis.Redis[Any]:
        if self._redis is None:
            self._redis = await _aioredis.from_url(  # type: ignore[assignment]
                self._url, decode_responses=True
            )
        return self._redis

    def _key(self, task_id: str) -> str:
        return f"fastapi_a2a:task:{task_id}"

    async def get(self, task_id: str) -> Task | None:
        r = await self._get_redis()
        raw = await r.get(self._key(task_id))
        if raw is None:
            return None
        return task_ta.validate_json(raw)

    async def save(self, task: Task) -> None:
        r = await self._get_redis()
        raw = task_ta.dump_json(task, by_alias=True)
        await r.set(self._key(task.id), raw, ex=self._ttl)

    async def update_status(
        self,
        task_id: str,
        state: TaskState,
        message: Message | None = None,
    ) -> Task:
        r = await self._get_redis()
        key = self._key(task_id)
        async with r.pipeline(transaction=True) as pipe:
            raw = await r.get(key)
            if raw is None:
                raise TaskNotFoundError(f"Task not found: {task_id}")
            task = task_ta.validate_json(raw)
            allowed = VALID_TRANSITIONS.get(task.status.state, frozenset())
            if state not in allowed:
                raise InvalidStateTransitionError(
                    f"Cannot transition {task.status.state!r} → {state!r}"
                )
            now = _now_iso()
            updated = task.model_copy(
                update={
                    "status": TaskStatus(state=state, message=message, timestamp=now),
                    "updated_at": now,
                }
            )
            pipe.set(key, task_ta.dump_json(updated, by_alias=True), ex=self._ttl)
            await pipe.execute()
        return updated

    async def add_artifact(self, task_id: str, artifact: Artifact) -> Task:
        r = await self._get_redis()
        key = self._key(task_id)
        raw = await r.get(key)
        if raw is None:
            raise TaskNotFoundError(f"Task not found: {task_id}")
        task = task_ta.validate_json(raw)
        now = _now_iso()
        updated = task.model_copy(
            update={
                "artifacts": [*task.artifacts, artifact],
                "updated_at": now,
            }
        )
        await r.set(key, task_ta.dump_json(updated, by_alias=True), ex=self._ttl)
        return updated

    async def list(
        self,
        context_id: str | None = None,
        state: TaskState | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[Task], str | None]:
        r = await self._get_redis()
        pattern = "fastapi_a2a:task:*"
        keys: list[str] = []
        async for key in r.scan_iter(pattern):
            keys.append(str(key))
        tasks: list[Task] = []
        for key in keys:
            raw = await r.get(key)
            if raw:
                task = task_ta.validate_json(raw)
                if context_id is not None and task.context_id != context_id:
                    continue
                if state is not None and task.status.state != state:
                    continue
                tasks.append(task)
        # cursor-based pagination
        if cursor is not None:
            ids = [t.id for t in tasks]
            try:
                start = ids.index(cursor) + 1
                tasks = tasks[start:]
            except ValueError:
                pass
        page = tasks[:limit]
        next_cursor = page[-1].id if len(tasks) > limit else None
        return page, next_cursor

    async def aclose(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
