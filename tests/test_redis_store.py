"""
Tests for RedisTaskStore.

Tests both:
1. In-memory Mock Redis unit tests (creation, retrieval, status updates,
   artifact handling, listing, lifecycle cleanup, setup_fastapi_a2a integration).
2. Live Redis integration tests (automatically skipped if no live Redis is reachable).
"""

from __future__ import annotations

import fnmatch

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from fastapi_a2a._internal.exceptions import (
    InvalidStateTransitionError,
    TaskNotFoundError,
)
from fastapi_a2a._internal.schema import Artifact, Message
from fastapi_a2a.plugin import setup_fastapi_a2a
from fastapi_a2a.stores.redis import RedisTaskStore


class MockRedis:
    """In-memory async mock adhering to the redis.asyncio.Redis subset used by RedisTaskStore."""

    def __init__(self) -> None:
        self._data: dict[str, str | bytes] = {}
        self._ttls: dict[str, int] = {}
        self.closed = False

    async def get(self, key: str) -> str | bytes | None:
        if self.closed:
            raise RuntimeError("Cannot use closed Redis client")
        return self._data.get(key)

    async def set(self, key: str, value: str | bytes, ex: int | None = None) -> bool:
        if self.closed:
            raise RuntimeError("Cannot use closed Redis client")
        self._data[key] = value
        if ex is not None:
            self._ttls[key] = ex
        return True

    async def scan_iter(self, match: str = "*"):
        if self.closed:
            raise RuntimeError("Cannot use closed Redis client")
        for key in list(self._data.keys()):
            if fnmatch.fnmatch(key, match):
                yield key

    async def aclose(self) -> None:
        self.closed = True


def make_message(text: str = "test message") -> Message:
    return {
        "role": "user",
        "kind": "message",
        "messageId": "msg-1",
        "parts": [{"kind": "text", "text": text}],
    }


def make_artifact(
    name: str = "out.txt",
    text: str = "result",
    artifact_id: str = "art-1",
) -> Artifact:
    return {
        "artifactId": artifact_id,
        "name": name,
        "parts": [{"kind": "text", "text": text}],
    }


@pytest.fixture
def mock_redis() -> MockRedis:
    return MockRedis()


@pytest.fixture
def store(mock_redis: MockRedis) -> RedisTaskStore:
    return RedisTaskStore(client=mock_redis)


# ── Instantiation Tests ────────────────────────────────────────────────────────


def test_redis_store_instantiation_variants(mock_redis: MockRedis) -> None:
    # 1. Default constructor
    s1 = RedisTaskStore()
    assert s1._url == "redis://localhost:6379"
    assert s1._redis is None

    # 2. String URL
    s2 = RedisTaskStore("redis://custom:6379", ttl_seconds=120)
    assert s2._url == "redis://custom:6379"
    assert s2._ttl == 120

    # 3. Client object as positional argument (documented in README.md)
    s3 = RedisTaskStore(mock_redis)
    assert s3._redis is mock_redis

    # 4. Client object as keyword argument
    s4 = RedisTaskStore(client=mock_redis)
    assert s4._redis is mock_redis


# ── CRUD & Contract Tests (Mocked) ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_redis_create_and_get(store: RedisTaskStore, mock_redis: MockRedis) -> None:
    msg = make_message("hello agent")
    task = await store.create(context_id="ctx-123", message=msg)

    assert task["id"]
    assert task["contextId"] == "ctx-123"
    assert task["kind"] == "task"
    assert task["status"]["state"] == "submitted"
    assert task["history"] == [msg]
    assert task["artifacts"] == []
    assert task["createdAt"].endswith("Z")
    assert task["updatedAt"] == task["createdAt"]

    # Verify underlying Redis key
    key = f"fastapi_a2a:task:{task['id']}"
    assert key in mock_redis._data
    assert mock_redis._ttls[key] == 3600

    # Fetch back
    fetched = await store.get(task["id"])
    assert fetched is not None
    assert fetched["id"] == task["id"]
    assert fetched["contextId"] == "ctx-123"


@pytest.mark.asyncio
async def test_redis_get_unknown_returns_none(store: RedisTaskStore) -> None:
    result = await store.get("non-existent-uuid")
    assert result is None


@pytest.mark.asyncio
async def test_redis_update_status_transitions(store: RedisTaskStore) -> None:
    msg1 = make_message("start")
    task = await store.create("ctx-1", msg1)
    tid = task["id"]

    # submitted -> working
    msg2 = make_message("in progress")
    updated = await store.update_status(tid, "working", msg2)
    assert updated["status"]["state"] == "working"
    assert updated["status"].get("message") == msg2
    assert len(updated["history"]) == 2
    assert updated["history"][1] == msg2

    # working -> completed
    msg3 = make_message("finished")
    completed = await store.update_status(tid, "completed", msg3)
    assert completed["status"]["state"] == "completed"
    assert len(completed["history"]) == 3

    # illegal transition: completed -> working
    with pytest.raises(InvalidStateTransitionError):
        await store.update_status(tid, "working")


