"""Tests for the @a2a_skill decorator."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI

from fastapi_a2a import a2a_skill
from fastapi_a2a.adapters.fastapi import FastApiAdapter, _slugify


def test_a2a_skill_attaches_metadata() -> None:
    @a2a_skill(description="Test skill", tags=["nlp"], examples=["example"])
    async def my_handler() -> None: ...

    meta = my_handler._a2a_skill  # type: ignore[attr-defined]
    assert meta["description"] == "Test skill"
    assert meta["tags"] == ["nlp"]
    assert meta["examples"] == ["example"]


def test_a2a_skill_default_none_id_and_name() -> None:
    @a2a_skill(description="x")
    async def my_func() -> None: ...

    meta = my_func._a2a_skill  # type: ignore[attr-defined]
    assert meta["id"] is None
    assert meta["name"] is None


def test_a2a_skill_explicit_id_and_name() -> None:
    @a2a_skill(id="custom-id", name="Custom Name", description="x")
    async def my_func() -> None: ...

    meta = my_func._a2a_skill  # type: ignore[attr-defined]
    assert meta["id"] == "custom-id"
    assert meta["name"] == "Custom Name"


def test_a2a_skill_mutable_defaults_are_independent() -> None:
    """FIX A1: ensure separate calls don't share the same list objects."""

    @a2a_skill(description="x")
    async def func_a() -> None: ...

    @a2a_skill(description="y")
    async def func_b() -> None: ...

    func_a._a2a_skill["tags"].append("poison")  # type: ignore[attr-defined]
    assert func_b._a2a_skill["tags"] == []  # type: ignore[attr-defined]


def test_a2a_skill_preserves_function_name() -> None:
    @a2a_skill(description="x")
    async def my_important_function() -> None: ...

    assert my_important_function.__name__ == "my_important_function"


def test_a2a_skill_tags_are_copied() -> None:
    """Verify that the passed list is copied, not referenced."""
    tags = ["nlp"]

    @a2a_skill(description="x", tags=tags)
    async def func() -> None: ...

    tags.append("mutated")
    assert func._a2a_skill["tags"] == ["nlp"]  # type: ignore[attr-defined]


def test_slugify() -> None:
    assert _slugify("my_function") == "my-function"
    assert _slugify("MyFunction") == "myfunction"
    assert _slugify("get_user_by_id") == "get-user-by-id"
    assert _slugify("__private") == "private"


def test_scanner_finds_decorated_routes(base_app: FastAPI) -> None:
    adapter = FastApiAdapter()
    skills = adapter.scan(base_app)
    ids = [s["id"] for s in skills]
    assert "echo" in ids
    assert "add" in ids


def test_scanner_ignores_undecorated_routes() -> None:
    app = FastAPI()

    @app.get("/health")
    async def health() -> dict:  # type: ignore[return-value]
        return {"status": "ok"}

    adapter = FastApiAdapter()
    skills = adapter.scan(app)
    assert len(skills) == 0


def test_scanner_skill_has_endpoint_field(base_app: FastAPI) -> None:
    adapter = FastApiAdapter()
    skills = adapter.scan(base_app)
    echo_skill = next(s for s in skills if s["id"] == "echo")
    assert echo_skill["endpoint"] == "POST /echo"


def test_scanner_respects_explicit_tags(base_app: FastAPI) -> None:
    adapter = FastApiAdapter()
    skills = adapter.scan(base_app)
    echo_skill = next(s for s in skills if s["id"] == "echo")
    assert "test" in echo_skill["tags"]


@pytest.mark.asyncio
async def test_a2a_skill_preserves_async_behaviour() -> None:
    @a2a_skill(description="x")
    async def echo_skill(text: str) -> str:
        await asyncio.sleep(0)
        return text

    result = await echo_skill("hello")
    assert result == "hello"
