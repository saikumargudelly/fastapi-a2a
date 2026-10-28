"""Regression tests for route discovery across direct, included, and nested routers."""

from __future__ import annotations

import pytest
from fastapi import APIRouter, Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from fastapi_a2a import a2a_skill, setup_fastapi_a2a
from fastapi_a2a.adapters.fastapi import FastApiAdapter


class ItemResponse(BaseModel):
    id: int
    name: str


class EchoInput(BaseModel):
    message: str


def test_scan_direct_routes() -> None:
    """Preserve existing behavior for directly registered routes."""
    app = FastAPI()

    @app.get("/items")
    @a2a_skill(name="list_items", description="List all items")
    def list_items() -> list[str]:
        return ["item1"]

    @app.post("/items")
    @a2a_skill(name="create_item", description="Create an item")
    def create_item() -> dict:
        return {"status": "created"}

    @app.get("/undecorated")
    def undecorated() -> dict:
        return {"status": "ignored"}

    adapter = FastApiAdapter()
    skills = adapter.scan(app)

    assert len(skills) == 2
    endpoints = {s["name"]: s["endpoint"] for s in skills}
    assert endpoints["list_items"] == "GET /items"
    assert endpoints["create_item"] == "POST /items"


def test_scan_included_router_without_prefix() -> None:
    """Included router without a prefix should discover routes at root path."""
    app = FastAPI()
    router = APIRouter()

    @router.get("/status")
    @a2a_skill(name="get_status", description="Get system status")
    def get_status() -> dict:
        return {"ok": True}

    app.include_router(router)

    adapter = FastApiAdapter()
    skills = adapter.scan(app)

    assert len(skills) == 1
    assert skills[0]["name"] == "get_status"
    assert skills[0]["endpoint"] == "GET /status"


def test_scan_included_router_with_router_prefix() -> None:
    """Included router with prefix defined on APIRouter."""
    app = FastAPI()
    router = APIRouter(prefix="/api/v1")

    @router.post("/users")
    @a2a_skill(name="create_user", description="Create a new user")
    def create_user() -> dict:
        return {"id": 1}

    app.include_router(router)

    adapter = FastApiAdapter()
    skills = adapter.scan(app)

    assert len(skills) == 1
    assert skills[0]["name"] == "create_user"
    assert skills[0]["endpoint"] == "POST /api/v1/users"


def test_scan_included_router_with_include_prefix() -> None:
    """Included router with prefix specified in app.include_router(prefix=...)."""
    app = FastAPI()
    router = APIRouter(prefix="/v1")

    @router.get("/orders")
    @a2a_skill(name="get_orders", description="Get user orders")
    def get_orders() -> list:
        return []

    app.include_router(router, prefix="/api")

    adapter = FastApiAdapter()
    skills = adapter.scan(app)

    assert len(skills) == 1
    assert skills[0]["name"] == "get_orders"
    assert skills[0]["endpoint"] == "GET /api/v1/orders"


def test_scan_nested_routers_with_prefixes() -> None:
    """Nested routers should compose all prefixes correctly."""
    app = FastAPI()
    parent_router = APIRouter(prefix="/parent")
    child_router = APIRouter(prefix="/child")

    @child_router.delete("/resource")
    @a2a_skill(name="delete_resource", description="Delete nested resource")
    def delete_resource() -> dict:
        return {"deleted": True}

    parent_router.include_router(child_router)
    app.include_router(parent_router, prefix="/api")

    adapter = FastApiAdapter()
    skills = adapter.scan(app)

    assert len(skills) == 1
    assert skills[0]["name"] == "delete_resource"
    assert skills[0]["endpoint"] == "DELETE /api/parent/child/resource"


