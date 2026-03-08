"""Tests for InMemoryTaskStore — all B5/B6/F1 fixes verified."""
from __future__ import annotations

import asyncio
import uuid

import pytest

from fastapi_a2a._internal.exceptions import (
    InvalidStateTransitionError,
    TaskNotFoundError,
)
from fastapi_a2a._internal.utils import utcnow
from fastapi_a2a.stores.memory import InMemoryTaskStore


def _make_message(text: str = "hello") -> dict:
    return {
        "role": "user",
        "kind": "message",
        "parts": [{"kind": "text", "text": text}],
        "messageId": str(uuid.uuid4()),
    }


@pytest.mark.asyncio
async def test_create_returns_submitted_task() -> None:
    store = InMemoryTaskStore()
    msg = _make_message()
    task = await store.create("ctx-1", msg)
    assert task["status"]["state"] == "submitted"
    assert task["contextId"] == "ctx-1"
    assert len(task["history"]) == 1
    assert task["history"][0] is msg


@pytest.mark.asyncio
async def test_get_returns_none_for_unknown() -> None:
    store = InMemoryTaskStore()
    result = await store.get("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_get_returns_existing_task() -> None:
    store = InMemoryTaskStore()
    task = await store.create("ctx", _make_message())
    fetched = await store.get(task["id"])
    assert fetched is not None
    assert fetched["id"] == task["id"]


@pytest.mark.asyncio
async def test_update_status_valid_transition() -> None:
    store = InMemoryTaskStore()
    task = await store.create("ctx", _make_message())
    updated = await store.update_status(task["id"], "working")
    assert updated["status"]["state"] == "working"


@pytest.mark.asyncio
async def test_update_status_invalid_transition_raises() -> None:
    store = InMemoryTaskStore()
    task = await store.create("ctx", _make_message())
    # submitted → working (valid)
    await store.update_status(task["id"], "working")
    # working → completed (valid)
    await store.update_status(task["id"], "completed")
    # completed → anything (invalid — terminal state)
    with pytest.raises(InvalidStateTransitionError):
        await store.update_status(task["id"], "working")


@pytest.mark.asyncio
async def test_update_status_unknown_task_raises() -> None:
    store = InMemoryTaskStore()
    with pytest.raises(TaskNotFoundError):
        await store.update_status("bad-id", "working")


@pytest.mark.asyncio
async def test_fix_b5_message_appended_to_history() -> None:
    """FIX B5: update_status with message must append it to history."""
    store = InMemoryTaskStore()
    task = await store.create("ctx", _make_message("original"))
    follow_up = _make_message("follow-up")
    updated = await store.update_status(task["id"], "working", message=follow_up)
    assert len(updated["history"]) == 2
    assert updated["history"][1] is follow_up


@pytest.mark.asyncio
async def test_update_status_without_message_history_unchanged() -> None:
    store = InMemoryTaskStore()
    task = await store.create("ctx", _make_message())
    updated = await store.update_status(task["id"], "working")
    assert len(updated["history"]) == 1


@pytest.mark.asyncio
async def test_add_artifact_success() -> None:
    store = InMemoryTaskStore()
    task = await store.create("ctx", _make_message())
    await store.update_status(task["id"], "working")
    artifact = {
        "artifactId": str(uuid.uuid4()),
        "parts": [{"kind": "data", "data": {"result": 42}}],
    }
    updated = await store.add_artifact(task["id"], artifact)
    assert len(updated["artifacts"]) == 1


@pytest.mark.asyncio
async def test_fix_b6_add_artifact_to_terminal_task_raises() -> None:
    """FIX B6: add_artifact must raise on terminal tasks."""
    store = InMemoryTaskStore()
    task = await store.create("ctx", _make_message())
    await store.update_status(task["id"], "working")
    await store.update_status(task["id"], "completed")
    artifact = {
        "artifactId": str(uuid.uuid4()),
        "parts": [{"kind": "data", "data": {}}],
    }
    with pytest.raises(InvalidStateTransitionError):
        await store.add_artifact(task["id"], artifact)


@pytest.mark.asyncio
async def test_add_artifact_unknown_task_raises() -> None:
    store = InMemoryTaskStore()
    with pytest.raises(TaskNotFoundError):
        await store.add_artifact("bad", {"artifactId": "x", "parts": []})


@pytest.mark.asyncio
async def test_list_all_tasks() -> None:
    store = InMemoryTaskStore()
    for _ in range(3):
        await store.create("ctx", _make_message())
    tasks, cursor = await store.list()
    assert len(tasks) == 3
    assert cursor is None


@pytest.mark.asyncio
async def test_list_filter_by_state() -> None:
    store = InMemoryTaskStore()
    t1 = await store.create("ctx", _make_message())
    await store.create("ctx", _make_message())
    await store.update_status(t1["id"], "working")
    working, _ = await store.list(state="working")
    assert len(working) == 1
    assert working[0]["id"] == t1["id"]


@pytest.mark.asyncio
async def test_list_pagination() -> None:
    store = InMemoryTaskStore()
    for _ in range(5):
        await store.create("ctx", _make_message())
    page1, cursor = await store.list(limit=3)
    assert len(page1) == 3
    assert cursor is not None
    page2, cursor2 = await store.list(limit=5, cursor=cursor)
    assert len(page2) == 2
    assert cursor2 is None


@pytest.mark.asyncio
async def test_list_filter_by_context_id() -> None:
    store = InMemoryTaskStore()
    await store.create("ctx-A", _make_message())
    await store.create("ctx-A", _make_message())
    await store.create("ctx-B", _make_message())
    results, _ = await store.list(context_id="ctx-A")
    assert len(results) == 2
    assert all(t["contextId"] == "ctx-A" for t in results)


@pytest.mark.asyncio
async def test_concurrent_updates_safety() -> None:
    """Concurrent updates to different tasks must not interfere."""
    store = InMemoryTaskStore()
    tasks = [await store.create("ctx", _make_message()) for _ in range(10)]
    await asyncio.gather(*[
        store.update_status(t["id"], "working") for t in tasks
    ])
    for t in tasks:
        fetched = await store.get(t["id"])
        assert fetched["status"]["state"] == "working"


@pytest.mark.asyncio
async def test_fix_f1_utcnow_format() -> None:
    """FIX F1: utcnow() should produce millisecond-precision ISO 8601 UTC."""
    ts = utcnow()
    assert ts.endswith("Z")
    assert len(ts) == 24  # '2024-01-15T10:30:00.123Z'


@pytest.mark.asyncio
async def test_store_lifecycle_start_stop() -> None:
    """start() begins the eviction loop; stop() cancels it cleanly."""
    store = InMemoryTaskStore(ttl_seconds=1)
    await store.start()
    assert store._evictor is not None
    assert not store._evictor.done()
    await store.stop()
    assert store._evictor.done()
