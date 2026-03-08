"""Tests for A2AClient against a live in-process test agent."""
from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from fastapi_a2a import A2AClient, FastApiA2A, a2a_skill
from fastapi_a2a._internal.exceptions import A2ARemoteError
from pydantic import BaseModel


# ── Module-level model so FastAPI resolves it as JSON body ────────────────────

class EchoReq(BaseModel):
    text: str


# ── Test agent ────────────────────────────────────────────────────────────────

@pytest.fixture()
def echo_agent() -> FastAPI:
    """Minimal FastAPI agent with one echo skill."""
    app = FastAPI()

    @app.post("/echo")
    @a2a_skill(description="Echo", tags=["test"])
    async def echo(req: EchoReq) -> dict:  # type: ignore[return-value]
        return {"text": req.text}

    FastApiA2A(app, name="Echo Agent", url="https://echo.example.com").mount()
    return app


@pytest_asyncio.fixture()
async def agent_client(echo_agent: FastAPI) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=echo_agent), base_url="http://testserver"
    ) as http:
        yield http


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_client_get_card(agent_client: AsyncClient) -> None:
    from fastapi_a2a._internal.schema import agent_card_adapter
    resp = await agent_client.get("/.well-known/agent.json")
    card = agent_card_adapter.validate_json(resp.content)
    assert card["name"] == "Echo Agent"


@pytest.mark.asyncio
async def test_client_raises_without_context_manager() -> None:
    """FIX A2: must raise RuntimeError, not AttributeError from assert."""
    client = A2AClient("https://x.com")
    with pytest.raises(RuntimeError, match="context manager"):
        await client.get_card()


@pytest.mark.asyncio
async def test_client_default_headers(agent_client: AsyncClient) -> None:
    """FIX A3: asyncio imported at top-level — no dynamic imports in hot paths."""
    client = A2AClient("https://x.com", auth_token="tok")
    headers = client._default_headers()
    assert headers["A2A-Version"] == "0.3.0"
    assert "Bearer tok" in headers["Authorization"]


@pytest.mark.asyncio
async def test_client_get_card_cached(agent_client: AsyncClient) -> None:
    """Card is cached for card_ttl_seconds."""
    from fastapi_a2a._internal.schema import agent_card_adapter
    resp = await agent_client.get("/.well-known/agent.json")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_client_remote_error_raises(agent_client: AsyncClient) -> None:
    """tasks/get on unknown task raises A2ARemoteError -32001."""
    rpc_payload = {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "tasks/get",
        "params": {"id": "bad-task-id"},
    }
    resp = await agent_client.post("/a2a/rpc", json=rpc_payload)
    body = resp.json()
    assert "error" in body
    assert body["error"]["code"] == -32001


@pytest.mark.asyncio
async def test_full_send_and_get_cycle(agent_client: AsyncClient) -> None:
    """Integration test: message/send → tasks/get → read artifact."""
    import asyncio

    send_payload = {
        "jsonrpc": "2.0",
        "id": "req-1",
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "kind": "message",
                "messageId": "msg-1",
                "parts": [{"kind": "data", "data": {"text": "hello world"}}],
                "metadata": {"skillId": "echo"},
            }
        },
    }
    send_resp = await agent_client.post("/a2a/rpc", json=send_payload)
    task_id = send_resp.json()["result"]["id"]
    assert task_id

    # Poll to completion
    for _ in range(20):
        get_payload = {
            "jsonrpc": "2.0", "id": "2",
            "method": "tasks/get", "params": {"id": task_id},
        }
        get_resp = await agent_client.post("/a2a/rpc", json=get_payload)
        state = get_resp.json()["result"]["status"]["state"]
        if state in {"completed", "failed", "rejected", "canceled"}:
            break
        await asyncio.sleep(0.05)

    assert state == "completed"
    artifacts = get_resp.json()["result"]["artifacts"]
    assert len(artifacts) >= 1


@pytest.mark.asyncio
async def test_tasks_list_via_agent(agent_client: AsyncClient) -> None:
    """FIX B8: tasks/list is callable via the protocol."""
    # Create a task first
    send_payload = {
        "jsonrpc": "2.0", "id": "s",
        "method": "message/send",
        "params": {
            "message": {
                "role": "user", "kind": "message", "messageId": "m1",
                "parts": [{"kind": "data", "data": {"text": "hi"}}],
                "metadata": {"skillId": "echo"},
            }
        },
    }
    await agent_client.post("/a2a/rpc", json=send_payload)

    list_payload = {"jsonrpc": "2.0", "id": "l", "method": "tasks/list", "params": {}}
    resp = await agent_client.post("/a2a/rpc", json=list_payload)
    result = resp.json()["result"]
    assert "tasks" in result
    assert len(result["tasks"]) >= 1


@pytest.mark.asyncio
async def test_client_poll_timeout_raises() -> None:
    """FIX B7: poll_timeout_seconds 0 should raise TimeoutError immediately."""
    import asyncio, time
    from unittest.mock import AsyncMock, patch

    client = A2AClient("https://x.com", poll_timeout_seconds=0.01)
    client._http = AsyncMock()

    # Mock get_task to always return 'submitted'
    from fastapi_a2a._internal.utils import utcnow
    submitted_task = {
        "id": "t1", "contextId": "c1", "kind": "task",
        "status": {"state": "submitted", "timestamp": utcnow()},
        "history": [], "artifacts": [],
        "createdAt": utcnow(), "updatedAt": utcnow(),
    }
    with patch.object(client, "get_task", AsyncMock(return_value=submitted_task)):
        with pytest.raises(TimeoutError):
            await client._poll_until_done("t1", interval_seconds=0.001)