def test_scan_deeply_nested_routers() -> None:
    """Three levels of nested routers must all resolve correctly."""
    app = FastAPI()
    level1 = APIRouter(prefix="/l1")
    level2 = APIRouter(prefix="/l2")
    level3 = APIRouter(prefix="/l3")

    @level3.patch("/deep")
    @a2a_skill(name="deep_patch", description="Deeply nested route")
    def deep_patch() -> dict:
        return {"depth": 3}

    level2.include_router(level3)
    level1.include_router(level2)
    app.include_router(level1)

    adapter = FastApiAdapter()
    skills = adapter.scan(app)

    assert len(skills) == 1
    assert skills[0]["name"] == "deep_patch"
    assert skills[0]["endpoint"] == "PATCH /l1/l2/l3/deep"


def test_scan_dependency_wrapped_endpoint_in_router() -> None:
    """Endpoints with dependencies in included routers should be detected."""
    app = FastAPI()
    router = APIRouter(prefix="/auth")

    def dummy_dep() -> str:
        return "token"

    @router.get("/profile")
    @a2a_skill(name="get_profile", description="Get auth profile")
    def get_profile(token: str = Depends(dummy_dep)) -> dict:
        return {"token": token}

    app.include_router(router)

    adapter = FastApiAdapter()
    skills = adapter.scan(app)

    assert len(skills) == 1
    assert skills[0]["name"] == "get_profile"
    assert skills[0]["endpoint"] == "GET /auth/profile"


def test_scan_response_model_in_included_router() -> None:
    """Response model JSON schema should be extracted for routes in included routers."""
    app = FastAPI()
    router = APIRouter(prefix="/catalog")

    @router.get("/item", response_model=ItemResponse)
    @a2a_skill(name="get_catalog_item", description="Get catalog item")
    def get_item() -> dict:
        return {"id": 1, "name": "Widget"}

    app.include_router(router)

    adapter = FastApiAdapter()
    skills = adapter.scan(app)

    assert len(skills) == 1
    assert "outputSchema" in skills[0]
    schema = skills[0]["outputSchema"]
    assert "properties" in schema
    assert "id" in schema["properties"]
    assert "name" in schema["properties"]


@pytest.mark.asyncio
async def test_plugin_integration_with_included_router() -> None:
    """End-to-end integration: card generation and RPC execution targeting included router skill."""
    app = FastAPI()
    router = APIRouter(prefix="/services")

    @router.post("/echo")
    @a2a_skill(id="service-echo", name="service_echo", description="Echo service")
    async def echo_endpoint(payload: EchoInput) -> dict:
        return {"reply": payload.message}

    app.include_router(router)

    setup_fastapi_a2a(
        app,
        name="Router Test Agent",
        url="https://test.example.com",
        version="1.0.0",
        description="Agent testing router integration",
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Verify card exposes the skill from the included router
        card_resp = await client.get("/.well-known/agent.json")
        assert card_resp.status_code == 200
        card = card_resp.json()
        skill_names = [s["name"] for s in card["skills"]]
        assert "service_echo" in skill_names

        # 2. Execute the skill via JSON-RPC message/send
        rpc_payload = {
            "jsonrpc": "2.0",
            "id": "req-123",
            "method": "message/send",
            "params": {
                "message": {
                    "role": "user",
                    "kind": "message",
                    "messageId": "msg-123",
                    "parts": [{"kind": "data", "data": {"message": "hello from included router"}}],
                    "metadata": {"skillId": "service-echo"},
                }
            },
        }
        send_resp = await client.post("/a2a/rpc", json=rpc_payload)
        assert send_resp.status_code == 200
        body = send_resp.json()
        assert "result" in body
        task_id = body["result"]["id"]

        # Wait for task background execution to complete
        import asyncio

        await asyncio.sleep(0.2)

        # 3. Retrieve task result
        get_payload = {
            "jsonrpc": "2.0",
            "id": "req-124",
            "method": "tasks/get",
            "params": {"id": task_id},
        }
        get_resp = await client.post("/a2a/rpc", json=get_payload)
        assert get_resp.status_code == 200
        task_body = get_resp.json()["result"]
        assert task_body["status"]["state"] == "completed"
        artifacts = task_body["artifacts"]
        assert artifacts[0]["parts"][0]["data"] == {"reply": "hello from included router"}