@pytest.mark.asyncio
async def test_redis_update_status_unknown_raises(store: RedisTaskStore) -> None:
    with pytest.raises(TaskNotFoundError):
        await store.update_status("missing-id", "working")


@pytest.mark.asyncio
async def test_redis_add_artifact(store: RedisTaskStore) -> None:
    task = await store.create("ctx-1", make_message("do work"))
    tid = task["id"]

    artifact = make_artifact("data.csv", "a,b,c")
    updated = await store.add_artifact(tid, artifact)
    assert len(updated["artifacts"]) == 1
    assert updated["artifacts"][0].get("name") == "data.csv"

    # Terminal state forbids artifacts
    await store.update_status(tid, "working")
    await store.update_status(tid, "completed")
    with pytest.raises(InvalidStateTransitionError):
        await store.add_artifact(tid, make_artifact("extra.txt", "fail"))


@pytest.mark.asyncio
async def test_redis_add_artifact_unknown_raises(store: RedisTaskStore) -> None:
    with pytest.raises(TaskNotFoundError):
        await store.add_artifact("missing-id", make_artifact())


@pytest.mark.asyncio
async def test_redis_list_filtering_and_pagination(store: RedisTaskStore) -> None:
    # Create 3 tasks with slight created time sequencing
    t1 = await store.create("ctx-A", make_message("1"))
    t2 = await store.create("ctx-B", make_message("2"))
    t3 = await store.create("ctx-A", make_message("3"))

    await store.update_status(t2["id"], "working")

    # List all
    tasks, cursor = await store.list()
    assert len(tasks) == 3
    assert cursor is None
    # Verified ordered by createdAt ascending
    assert [t["id"] for t in tasks] == [t1["id"], t2["id"], t3["id"]]

    # Filter by contextId
    ctx_a_tasks, _ = await store.list(context_id="ctx-A")
    assert len(ctx_a_tasks) == 2
    assert {t["id"] for t in ctx_a_tasks} == {t1["id"], t3["id"]}

    # Filter by state
    working_tasks, _ = await store.list(state="working")
    assert len(working_tasks) == 1
    assert working_tasks[0]["id"] == t2["id"]

    # Pagination with limit
    p1, cur1 = await store.list(limit=2)
    assert len(p1) == 2
    assert cur1 == p1[-1]["id"]

    p2, cur2 = await store.list(limit=2, cursor=cur1)
    assert len(p2) == 1
    assert cur2 is None
    assert p2[0]["id"] == t3["id"]


# ── Lifecycle & Cleanup Tests ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_redis_lifecycle_start_and_stop(mock_redis: MockRedis) -> None:
    store = RedisTaskStore(client=mock_redis)
    assert not mock_redis.closed

    await store.start()
    assert not mock_redis.closed

    await store.stop()
    assert mock_redis.closed
    assert store._redis is None

    # Repeated stop is safe no-op
    await store.stop()


# ── Integration with setup_fastapi_a2a ───────────────────────────────────────


def test_redis_store_injection_into_fastapi_a2a(mock_redis: MockRedis) -> None:
    store = RedisTaskStore(client=mock_redis)
    app = FastAPI()

    _ = setup_fastapi_a2a(
        app,
        name="RedisAgent",
        url="https://agent.example.com",
        store=store,
    )

    with TestClient(app) as client:
        # Agent card endpoint responds
        res = client.get("/.well-known/agent.json")
        assert res.status_code == 200

        # Send an RPC message to create a task in RedisTaskStore
        rpc_payload = {
            "jsonrpc": "2.0",
            "id": "req-1",
            "method": "message/send",
            "params": {
                "message": {
                    "role": "user",
                    "kind": "message",
                    "messageId": "msg-100",
                    "parts": [{"kind": "text", "text": "hello"}],
                }
            },
        }
        res2 = client.post("/a2a/rpc", json=rpc_payload)
        assert res2.status_code == 200
        body = res2.json()
        assert "result" in body
        task_id = body["result"]["id"]

        # Verify task exists in mock_redis
        assert f"fastapi_a2a:task:{task_id}" in mock_redis._data

    # On client exit (app shutdown), store.stop() closes Redis
    assert mock_redis.closed


# ── Live Redis Integration Tests (Conditional) ───────────────────────────────


def _live_redis_available() -> bool:
    try:
        import redis

        r = redis.Redis.from_url("redis://localhost:6379", socket_timeout=0.2)
        return bool(r.ping())
    except Exception:
        return False


@pytest.mark.skipif(not _live_redis_available(), reason="Live Redis server is not available")
@pytest.mark.asyncio
async def test_live_redis_store_integration() -> None:
    """Integration test against a live Redis server at redis://localhost:6379."""
    store = RedisTaskStore("redis://localhost:6379", ttl_seconds=30)
    await store.start()

    task = await store.create("live-ctx", make_message("live test"))
    fetched = await store.get(task["id"])
    assert fetched is not None
    assert fetched["id"] == task["id"]

    updated = await store.update_status(task["id"], "working")
    assert updated["status"]["state"] == "working"

    await store.stop()
