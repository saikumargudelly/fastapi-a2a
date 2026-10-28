"""Deterministic regression tests for task cancellation propagation and lifecycle.

Verifies:
1. Cancellation stops cooperative coroutines during execution and releases semaphore slots.
2. Cancellation of queued tasks before execution prevents them from ever running.
3. Cancellation of already completed tasks returns TASK_NOT_CANCELABLE (-32002).
4. Repeated cancellation requests return TASK_NOT_CANCELABLE.
5. Non-cooperative tasks that suppress CancelledError cannot overwrite the 'canceled' state.
6. Cancelled tasks free up capacity immediately so subsequent tasks are accepted.
7. Active task mapping registers on creation and unregisters safely on finish/cancel.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from fastapi_a2a import FastApiA2A, a2a_skill
from fastapi_a2a._internal.constants import TASK_NOT_CANCELABLE, TASK_NOT_FOUND


def make_rpc_payload(skill_id: str, payload_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "kind": "message",
                "messageId": str(uuid.uuid4()),
                "parts": [{"kind": "data", "data": payload_data}],
                "metadata": {"skillId": skill_id},
            }
        },
    }


def make_cancel_payload(task_id: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "tasks/cancel",
        "params": {"id": task_id},
    }


async def poll_task_state(
    client: AsyncClient,
    task_id: str,
    target_state: str,
    deadline_seconds: float = 3.0,
) -> str:
    deadline = time.monotonic() + deadline_seconds
    last_state = ""
    req = {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "tasks/get",
        "params": {"id": task_id},
    }
    while time.monotonic() < deadline:
        res = await client.post("/a2a/rpc", json=req)
        last_state = res.json()["result"]["status"]["state"]
        if last_state == target_state:
            return last_state
        await asyncio.sleep(0.02)
    return last_state


@pytest.mark.asyncio
async def test_cancellation_during_execution_stops_cooperative_coroutine() -> None:
    """Canceling a running task interrupts cooperative execution and releases permits."""
    app = FastAPI()
    started_event = asyncio.Event()
    cancel_caught = False

    @app.post("/cooperative")
    @a2a_skill(description="Cooperative worker")
    async def cooperative(req: dict) -> dict:
        nonlocal cancel_caught
        started_event.set()
        try:
            await asyncio.sleep(10.0)
        except asyncio.CancelledError:
            cancel_caught = True
            raise
        return {"status": "finished"}

    a2a = FastApiA2A(app, name="Agent", url="https://x.com", max_concurrency=1, max_queue_size=5)
    a2a.mount()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Submit task
        send_resp = await client.post("/a2a/rpc", json=make_rpc_payload("cooperative", {}))
        task_id = send_resp.json()["result"]["id"]

        # Wait until task has acquired semaphore and entered skill
        await asyncio.wait_for(started_event.wait(), timeout=2.0)
        bg_task = a2a._manager.get_active_task(task_id)
        assert bg_task is not None
        assert not bg_task.done()

        # Cancel the task
        cancel_resp = await client.post("/a2a/rpc", json=make_cancel_payload(task_id))
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["result"]["status"]["state"] == "canceled"

        # Wait for the coroutine to complete cancellation
        deadline = time.monotonic() + 2.0
        while not bg_task.done() and time.monotonic() < deadline:
            await asyncio.sleep(0.02)

        assert bg_task.done()
        assert bg_task.cancelled()
        assert cancel_caught is True

        # Verify active task mapping and bg tasks are cleared
        assert a2a._manager.get_active_task(task_id) is None
        assert len(a2a._manager._bg_tasks) == 0

        # Verify stored state remains canceled
        state = await poll_task_state(client, task_id, "canceled")
        assert state == "canceled"


@pytest.mark.asyncio
async def test_cancellation_before_execution_starts_queued_task() -> None:
    """Canceling a task while waiting in queue prevents it from ever executing."""
    app = FastAPI()
    task1_hold = asyncio.Event()
    task2_ran = False

    @app.post("/first")
    @a2a_skill(description="First task occupying slot")
    async def first(req: dict) -> dict:
        await task1_hold.wait()
        return {"first": True}

    @app.post("/second")
    @a2a_skill(description="Second task queued")
    async def second(req: dict) -> dict:
        nonlocal task2_ran
        task2_ran = True
        return {"second": True}

    # Only 1 concurrent task allowed
    a2a = FastApiA2A(app, name="Agent", url="https://x.com", max_concurrency=1, max_queue_size=5)
    a2a.mount()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Submit Task 1 (acquires semaphore)
        resp1 = await client.post("/a2a/rpc", json=make_rpc_payload("first", {}))
        tid1 = resp1.json()["result"]["id"]

        # Submit Task 2 (queued, waiting for semaphore)
        resp2 = await client.post("/a2a/rpc", json=make_rpc_payload("second", {}))
        tid2 = resp2.json()["result"]["id"]

        # Cancel Task 2 while it is still waiting in queue
        cancel_resp = await client.post("/a2a/rpc", json=make_cancel_payload(tid2))
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["result"]["status"]["state"] == "canceled"

        # Release Task 1
        task1_hold.set()
        state1 = await poll_task_state(client, tid1, "completed")
        assert state1 == "completed"

        # Task 2 coroutine should never execute skill logic
        await asyncio.sleep(0.1)
        assert task2_ran is False

        # Task 2 store state must remain canceled
        state2 = await poll_task_state(client, tid2, "canceled")
        assert state2 == "canceled"

        # Active tasks mapping must be empty
        assert len(a2a._manager.active_tasks) == 0


@pytest.mark.asyncio
async def test_cancellation_after_task_completed_returns_not_cancelable() -> None:
    """Canceling a task that has already finished returns TASK_NOT_CANCELABLE (-32002)."""
    app = FastAPI()

    @app.post("/instant")
    @a2a_skill(description="Instant return")
    async def instant(req: dict) -> dict:
        return {"done": True}

    a2a = FastApiA2A(app, name="Agent", url="https://x.com", max_concurrency=2)
    a2a.mount()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        send_resp = await client.post("/a2a/rpc", json=make_rpc_payload("instant", {}))
        task_id = send_resp.json()["result"]["id"]

        state = await poll_task_state(client, task_id, "completed")
        assert state == "completed"

        # Try to cancel completed task
        cancel_resp = await client.post("/a2a/rpc", json=make_cancel_payload(task_id))
        assert cancel_resp.status_code == 200
        data = cancel_resp.json()
        assert "error" in data
        assert data["error"]["code"] == TASK_NOT_CANCELABLE

        # State remains completed
        state_after = await poll_task_state(client, task_id, "completed")
        assert state_after == "completed"


@pytest.mark.asyncio
async def test_repeated_cancellation_returns_not_cancelable() -> None:
    """The second cancellation request on an already-canceled task returns -32002."""
    app = FastAPI()
    hold_event = asyncio.Event()

    @app.post("/work")
    @a2a_skill(description="Hold work")
    async def work(req: dict) -> dict:
        await hold_event.wait()
        return {"done": True}

    a2a = FastApiA2A(app, name="Agent", url="https://x.com", max_concurrency=2)
    a2a.mount()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        send_resp = await client.post("/a2a/rpc", json=make_rpc_payload("work", {}))
        task_id = send_resp.json()["result"]["id"]

        # First cancellation succeeds
        cancel_resp1 = await client.post("/a2a/rpc", json=make_cancel_payload(task_id))
        assert cancel_resp1.status_code == 200
        assert cancel_resp1.json()["result"]["status"]["state"] == "canceled"

        # Second cancellation must fail with TASK_NOT_CANCELABLE
        cancel_resp2 = await client.post("/a2a/rpc", json=make_cancel_payload(task_id))
        assert cancel_resp2.status_code == 200
        data = cancel_resp2.json()
        assert "error" in data
        assert data["error"]["code"] == TASK_NOT_CANCELABLE

        hold_event.set()


@pytest.mark.asyncio
async def test_cancellation_unknown_task_returns_not_found() -> None:
    """Canceling a nonexistent task ID returns TASK_NOT_FOUND (-32001)."""
    app = FastAPI()
    a2a = FastApiA2A(app, name="Agent", url="https://x.com")
    a2a.mount()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        cancel_resp = await client.post("/a2a/rpc", json=make_cancel_payload("unknown-id"))
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["error"]["code"] == TASK_NOT_FOUND


@pytest.mark.asyncio
async def test_non_cooperative_task_cannot_overwrite_canceled_state() -> None:
    """A task that suppresses CancelledError cannot overwrite the canceled state
    or add artifacts.
    """
    app = FastAPI()
    started_event = asyncio.Event()
    release_event = asyncio.Event()

    @app.post("/stubborn")
    @a2a_skill(description="Suppresses cancellation")
    async def stubborn(req: dict) -> dict:
        started_event.set()
        try:
            await release_event.wait()
        except BaseException:
            # Deliberately suppress cancellation
            pass
        return {"secret": "should_be_dropped"}

    a2a = FastApiA2A(app, name="Agent", url="https://x.com", max_concurrency=1)
    a2a.mount()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        send_resp = await client.post("/a2a/rpc", json=make_rpc_payload("stubborn", {}))
        task_id = send_resp.json()["result"]["id"]

        await asyncio.wait_for(started_event.wait(), timeout=2.0)

        # Cancel the task
        cancel_resp = await client.post("/a2a/rpc", json=make_cancel_payload(task_id))
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["result"]["status"]["state"] == "canceled"

        # Now allow the stubborn skill to finish its suppressed execution
        release_event.set()
        await asyncio.sleep(0.1)

        # Verify state is still canceled, NOT completed
        get_resp = await client.post(
            "/a2a/rpc",
            json={"jsonrpc": "2.0", "id": "1", "method": "tasks/get", "params": {"id": task_id}},
        )
        task_data = get_resp.json()["result"]
        assert task_data["status"]["state"] == "canceled"
        # Output must be discarded (no artifacts added)
        assert len(task_data["artifacts"]) == 0

        # Mapping must be cleaned up
        assert a2a._manager.get_active_task(task_id) is None


@pytest.mark.asyncio
async def test_canceled_task_restores_queue_capacity_immediately() -> None:
    """Canceling a task frees capacity immediately so a subsequent task can be accepted."""
    app = FastAPI()
    hold_event = asyncio.Event()

    @app.post("/busy")
    @a2a_skill(description="Busy worker")
    async def busy(req: dict) -> dict:
        await hold_event.wait()
        return {"val": req.get("val")}

    # Capacity = 1 concurrency + 1 queue = 2
    a2a = FastApiA2A(app, name="Agent", url="https://x.com", max_concurrency=1, max_queue_size=1)
    a2a.mount()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Task 1 occupies concurrency
        resp1 = await client.post("/a2a/rpc", json=make_rpc_payload("busy", {"val": 1}))
        tid1 = resp1.json()["result"]["id"]

        # Task 2 occupies queue slot
        resp2 = await client.post("/a2a/rpc", json=make_rpc_payload("busy", {"val": 2}))
        tid2 = resp2.json()["result"]["id"]

        # Task 3 exceeds capacity -> rejected
        resp3 = await client.post("/a2a/rpc", json=make_rpc_payload("busy", {"val": 3}))
        assert "error" in resp3.json()
        assert "Task queue overloaded" in resp3.json()["error"]["message"]

        # Cancel Task 2
        cancel_resp = await client.post("/a2a/rpc", json=make_cancel_payload(tid2))
        assert cancel_resp.json()["result"]["status"]["state"] == "canceled"

        # Wait deterministically for Task 2's coroutine to finish unwinding
        active_t2 = a2a._manager.get_active_task(tid2)
        if active_t2 is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await active_t2

        # Now Task 4 should be accepted because Task 2 freed a slot!
        resp4 = await client.post("/a2a/rpc", json=make_rpc_payload("busy", {"val": 4}))
        assert "result" in resp4.json()
        assert resp4.json()["result"]["status"]["state"] == "submitted"
        tid4 = resp4.json()["result"]["id"]

        # Release hold
        hold_event.set()

        state1 = await poll_task_state(client, tid1, "completed")
        assert state1 == "completed"

        state4 = await poll_task_state(client, tid4, "completed")
        assert state4 == "completed"


@pytest.mark.asyncio
async def test_task_temporarily_suppressing_cancellation_retains_queue_capacity() -> None:
    """A task that suppresses or delays cancellation continues occupying capacity
    until it finishes.
    """
    app = FastAPI()
    started_event = asyncio.Event()
    cancel_received_event = asyncio.Event()
    release_cleanup_event = asyncio.Event()
    worker_finished_event = asyncio.Event()

    @app.post("/delayed-cleanup")
    @a2a_skill(description="Worker with delayed cancellation cleanup")
    async def delayed_cleanup(req: dict) -> dict:
        started_event.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancel_received_event.set()
            await release_cleanup_event.wait()
            raise
        finally:
            worker_finished_event.set()
        return {"ok": True}

    # Capacity = 1 concurrency + 1 queue = 2
    a2a = FastApiA2A(app, name="Agent", url="https://x.com", max_concurrency=1, max_queue_size=1)
    a2a.mount()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Task 1 occupies concurrency
        resp1 = await client.post("/a2a/rpc", json=make_rpc_payload("delayed-cleanup", {}))
        tid1 = resp1.json()["result"]["id"]
        await asyncio.wait_for(started_event.wait(), timeout=2.0)

        # Task 2 occupies the queue
        await client.post("/a2a/rpc", json=make_rpc_payload("delayed-cleanup", {}))

        # Capacity is full (2/2) -> Task 3 rejected
        resp3 = await client.post("/a2a/rpc", json=make_rpc_payload("delayed-cleanup", {}))
        assert "error" in resp3.json()
        assert "Task queue overloaded" in resp3.json()["error"]["message"]

        # Cancel Task 1 (which is currently running)
        cancel_resp = await client.post("/a2a/rpc", json=make_cancel_payload(tid1))
        assert cancel_resp.json()["result"]["status"]["state"] == "canceled"

        # Wait until Task 1 catches CancelledError and is in delayed cleanup
        await asyncio.wait_for(cancel_received_event.wait(), timeout=2.0)

        # While Task 1 is still running its cleanup, Task 4 MUST BE REJECTED
        # because actual capacity remains exhausted!
        resp4 = await client.post("/a2a/rpc", json=make_rpc_payload("delayed-cleanup", {}))
        assert "error" in resp4.json()
        assert "Task queue overloaded" in resp4.json()["error"]["message"]

        # Now allow Task 1 to finish its cleanup
        release_cleanup_event.set()
        await asyncio.wait_for(worker_finished_event.wait(), timeout=2.0)

        # Wait for Task 1's finally block to release resources
        active_t1 = a2a._manager.get_active_task(tid1)
        if active_t1 is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await active_t1

        # Now that Task 1 has genuinely terminated, queue slot is freed for Task 5!
        resp5 = await client.post("/a2a/rpc", json=make_rpc_payload("delayed-cleanup", {}))
        assert "result" in resp5.json()
        assert resp5.json()["result"]["status"]["state"] == "submitted"
