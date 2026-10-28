"""
Stress and Edge Case tests for fastapi-a2a.
These are meant to be run manually and intentionally break or stress the system.
Tests:
- Heavy concurrency (Task Manager Semaphore boundaries)
- Gigantic Payloads (Memory isolation)
- Extreme latency (Timeout bounds)
- Deep nesting (Recursion limits)
"""

from __future__ import annotations

import asyncio
import time
import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from fastapi_a2a import FastApiA2A, a2a_skill


@pytest.fixture()
def stress_agent() -> FastAPI:
    app = FastAPI()

    @app.post("/heavy-io")
    @a2a_skill(description="Simulate slow DB queries")
    async def heavy_io(req: dict) -> dict:
        await asyncio.sleep(req.get("delay", 1.0))
        return {"status": "done", "delay": req.get("delay")}

    @app.post("/memory-hog")
    @a2a_skill(description="Allocate a huge string")
    async def memory_hog(req: dict) -> dict:
        size = req.get("megabytes", 10)
        # Allocate huge string
        giant_string = "A" * (size * 1024 * 1024)
        return {"size": len(giant_string)}

    @app.post("/crash")
    @a2a_skill(description="Always crashes")
    async def crash(req: dict) -> dict:
        raise MemoryError("Fake out of memory")

    a2a = FastApiA2A(
        app,
        name="Stress Agent",
        url="http://stress.local",
        max_concurrency=10,  # Strict concurrency limit for testing
        max_queue_size=50,  # Accommodate 50 concurrent requests in extreme queueing test
        timeout_seconds=2.0,  # Aggressive timeout
    )
    a2a.mount()
    return app


@pytest.fixture()
async def client(stress_agent: FastAPI):
    async with AsyncClient(transport=ASGITransport(app=stress_agent), base_url="http://test") as c:
        yield c


def make_rpc_payload(skill_id: str, payload_data: dict) -> dict:
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


@pytest.mark.asyncio
async def test_extreme_concurrency_queueing(client: AsyncClient):
    """
    Test that sending 50 concurrent requests to a 10-concurrency agent
    doesn't crash the server and properly queues tasks.
    """
    # 50 requests that take 0.5s each.
    # With 10 concurrency, this will take ~2.5 seconds total.
    payloads = [make_rpc_payload("heavy-io", {"delay": 0.5}) for _ in range(50)]

    start_time = time.monotonic()

    # 1. Fire all 50 RPC requests concurrently
    responses = await asyncio.gather(*(client.post("/a2a/rpc", json=p) for p in payloads))

    # All should be accepted immediately (HTTP 200, state: submitted)
    assert all(r.status_code == 200 for r in responses)
    task_ids = [r.json()["result"]["id"] for r in responses]

    # 2. Wait for tasks to complete with deterministic polling
    deadline = time.monotonic() + 10.0
    completed = 0
    while time.monotonic() < deadline:
        completed = 0
        for tid in task_ids:
            get_req = {"jsonrpc": "2.0", "id": "1", "method": "tasks/get", "params": {"id": tid}}
            res = await client.post("/a2a/rpc", json=get_req)
            state = res.json()["result"]["status"]["state"]
            if state == "completed":
                completed += 1
        if completed == 50:
            break
        await asyncio.sleep(0.1)

    assert completed == 50
    duration = time.monotonic() - start_time
    assert duration >= 2.5  # Must take at least 2.5s due to 10-limit queueing


@pytest.mark.asyncio
async def test_task_timeout_enforcement(client: AsyncClient):
    """
    Test that a task intentionally exceeding the TaskManager timeout
    is correctly transitioned to 'failed' state.
    """
    # Agent timeout is 2.0s, we delay 3.0s
    payload = make_rpc_payload("heavy-io", {"delay": 3.0})
    resp = await client.post("/a2a/rpc", json=payload)
    task_id = resp.json()["result"]["id"]

    # Deterministic polling until timeout transitions task to failed
    deadline = time.monotonic() + 5.0
    state = None
    while time.monotonic() < deadline:
        get_req = {"jsonrpc": "2.0", "id": "1", "method": "tasks/get", "params": {"id": task_id}}
        res = await client.post("/a2a/rpc", json=get_req)
        state = res.json()["result"]["status"]["state"]
        if state == "failed":
            break
        await asyncio.sleep(0.1)

    assert state == "failed"


@pytest.mark.asyncio
async def test_gigantic_payload_ingestion(client: AsyncClient):
    """
    Test sending a 20MB RPC payload.
    Ensures Pydantic / JSON parsing can handle massive inputs without recursion errors.
    """
    giant_string = "X" * (20 * 1024 * 1024)  # 20MB string
    payload = make_rpc_payload("heavy-io", {"delay": 0.1, "giant": giant_string})

    resp = await client.post("/a2a/rpc", json=payload)
    assert resp.status_code == 200
    assert "error" not in resp.json()


@pytest.mark.asyncio
async def test_hard_crash_recovery(client: AsyncClient):
    """
    If a skill raises a fatal error (MemoryError), TaskManager must catch it,
    log it (handled implicitly by logging), and mark task as failed,
    NOT crash the asyncio loop.
    """
    payload = make_rpc_payload("crash", {})
    resp = await client.post("/a2a/rpc", json=payload)
    task_id = resp.json()["result"]["id"]

    # Deterministic polling until fatal crash transitions task to failed
    deadline = time.monotonic() + 5.0
    state = None
    while time.monotonic() < deadline:
        get_req = {"jsonrpc": "2.0", "id": "1", "method": "tasks/get", "params": {"id": task_id}}
        res = await client.post("/a2a/rpc", json=get_req)
        state = res.json()["result"]["status"]["state"]
        if state == "failed":
            break
        await asyncio.sleep(0.05)

    assert state == "failed"


@pytest.mark.asyncio
async def test_deeply_nested_json(client: AsyncClient):
    """
    Test how the system handles maliciously deep JSON.
    Standard Python recursion limit is 1000. Pydantic usually drops at 100.
    """
    deep_json = "value"
    for _ in range(250):
        deep_json = {"k": deep_json}

    payload = make_rpc_payload("heavy-io", {"delay": 0.1, "deep": deep_json})
    resp = await client.post("/a2a/rpc", json=payload)

    # It might pass or fail with a Parse Error (-32700), but it MUST NOT take down the server.
    # HTTP status must remain 200.
    assert resp.status_code == 200
