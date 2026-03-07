"""
FastApiA2A — Main bridge class.
Usage:
    from fastapi_a2a import FastApiA2A, RegistryConfig
    a2a = FastApiA2A(app, url="https://my-agent.example.com")
    # Registry auto-discovered via DNS-SD; or pass registry=RegistryConfig(url="...")
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from fastapi_a2a.bridge.config import FastApiA2AConfig, RegistryConfig
from fastapi_a2a.bridge.inspector import inspect_routes
from fastapi_a2a.database import Base, create_engine, create_session_factory
from fastapi_a2a.domains.consent.endpoints import router as consent_router
from fastapi_a2a.domains.core_a2a.endpoints import router as a2a_router
from fastapi_a2a.domains.core_a2a.models import (
    AgentCapabilities as AgentCapabilitiesModel,
)
from fastapi_a2a.domains.core_a2a.models import (
    AgentCard as AgentCardModel,
)
from fastapi_a2a.domains.core_a2a.models import (
    AgentSkill as AgentSkillModel,
)
from fastapi_a2a.domains.key_management.endpoints import router as key_mgmt_router
from fastapi_a2a.domains.registry.endpoints import router as registry_router
from fastapi_a2a.domains.task_lifecycle.endpoints import router as task_router


class FastApiA2A:
    """
    Bridge adapter that exposes a FastAPI application as an A2A Protocol agent.

    Features:
    - Auto-discovers routes and derives AgentSkill + SkillSchema objects
    - Mounts /.well-known/agent.json, /.well-known/agent-extended.json
    - Mounts /rpc (A2A JSON-RPC: tasks/send, tasks/get, tasks/cancel)
    - Mounts /registry (discovery + heartbeat)
    - Creates/manages SQLAlchemy async engine and session factory
    """

    def __init__(
        self,
        app: FastAPI,
        *,
        name: str | None = None,
        description: str | None = None,
        version: str = "1.0.0",
        url: str = "http://localhost:8000",
        registry: RegistryConfig | None = None,
        config: FastApiA2AConfig | None = None,
        database_url: str | None = None,
        documentation_url: str | None = None,
        provider_org: str | None = None,
        provider_url: str | None = None,
        data_region: str | None = None,
        streaming: bool = False,
        push_notifications: bool = False,
    ) -> None:
        self._app = app
        self._name = name or (app.title or "A2A Agent")
        self._description = description or (app.description or "An A2A-compatible agent")
        self._version = version
        self._url = url
        self._registry_config = registry
        self._config = config or FastApiA2AConfig()  # type: ignore[call-arg]
        self._database_url = database_url or self._config.database_url
        self._documentation_url = documentation_url
        self._provider_org = provider_org
        self._provider_url = provider_url
        self._data_region = data_region
        self._streaming = streaming
        self._push_notifications = push_notifications

        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None
        self._agent_card_id: uuid.UUID | None = None
        self._skill_handlers: dict[str, Callable] = {}

        # Mount immediately
        self._mount()

    def skill(self, skill_id: str) -> Callable:
        """Decorator to register a handler for a specific skill."""
        def decorator(fn: Callable) -> Callable:
            self._skill_handlers[skill_id] = fn
            return fn
        return decorator

    def _mount(self) -> None:
        """Mount all A2A routers and add lifespan integration."""
        self._app.include_router(a2a_router)
        self._app.include_router(task_router)
        self._app.include_router(registry_router, prefix="/registry")
        self._app.include_router(key_mgmt_router)
        self._app.include_router(consent_router)

        # Late-import to avoid circular on cold start
        from fastapi_a2a.domains.federation.endpoints import router as federation_router
        self._app.include_router(federation_router)

        # Store self on app state for access in lifespan
        self._app.state.a2a_bridge = self

        # Add session middleware
        @self._app.middleware("http")
        async def attach_db_session(request: Request, call_next):
            if self._session_factory is None:
                return await call_next(request)
            async with self._session_factory() as session:
                request.state.db = session
                try:
                    response = await call_next(request)
                    await session.commit()
                    return response
                except Exception:
                    await session.rollback()
                    raise

    async def startup(self) -> None:
        """
        Initialize DB engine, create tables, upsert the agent card,
        and start all background tasks.
        Call from your FastAPI lifespan or startup event.
        """
        self._engine = create_engine(self._database_url)
        self._session_factory = create_session_factory(self._engine)

        # Create all tables (dev mode — use Alembic for production)
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # Discover skills from routes
        skills_data = inspect_routes(self._app)

        # Upsert agent card
        async with self._session_factory() as db:
            agent_card_id = await self._upsert_agent_card(db, skills_data)
            self._agent_card_id = agent_card_id
            await db.commit()

        # Store on app state for endpoint access
        self._app.state.agent_card_id = self._agent_card_id
        self._app.state.skill_handlers = self._skill_handlers
        self._app.state.a2a_config = self._config

        # Configure rate limiter
        from fastapi_a2a.domains.token_hardening.rate_limiter import configure_rate_limiter
        configure_rate_limiter(
            redis_url=self._config.redis_url,
            shards=8,
        )

        # Start heartbeat scheduler
        from fastapi_a2a.domains.registry.heartbeat import start_heartbeat_scheduler
        self._heartbeat_task = await start_heartbeat_scheduler(
            self._session_factory,
            interval_seconds=self._config.dns_srv_timeout_ms // 1000 if self._config.dns_srv_timeout_ms >= 1000 else 60,
        )

        # Start dual-write fanout worker (if enabled)
        if self._config.dual_write_enabled:
            from fastapi_a2a.domains.token_hardening.dual_write_worker import (
                start_dual_write_fanout,
            )
            self._dual_write_task = await start_dual_write_fanout(
                self._session_factory,
                queue_type=self._config.dual_write_queue_type,
            )

        # Start embedding job processor
        from fastapi_a2a.domains.embedding.processor import start_embedding_processor
        await start_embedding_processor(self._session_factory)

        # Start execution policy runtime (lease reaper + compliance + SLO alerts)
        from fastapi_a2a.domains.execution_policy.runtime import start_execution_policy_runtime
        await start_execution_policy_runtime(self._session_factory)

        # Start crawler worker
        from fastapi_a2a.domains.federation.crawler import start_crawler_worker
        await start_crawler_worker(self._session_factory)

        # Start reputation engine
        from fastapi_a2a.domains.safety.reputation_engine import start_reputation_engine
        await start_reputation_engine(self._session_factory)

    async def _upsert_agent_card(
        self, db: AsyncSession, skills_data: list[dict]
    ) -> uuid.UUID:
        from sqlalchemy import select

        # Check for existing card at this URL
        result = await db.execute(
            select(AgentCardModel).where(AgentCardModel.url == self._url)
        )
        card = result.scalar_one_or_none()

        if card is None:
            card = AgentCardModel(
                name=self._name,
                description=self._description,
                url=self._url,
                version=self._version,
                documentation_url=self._documentation_url,
                provider_org=self._provider_org,
                provider_url=self._provider_url,
                data_region=self._data_region,
            )
            db.add(card)
            await db.flush()

            caps = AgentCapabilitiesModel(
                agent_card_id=card.id,
                streaming=self._streaming,
                push_notifications=self._push_notifications,
                state_transition_history=False,
                default_input_modes=["application/json"],
                default_output_modes=["application/json"],
                supports_auth_schemes=["bearer", "none"],
            )
            db.add(caps)
            await db.flush()
        else:
            card.name = self._name
            card.description = self._description
            card.version = self._version
            await db.flush()

        # Sync skills from route inspection
        for skill_data in skills_data:
            skill_result = await db.execute(
                select(AgentSkillModel).where(
                    AgentSkillModel.agent_card_id == card.id,
                    AgentSkillModel.skill_id == skill_data["skill_id"],
                )
            )
            skill = skill_result.scalar_one_or_none()
            if skill is None:
                skill = AgentSkillModel(
                    agent_card_id=card.id,
                    skill_id=skill_data["skill_id"],
                    name=skill_data["name"],
                    description=skill_data["description"],
                    tags=skill_data.get("tags", []),
                    examples=skill_data.get("examples", []),
                    input_modes=skill_data.get("input_modes", ["application/json"]),
                    output_modes=skill_data.get("output_modes", ["application/json"]),
                )
                db.add(skill)
            else:
                skill.name = skill_data["name"]
                skill.description = skill_data["description"]
            await db.flush()

        return card.id

    async def shutdown(self) -> None:
        """Cleanup — stop background tasks and close DB engine."""
        # Stop heartbeat scheduler
        from fastapi_a2a.domains.registry.heartbeat import stop_heartbeat_scheduler
        await stop_heartbeat_scheduler()

        # Stop dual-write fanout
        if self._config.dual_write_enabled:
            from fastapi_a2a.domains.token_hardening.dual_write_worker import stop_dual_write_fanout
            await stop_dual_write_fanout()

        # Close rate limiter
        from fastapi_a2a.domains.token_hardening.rate_limiter import get_rate_limiter
        await get_rate_limiter().close()

        if self._engine:
            await self._engine.dispose()

    @asynccontextmanager
    async def lifespan(self, app: FastAPI) -> AsyncGenerator[None, None]:
        """
        Pre-built asynccontextmanager for use as FastAPI lifespan:

            lifespan = a2a.lifespan
            app = FastAPI(lifespan=lifespan)
        """
        await self.startup()
        try:
            yield
        finally:
            await self.shutdown()
