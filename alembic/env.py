"""
Alembic environment configuration for fastapi-a2a migrations.
Supports async SQLAlchemy with asyncpg.
"""
import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

# Import all models so Alembic autogenerate detects them
import fastapi_a2a.domains.core_a2a.models          # noqa: F401
import fastapi_a2a.domains.task_lifecycle.models    # noqa: F401
import fastapi_a2a.domains.security.models          # noqa: F401
import fastapi_a2a.domains.registry.models          # noqa: F401
import fastapi_a2a.domains.access_control.models    # noqa: F401
import fastapi_a2a.domains.tracing.models           # noqa: F401
import fastapi_a2a.domains.token_hardening.models   # noqa: F401
import fastapi_a2a.domains.embedding.models         # noqa: F401
import fastapi_a2a.domains.consent.models           # noqa: F401
import fastapi_a2a.domains.key_management.models    # noqa: F401
import fastapi_a2a.domains.execution_policy.models  # noqa: F401
import fastapi_a2a.domains.federation.models        # noqa: F401
import fastapi_a2a.domains.safety.models            # noqa: F401

from fastapi_a2a.database import Base, get_database_url

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    return os.getenv("DATABASE_URL", get_database_url())


def run_migrations_offline() -> None:
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_schemas=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        include_schemas=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    url = get_url()
    connectable = create_async_engine(url)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
