"""Tests for the FastApiA2A plugin — card, RPC, lifecycle, double-mount."""
from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from fastapi_a2a import FastApiA2A, a2a_skill


@pytest.mark.asyncio
async def test_agent_card_endpoint(client: AsyncClient) -> None:
    resp = await client.get("/.well-known/agent.json")
    assert resp.status_code == 200
    card = resp.json()
    assert card["name"] == "Test Agent"
    assert card["protocolVersion"] == "0.3.0"
    assert "skills" in card


@pytest.mark.asyncio
async def test_agent_card_skills_contain_echo(client: AsyncClient) -> None:
    resp = await client.get("/.well-known/agent.json")
    skill_ids = [s["id"] for s in resp.json()["skills"]]
    assert "echo" in skill_ids


@pytest.mark.asyncio
async def test_agent_card_has_no_endpoint_field(client: AsyncClient) -> None:
    """endpoint must be stripped from card before wire serialisation."""
    resp = await client.get("/.well-known/agent.json")
    for skill in resp.json()["skills"]:
        assert "endpoint" not in skill
        assert "_endpoint" not in skill


@pytest.mark.asyncio
async def test_message_send_returns_submitted(client: AsyncClient, rpc_send_payload: dict) -> None:
    resp = await client.post("/a2a/rpc", json=rpc_send_payload)
    assert resp.status_code == 200
    body = resp.json()
    assert "result" in body
    assert body["result"]["status"]["state"] == "submitted"


@pytest.mark.asyncio
async def test_tasks_get_returns_task(client: AsyncClient, rpc_send_payload: dict) -> None:
    send_resp = await client.post("/a2a/rpc", json=rpc_send_payload)
    task_id = send_resp.json()["result"]["id"]

    get_payload = {
        "jsonrpc": "2.0", "id": "2",
        "method": "tasks/get", "params": {"id": task_id},
    }
    get_resp = await client.post("/a2a/rpc", json=get_payload)
    assert get_resp.json()["result"]["id"] == task_id


@pytest.mark.asyncio
async def test_tasks_get_unknown_returns_error(client: AsyncClient) -> None:
    payload = {
        "jsonrpc": "2.0", "id": "1",
        "method": "tasks/get", "params": {"id": "nonexistent"},
    }
    resp = await client.post("/a2a/rpc", json=payload)
    body = resp.json()
    assert "error" in body
    assert body["error"]["code"] == -32001


@pytest.mark.asyncio
async def test_tasks_cancel_terminal_returns_error(client: AsyncClient, rpc_send_payload: dict) -> None:
    send_resp = await client.post("/a2a/rpc", json=rpc_send_payload)
    task_id = send_resp.json()["result"]["id"]
    await asyncio.sleep(0.3)  # let the task complete

    get_payload = {
        "jsonrpc": "2.0", "id": "g",
        "method": "tasks/get", "params": {"id": task_id},
    }
    get_resp = await client.post("/a2a/rpc", json=get_payload)
    state = get_resp.json()["result"]["status"]["state"]

    if state == "completed":
        cancel_payload = {
            "jsonrpc": "2.0", "id": "c",
            "method": "tasks/cancel", "params": {"id": task_id},
        }
        cancel_resp = await client.post("/a2a/rpc", json=cancel_payload)
        assert cancel_resp.json()["error"]["code"] == -32002


@pytest.mark.asyncio
async def test_tasks_list_rpc(client: AsyncClient, rpc_send_payload: dict) -> None:
    """FIX B8: tasks/list must be reachable via the protocol."""
    await client.post("/a2a/rpc", json=rpc_send_payload)
    payload = {"jsonrpc": "2.0", "id": "1", "method": "tasks/list", "params": {}}
    resp = await client.post("/a2a/rpc", json=payload)
    body = resp.json()
    assert "result" in body
    assert "tasks" in body["result"]
    assert len(body["result"]["tasks"]) >= 1


@pytest.mark.asyncio
async def test_invalid_rpc_method(client: AsyncClient) -> None:
    payload = {"jsonrpc": "2.0", "id": "1", "method": "nonexistent/method", "params": {}}
    resp = await client.post("/a2a/rpc", json=payload)
    assert resp.json()["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_message_send_missing_message_key(client: AsyncClient) -> None:
    """FIX D2: missing 'message' key → INVALID_PARAMS (-32602)."""
    payload = {"jsonrpc": "2.0", "id": "1", "method": "message/send", "params": {}}
    resp = await client.post("/a2a/rpc", json=payload)
    assert resp.json()["error"]["code"] == -32602


@pytest.mark.asyncio
async def test_tasks_get_missing_id_key(client: AsyncClient) -> None:
    """FIX D1: missing 'id' key → INVALID_PARAMS (-32602), not INTERNAL_ERROR."""
    payload = {"jsonrpc": "2.0", "id": "1", "method": "tasks/get", "params": {}}
    resp = await client.post("/a2a/rpc", json=payload)
    assert resp.json()["error"]["code"] == -32602


@pytest.mark.asyncio
async def test_tasks_cancel_missing_id_key(client: AsyncClient) -> None:
    """FIX D1: same for tasks/cancel."""
    payload = {"jsonrpc": "2.0", "id": "1", "method": "tasks/cancel", "params": {}}
    resp = await client.post("/a2a/rpc", json=payload)
    assert resp.json()["error"]["code"] == -32602


@pytest.mark.asyncio
async def test_parse_error_on_bad_json(client: AsyncClient) -> None:
    resp = await client.post(
        "/a2a/rpc",
        content=b"not json!",
        headers={"Content-Type": "application/json"},
    )
    assert resp.json()["error"]["code"] == -32700


@pytest.mark.asyncio
async def test_double_mount_raises() -> None:
    app = FastAPI()
    a2a = FastApiA2A(app, name="Test", url="https://x.com")
    a2a.mount()
    with pytest.raises(RuntimeError, match="already called"):
        a2a.mount()


def test_is_mounted_property(base_app: FastAPI) -> None:
    """is_mounted reflects mounting state correctly."""
    app = FastAPI()
    a2a = FastApiA2A(app, name="Test", url="https://x.com")
    assert a2a.is_mounted is False
    a2a.mount()
    assert a2a.is_mounted is True


def test_fix_a5_empty_skills_list_respected() -> None:
    """FIX A5: passing skills=[] must override scan(), not trigger scan()."""
    app = FastAPI()

    @app.post("/route")
    @a2a_skill(description="Should NOT appear")
    async def my_route() -> dict: ...  # type: ignore[return-value]

    a2a = FastApiA2A(app, name="T", url="https://x.com", skills=[])
    assert a2a._skills == []  # explicit empty list was honoured


def test_existing_routes_unaffected(a2a_app: FastAPI) -> None:
    """Mounting A2A must not break pre-existing routes."""
    from starlette.testclient import TestClient

    with TestClient(app=a2a_app) as c:
        resp = c.post("/echo", json={"text": "world"})
    assert resp.status_code == 200
    assert resp.json()["text"] == "world"
