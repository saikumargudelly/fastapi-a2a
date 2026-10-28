"""Regression tests for public API exports and README quickstart workflows.

Verifies:
1. Public exports from `fastapi_a2a` (FastApiA2A, A2AClient, TaskStore, etc.).
2. Automatic setup workflow (setup_fastapi_a2a) mounts without double-mounting.
3. Explicit two-step workflow (FastApiA2A + mount()) mounts as expected.
4. Exact execution of README quickstart examples.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import fastapi_a2a
from fastapi_a2a import (
    A2AClient,
    FastApiA2A,
    InMemoryTaskStore,
    TaskStore,
    a2a_skill,
    create_a2a_client,
    setup_fastapi_a2a,
)


def test_public_api_exports() -> None:
    """Verify that FastApiA2A and essential components are in __all__ and exported."""
    expected_exports = [
        "FastApiA2A",
        "setup_fastapi_a2a",
        "a2a_skill",
        "A2AClient",
        "create_a2a_client",
        "TaskStore",
        "InMemoryTaskStore",
        "PROTOCOL_VERSION",
        "TERMINAL_STATES",
        "VALID_TRANSITIONS",
        "TaskState",
    ]
    for export in expected_exports:
        assert hasattr(fastapi_a2a, export), f"Missing export: {export}"
        assert export in fastapi_a2a.__all__, f"{export} not listed in __all__"

    # Verify InMemoryTaskStore can be directly instantiated from top-level import
    mem_store = InMemoryTaskStore(ttl_seconds=60)
    assert isinstance(mem_store, TaskStore)


def test_quickstart_option_a_automatic_setup() -> None:
    """Option A: setup_fastapi_a2a automatically mounts endpoints in one step."""
    app = FastAPI()

    @app.post("/summarise")
    @a2a_skill(description="Summarises long-form text into key bullet points.", tags=["nlp"])
    async def summarise(req: dict) -> dict:
        return {"summary": "This is a summary of the text..."}

    # Automatically inspects routes and mounts A2A endpoints
    a2a = setup_fastapi_a2a(app, name="My NLP Tools", url="https://nlp.example.com")

    # It must already be mounted
    assert a2a.is_mounted is True

    # Calling a2a.mount() explicitly must raise RuntimeError to prevent duplicate mounts
    with pytest.raises(
        RuntimeError,
        match=r"FastApiA2A\.mount\(\) already called on this application",
    ):
        a2a.mount()

    # Verify routes are functional
    with TestClient(app) as client:
        # Original endpoint
        resp = client.post("/summarise", json={"text": "hello world"})
        assert resp.status_code == 200
        assert resp.json() == {"summary": "This is a summary of the text..."}

        # Agent Card discovery
        card_resp = client.get("/.well-known/agent.json")
        assert card_resp.status_code == 200
        card_data = card_resp.json()
        assert card_data["name"] == "My NLP Tools"
        assert card_data["url"] == "https://nlp.example.com"
        assert len(card_data["skills"]) == 1
        assert card_data["skills"][0]["tags"] == ["nlp"]


def test_quickstart_option_b_explicit_setup() -> None:
    """Option B: FastApiA2A direct instantiation and explicit mount()."""
    app = FastAPI()

    @app.post("/summarise")
    @a2a_skill(description="Summarises long-form text into key bullet points.", tags=["nlp"])
    async def summarise(req: dict) -> dict:
        return {"summary": "This is a summary of the text..."}

    a2a = FastApiA2A(app, name="My NLP Tools", url="https://nlp.example.com")

    # Not mounted initially
    assert a2a.is_mounted is False

    # Mount explicitly
    a2a.mount()
    assert a2a.is_mounted is True

    # Calling a second time raises RuntimeError
    with pytest.raises(
        RuntimeError,
        match=r"FastApiA2A\.mount\(\) already called on this application",
    ):
        a2a.mount()

    # Verify routes are functional
    with TestClient(app) as client:
        resp = client.post("/summarise", json={"text": "hello world"})
        assert resp.status_code == 200
        assert resp.json() == {"summary": "This is a summary of the text..."}

        card_resp = client.get("/.well-known/agent.json")
        assert card_resp.status_code == 200
        card_data = card_resp.json()
        assert card_data["name"] == "My NLP Tools"
        assert len(card_data["skills"]) == 1


@pytest.mark.asyncio
async def test_quickstart_client_usage() -> None:
    """Verify that create_a2a_client and A2AClient can be constructed as documented."""
    async with create_a2a_client("https://example.com") as client:
        assert isinstance(client, A2AClient)
        assert str(client._base_url) == "https://example.com"


def test_custom_task_store_subclass_import() -> None:
    """Verify TaskStore base class can be subclassed when imported from top-level."""

    class CustomStore(TaskStore):
        async def create(self, context_id, message):
            raise NotImplementedError

        async def get(self, task_id):
            return None

        async def update_status(self, task_id, state, message=None):
            raise NotImplementedError

        async def add_artifact(self, task_id, artifact):
            raise NotImplementedError

        async def list(self, *, context_id=None, state=None, limit=50, cursor=None):
            return [], None

    store = CustomStore()
    assert isinstance(store, TaskStore)

    app = FastAPI()
    a2a = setup_fastapi_a2a(app, name="Custom Agent", url="https://agent.example.com", store=store)
    assert a2a.is_mounted is True
