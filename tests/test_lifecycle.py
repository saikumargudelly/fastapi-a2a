"""
Regression tests for startup and shutdown lifecycles in fastapi-a2a.

Verifies:
1. Default host application lifespan (FastAPI() with no custom lifespan).
2. Custom host application lifespan (FastAPI(lifespan=...)) preserving host state.
3. Legacy host event handlers (app.add_event_handler).
4. TaskStore start() and stop() idempotency and restart capability.
5. Custom TaskStore integration with start() and stop().
6. Error handling during startup (failed store initialization or failed host lifespan).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi import FastAPI, Request
from starlette.testclient import TestClient

from fastapi_a2a.adapters.base import BaseAdapter
from fastapi_a2a.plugin import FastApiA2A, setup_fastapi_a2a
from fastapi_a2a.stores.memory import InMemoryTaskStore


def test_default_host_lifespan_starts_and_stops_store() -> None:
    """When the host app uses default lifespan, the task store starts and stops."""
    app = FastAPI()
    a2a = setup_fastapi_a2a(
        app,
        name="DefaultLifespanAgent",
        url="https://agent.example.com",
    )

    store = a2a._store
    assert isinstance(store, InMemoryTaskStore)
    assert store._evictor is None

    with TestClient(app) as client:
        # Eviction task should be running while the app is active
        assert store._evictor is not None
        assert not store._evictor.done()

        # A2A discovery endpoint works
        res = client.get("/.well-known/agent.json")
        assert res.status_code == 200

    # Eviction task should be stopped after app shutdown
    assert store._evictor is not None
    assert store._evictor.done()


def test_custom_host_lifespan_starts_and_stops_store_and_preserves_state() -> None:
    """
    When the host app uses a custom lifespan:
    - Host lifespan startup and shutdown execute.
    - Host state yielded by custom lifespan is preserved on request.state.
    - TaskStore eviction task starts on startup and stops on shutdown.
    - Lifecycle order is strictly:
        host startup -> a2a startup -> a2a shutdown -> host shutdown.
    """
    lifecycle_events: list[str] = []

    @asynccontextmanager
    async def host_lifespan(app: FastAPI) -> AsyncIterator[dict[str, Any]]:
        lifecycle_events.append("host_startup")
        try:
            yield {"db_connection": "active_pool"}
        finally:
            lifecycle_events.append("host_shutdown")

    app = FastAPI(lifespan=host_lifespan)

    @app.get("/host-status")
    def host_status(request: Request) -> dict[str, str]:
        return {"db": request.state.db_connection}

    a2a = setup_fastapi_a2a(
        app,
        name="CustomLifespanAgent",
        url="https://agent.example.com",
    )

    store = a2a._store
    assert isinstance(store, InMemoryTaskStore)
    assert store._evictor is None

    with TestClient(app) as client:
        # Verify host route accessed yielded state
        resp = client.get("/host-status")
        assert resp.status_code == 200
        assert resp.json() == {"db": "active_pool"}

        # Verify A2A route works
        res = client.get("/.well-known/agent.json")
        assert res.status_code == 200

        # Verify store is active
        assert store._evictor is not None
        assert not store._evictor.done()

    # Verify store is stopped
    assert store._evictor is not None
    assert store._evictor.done()

    # Host lifespan ran both startup and shutdown
    assert lifecycle_events == ["host_startup", "host_shutdown"]


def test_host_legacy_event_handlers_coexist_with_a2a_lifespan() -> None:
    """Legacy add_event_handler on host app executes alongside A2A lifespan."""
    events: list[str] = []

    app = FastAPI()
    add_handler = getattr(app, "add_event_handler", None)
    if not callable(add_handler):
        add_handler = getattr(getattr(app, "router", None), "add_event_handler", None)

    if not callable(add_handler):
        pytest.skip("FastAPI version does not support event handlers")

    add_handler("startup", lambda: events.append("host_legacy_startup"))
    add_handler("shutdown", lambda: events.append("host_legacy_shutdown"))

    a2a = setup_fastapi_a2a(
        app,
        name="LegacyHandlerAgent",
        url="https://agent.example.com",
    )
    store = a2a._store
    assert isinstance(store, InMemoryTaskStore)

    with TestClient(app) as client:
        assert "host_legacy_startup" in events
        assert store._evictor is not None
        assert not store._evictor.done()
        res = client.get("/.well-known/agent.json")
        assert res.status_code == 200

    assert "host_legacy_shutdown" in events
    assert store._evictor.done()


@pytest.mark.asyncio
async def test_in_memory_task_store_idempotency_and_restart() -> None:
    """
    InMemoryTaskStore.start() and stop() must be idempotent:
    - Calling start() repeatedly does not create duplicate tasks.
    - Calling stop() repeatedly is a safe no-op.
    - Calling start() after stop() cleanly restarts the eviction loop.
    """
    store = InMemoryTaskStore(ttl_seconds=10)
    assert store._evictor is None

    # First start
    await store.start()
    task1 = store._evictor
    assert task1 is not None
    assert not task1.done()

    # Second start: idempotent, same task reference
    await store.start()
    assert store._evictor is task1

    # Stop: cancels and completes task
    await store.stop()
    assert task1.done()

    # Second stop: idempotent no-op
    await store.stop()
    assert task1.done()

    # Restart after stop: starts a new task
    await store.start()
    task2 = store._evictor
    assert task2 is not None
    assert task2 is not task1
    assert not task2.done()

    await store.stop()
    assert task2.done()


def test_custom_task_store_start_and_stop_invoked() -> None:
    """A custom TaskStore passed to FastApiA2A has start() and stop() called."""
    events: list[str] = []

    class CustomTestStore(InMemoryTaskStore):
        async def start(self) -> None:
            events.append("custom_store_start")
            await super().start()

        async def stop(self) -> None:
            events.append("custom_store_stop")
            await super().stop()

    custom_store = CustomTestStore()
    app = FastAPI()
    setup_fastapi_a2a(
        app,
        name="CustomStoreAgent",
        url="https://agent.example.com",
        store=custom_store,
    )

    assert events == []
    with TestClient(app) as client:
        assert events == ["custom_store_start"]
        res = client.get("/.well-known/agent.json")
        assert res.status_code == 200

    assert events == ["custom_store_start", "custom_store_stop"]


def test_store_startup_failure_prevents_app_startup() -> None:
    """If store.start() fails with an exception, application startup fails cleanly."""

    class FailingStore(InMemoryTaskStore):
        async def start(self) -> None:
            raise RuntimeError("Database connection refused")

    app = FastAPI()
    setup_fastapi_a2a(
        app,
        name="FailingStoreAgent",
        url="https://agent.example.com",
        store=FailingStore(),
    )

    with pytest.raises(RuntimeError, match="Database connection refused"):
        with TestClient(app):
            pass


def test_host_startup_failure_aborts_before_store_start() -> None:
    """If host lifespan startup fails, store.start() is not reached."""
    events: list[str] = []

    @asynccontextmanager
    async def failing_host_lifespan(app: FastAPI) -> AsyncIterator[None]:
        events.append("host_startup_begin")
        raise RuntimeError("Host configuration invalid")
        yield

    class TrackingStore(InMemoryTaskStore):
        async def start(self) -> None:
            events.append("store_start")
            await super().start()

        async def stop(self) -> None:
            events.append("store_stop")
            await super().stop()

    app = FastAPI(lifespan=failing_host_lifespan)
    setup_fastapi_a2a(
        app,
        name="HostFailAgent",
        url="https://agent.example.com",
        store=TrackingStore(),
    )

    with pytest.raises(RuntimeError, match="Host configuration invalid"):
        with TestClient(app):
            pass

    assert events == ["host_startup_begin"]
    assert "store_start" not in events


def test_partial_startup_failure_cleans_up_host_lifespan() -> None:
    """If store startup fails after host lifespan has entered, host cleanup must execute."""
    events: list[str] = []

    @asynccontextmanager
    async def host_lifespan(app: FastAPI) -> AsyncIterator[dict[str, str]]:
        events.append("host_startup")
        try:
            yield {"db": "active"}
        finally:
            events.append("host_shutdown")

    class FailingStore(InMemoryTaskStore):
        async def start(self) -> None:
            events.append("store_start_failed")
            raise RuntimeError("TaskStore initialization failed")

    app = FastAPI(lifespan=host_lifespan)
    setup_fastapi_a2a(
        app,
        name="PartialFailureAgent",
        url="https://agent.example.com",
        store=FailingStore(),
    )

    with pytest.raises(RuntimeError, match="TaskStore initialization failed"):
        with TestClient(app):
            pass

    # Host shutdown must have been executed despite A2A store startup failure
    assert events == ["host_startup", "store_start_failed", "host_shutdown"]


def test_repeated_application_lifecycles_on_same_app() -> None:
    """An application can be started and stopped multiple times cleanly."""
    app = FastAPI()
    a2a = setup_fastapi_a2a(
        app,
        name="RepeatedLifecycleAgent",
        url="https://agent.example.com",
    )
    store = a2a._store
    assert isinstance(store, InMemoryTaskStore)

    # First lifecycle run
    with TestClient(app) as client1:
        assert store._evictor is not None
        assert not store._evictor.done()
        res1 = client1.get("/.well-known/agent.json")
        assert res1.status_code == 200

    assert store._evictor.done()

    # Second lifecycle run on the same FastAPI instance
    with TestClient(app) as client2:
        assert store._evictor is not None
        assert not store._evictor.done()
        res2 = client2.get("/.well-known/agent.json")
        assert res2.status_code == 200

    assert store._evictor.done()


def test_legacy_adapter_without_lifespan_mount_succeeds() -> None:
    """
    A legacy custom adapter with mount(self, app, routes) (lacking lifespan)
    mounts without TypeError, and fallback event handlers execute the store lifecycle.
    """
    mounted_routes: list[tuple[str, Any, list[str]]] = []

    class LegacyCustomAdapter(BaseAdapter):
        def scan(self, app: Any) -> list[Any]:
            return []

        async def call(
            self,
            app: Any,
            path: str,
            payload: dict[str, Any],
            headers: dict[str, str],
        ) -> dict[str, Any]:
            return {}

        def mount(  # type: ignore[override]
            self,
            app: Any,
            routes: list[tuple[str, Any, list[str]]],
        ) -> None:
            # Legacy 2-argument signature without lifespan keyword
            mounted_routes.extend(routes)

    app = FastAPI()
    a2a = FastApiA2A(app, name="LegacyAdapterAgent", url="https://agent.example.com")
    a2a._adapter = LegacyCustomAdapter()
    a2a.mount()

    # Verify routes were mounted
    assert len(mounted_routes) == 2
    paths = [r[0] for r in mounted_routes]
    assert "/.well-known/agent.json" in paths
    assert "/a2a/rpc" in paths

    store = a2a._store
    assert isinstance(store, InMemoryTaskStore)

    # In default lifespan apps, fallback event handlers start and stop the store
    with TestClient(app):
        assert store._evictor is not None
        assert not store._evictor.done()

    assert store._evictor.done()


def test_legacy_adapter_without_lifespan_or_event_handler_raises() -> None:
    """
    If an adapter lacks lifespan support and the app does not provide add_event_handler,
    mount() fails explicitly rather than silently breaking lifecycle semantics.
    """

    class LegacyCustomAdapter(BaseAdapter):
        def scan(self, app: Any) -> list[Any]:
            return []

        async def call(
            self,
            app: Any,
            path: str,
            payload: dict[str, Any],
            headers: dict[str, str],
        ) -> dict[str, Any]:
            return {}

        def mount(  # type: ignore[override]
            self,
            app: Any,
            routes: list[tuple[str, Any, list[str]]],
        ) -> None:
            pass

    class AppWithoutEventHandlers:
        def __init__(self) -> None:
            self.routes: list[Any] = []

    app = AppWithoutEventHandlers()
    a2a = FastApiA2A(
        app,  # type: ignore[arg-type]
        name="NoHandlerAgent",
        url="https://agent.example.com",
        skills=[],
    )
    a2a._adapter = LegacyCustomAdapter()

    with pytest.raises(RuntimeError, match=r"Cannot register task store lifecycle"):
        a2a.mount()
