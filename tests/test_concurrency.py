"""Deterministic regression tests for concurrency limits, queue capacity, and semaphore release.

Verifies:
1. Tasks execute concurrently up to max_concurrency and do not exceed it.
2. Additional tasks within max_queue_size are accepted in 'submitted' state.
3. Requests exceeding max_capacity (concurrency + queue) are rejected with overload error.
4. Semaphore permits are reliably released after success, unhandled exceptions, and timeouts.
5. Repeated concurrent submissions complete without dropped tasks or missing results.
6. TaskManager defaults preserve the 2x capacity policy without private asyncio attributes.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from fastapi_a2a import FastApiA2A, a2a_skill
from fastapi_a2a._internal.task_manager import TaskManager
from fastapi_a2a.adapters.fastapi import FastApiAdapter
from fastapi_a2a.stores.memory import InMemoryTaskStore


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


async def poll_task_state(
    client: AsyncClient,
    task_id: str,
    target_state: str,
    deadline_seconds: float = 3.0,
) -> str:
    """Poll task status until reaching target_state or deadline."""
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


def test_task_manager_capacity_defaults() -> None:
    """Verify TaskManager stable capacity calculation and default 2x queue policy."""
    app = FastAPI()
    mgr = TaskManager(
        app=app,
        adapter=FastApiAdapter(),
        store=InMemoryTaskStore(),
        skills=[],
        timeout_seconds=10.0,
        max_concurrency=10,
    )
    # By default, max_queue_size == max_concurrency, so max_capacity == max_concurrency * 2
    assert mgr.max_concurrency == 10
    assert mgr.max_queue_size == 10
    assert mgr.max_capacity == 20

    # Custom queue size
    mgr_custom = TaskManager(
        app=app,
        adapter=FastApiAdapter(),
        store=InMemoryTaskStore(),
        skills=[],
        timeout_seconds=10.0,
        max_concurrency=5,
        max_queue_size=25,
    )
    assert mgr_custom.max_concurrency == 5
    assert mgr_custom.max_queue_size == 25
    assert mgr_custom.max_capacity == 30


@pytest.mark.asyncio
async def test_concurrent_execution_limited_to_max_concurrency() -> None:
    """Tasks actively executing simultaneously must never exceed configured max_concurrency."""
    app = FastAPI()

    active_count = 0
    peak_active = 0
    lock = asyncio.Lock()
    proceed_event = asyncio.Event()

    @app.post("/worker")
    @a2a_skill(description="Worker with concurrency tracking")
    async def worker(req: dict) -> dict:
        nonlocal active_count, peak_active
        async with lock:
            active_count += 1
            if active_count > peak_active:
                peak_active = active_count
        await proceed_event.wait()
        async with lock:
            active_count -= 1
        return {"status": "ok"}

    a2a = FastApiA2A(
        app,
        name="Concurrency Agent",
        url="https://x.com",
        max_concurrency=3,
        max_queue_size=10,
    )
    a2a.mount()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Submit 6 tasks concurrently
        payloads = [make_rpc_payload("worker", {"i": i}) for i in range(6)]
        responses = await asyncio.gather(*(client.post("/a2a/rpc", json=p) for p in payloads))
        assert all(r.status_code == 200 for r in responses)
        task_ids = [r.json()["result"]["id"] for r in responses]

        # Wait until 3 tasks are running
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            async with lock:
                if active_count == 3:
                    break
            await asyncio.sleep(0.02)

        async with lock:
            assert active_count == 3
            assert peak_active == 3

        # Release all waiting workers
        proceed_event.set()

        # Wait for all tasks to complete
        for tid in task_ids:
            state = await poll_task_state(client, tid, "completed")
            assert state == "completed"

        assert peak_active == 3


@pytest.mark.asyncio
async def test_queue_limit_acceptance() -> None:
    """Additional tasks up to max_queue_size are accepted in submitted state and queued."""
    app = FastAPI()
    proceed_event = asyncio.Event()

    @app.post("/hold")
    @a2a_skill(description="Hold task until released")
    async def hold(req: dict) -> dict:
        await proceed_event.wait()
        return {"result": req.get("val")}

    # 2 executing + 3 queued = 5 total capacity
    a2a = FastApiA2A(
        app,
        name="Queue Agent",
        url="https://x.com",
        max_concurrency=2,
        max_queue_size=3,
    )
    a2a.mount()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payloads = [make_rpc_payload("hold", {"val": i}) for i in range(5)]
        responses = await asyncio.gather(*(client.post("/a2a/rpc", json=p) for p in payloads))

        # All 5 must be accepted immediately
        assert all(r.status_code == 200 for r in responses)
        for r in responses:
            assert "result" in r.json()
            assert r.json()["result"]["status"]["state"] == "submitted"

        task_ids = [r.json()["result"]["id"] for r in responses]

        # Release tasks
        proceed_event.set()

        # Verify all 5 complete
        for tid in task_ids:
            state = await poll_task_state(client, tid, "completed", deadline_seconds=4.0)
            assert state == "completed"


@pytest.mark.asyncio
async def test_queue_overload_rejection_at_true_limit() -> None:
    """Requests exceeding total capacity are rejected, and accepted again when capacity frees up."""
    app = FastAPI()
    proceed_event = asyncio.Event()

    @app.post("/blocker")
    @a2a_skill(description="Block until event")
    async def blocker(req: dict) -> dict:
        await proceed_event.wait()
        return {"done": True}

    # max_concurrency=2, max_queue_size=2 -> capacity = 4
    a2a = FastApiA2A(
        app,
        name="Overload Agent",
        url="https://x.com",
        max_concurrency=2,
        max_queue_size=2,
    )
    a2a.mount()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Submit exactly 4 tasks (fill capacity)
        payloads = [make_rpc_payload("blocker", {"i": i}) for i in range(4)]
        responses = await asyncio.gather(*(client.post("/a2a/rpc", json=p) for p in payloads))
        assert all(r.status_code == 200 for r in responses)
        assert all("result" in r.json() for r in responses)

        # 5th request exceeds capacity -> must be rejected with overload error
        overload_payload = make_rpc_payload("blocker", {"i": 99})
        overload_resp = await client.post("/a2a/rpc", json=overload_payload)
        assert overload_resp.status_code == 200
        overload_data = overload_resp.json()
        assert "error" in overload_data
        assert "Task queue overloaded" in overload_data["error"]["message"]

        # Release blocked tasks
        proceed_event.set()

        # Wait for the 4 tasks to finish
        for r in responses:
            tid = r.json()["result"]["id"]
            state = await poll_task_state(client, tid, "completed")
            assert state == "completed"

        # Now capacity has freed up -> new request must succeed
        new_payload = make_rpc_payload("blocker", {"i": 100})
        new_resp = await client.post("/a2a/rpc", json=new_payload)
        assert new_resp.status_code == 200
        assert "result" in new_resp.json()
        assert new_resp.json()["result"]["status"]["state"] == "submitted"


@pytest.mark.asyncio
async def test_semaphore_release_after_success_and_failure() -> None:
    """Semaphore permits must be released on success, unhandled exceptions, and timeouts."""
    app = FastAPI()

    @app.post("/fast-ok")
    @a2a_skill(description="Fast success")
    async def fast_ok(req: dict) -> dict:
        return {"status": "ok"}

    @app.post("/crash")
    @a2a_skill(description="Always crashes")
    async def crash(req: dict) -> dict:
        raise ValueError("Intentional crash")

    @app.post("/slow")
    @a2a_skill(description="Times out")
    async def slow(req: dict) -> dict:
        await asyncio.sleep(2.0)
        return {"status": "slow"}

    # max_concurrency=1, timeout=0.2s
    a2a = FastApiA2A(
        app,
        name="Lifecycle Agent",
        url="https://x.com",
        max_concurrency=1,
        max_queue_size=1,
        timeout_seconds=0.2,
    )
    a2a.mount()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Case 1: Normal success releases semaphore
        resp1 = await client.post("/a2a/rpc", json=make_rpc_payload("fast-ok", {}))
        tid1 = resp1.json()["result"]["id"]
        state1 = await poll_task_state(client, tid1, "completed", deadline_seconds=2.0)
        assert state1 == "completed"

        # Case 2: Unhandled exception releases semaphore
        resp2 = await client.post("/a2a/rpc", json=make_rpc_payload("crash", {}))
        tid2 = resp2.json()["result"]["id"]
        state2 = await poll_task_state(client, tid2, "failed", deadline_seconds=2.0)
        assert state2 == "failed"

        # Case 3: Timeout releases semaphore
        resp3 = await client.post("/a2a/rpc", json=make_rpc_payload("slow", {}))
        tid3 = resp3.json()["result"]["id"]
        state3 = await poll_task_state(client, tid3, "failed", deadline_seconds=2.0)
        assert state3 == "failed"

        # Final verification: A new task can run and complete immediately
        resp4 = await client.post("/a2a/rpc", json=make_rpc_payload("fast-ok", {}))
        assert resp4.status_code == 200
        tid4 = resp4.json()["result"]["id"]
        state4 = await poll_task_state(client, tid4, "completed", deadline_seconds=2.0)
        assert state4 == "completed"


@pytest.mark.asyncio
async def test_repeated_extreme_concurrent_submissions() -> None:
    """Repeated waves of concurrent tasks execute without dropped tasks or missing results."""
    app = FastAPI()

    @app.post("/echo-compute")
    @a2a_skill(description="Echo compute")
    async def echo_compute(req: dict) -> dict:
        await asyncio.sleep(0.01)
        return {"echo": req.get("val")}

    # max_concurrency=4, max_queue_size=20 (capacity = 24)
    a2a = FastApiA2A(
        app,
        name="Wave Agent",
        url="https://x.com",
        max_concurrency=4,
        max_queue_size=20,
    )
    a2a.mount()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 3 waves of 12 requests each
        for wave in range(3):
            payloads = [
                make_rpc_payload("echo-compute", {"val": f"w{wave}-{i}"}) for i in range(12)
            ]
            responses = await asyncio.gather(*(client.post("/a2a/rpc", json=p) for p in payloads))
            assert all(r.status_code == 200 for r in responses)
            assert all("result" in r.json() for r in responses)

            task_ids = [r.json()["result"]["id"] for r in responses]

            # Poll until all 12 are completed
            for tid in task_ids:
                state = await poll_task_state(client, tid, "completed", deadline_seconds=4.0)
                assert state == "completed"
