"""
Shared test fixtures for fastapi-a2a v3.

Architecture note:
- base_app: raw FastAPI app with @a2a_skill decorated routes
- a2a_app:  same app with FastApiA2A mounted
- client:   ASGI test client (httpx + ASGITransport)
- All fixtures use module-level models so FastAPI resolves them as JSON body.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from fastapi_a2a import FastApiA2A, a2a_skill

# ── Module-level request models ───────────────────────────────────────────────
# Must be at module level so FastAPI sees them as JSON body parameters.


class EchoRequest(BaseModel):
    text: str


class AddRequest(BaseModel):
    a: int
    b: int


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def base_app() -> FastAPI:
    """Raw FastAPI app with two A2A-decorated skills."""
    app = FastAPI()

    @app.post("/echo")
    @a2a_skill(description="Echo text back", tags=["test"])
    async def echo(req: EchoRequest) -> dict:  # type: ignore[return-value]
        return {"text": req.text}

    @app.post("/add")
    @a2a_skill(description="Add two numbers", tags=["math"])
    async def add(req: AddRequest) -> dict:  # type: ignore[return-value]
        return {"result": req.a + req.b}

    return app


@pytest.fixture()
def a2a_app(base_app: FastAPI) -> FastAPI:
    """base_app with FastApiA2A plugin mounted."""
    a2a = FastApiA2A(
        base_app,
        name="Test Agent",
        url="https://test.example.com",
        version="1.0.0",
        description="A test agent",
    )
    a2a.mount()
    return base_app


@pytest_asyncio.fixture()
async def client(a2a_app: FastAPI) -> AsyncClient:
    """Async HTTP test client against a2a_app."""
    async with AsyncClient(transport=ASGITransport(app=a2a_app), base_url="http://test") as c:
        yield c


@pytest.fixture()
def rpc_send_payload() -> dict:
    """Valid message/send JSON-RPC payload targeting the echo skill."""
    return {
        "jsonrpc": "2.0",
        "id": "test-req-1",
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "kind": "message",
                "messageId": "msg-1",
                "parts": [{"kind": "data", "data": {"text": "hello"}}],
                "metadata": {"skillId": "echo"},
            }
        },
    }
