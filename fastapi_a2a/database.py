"""
Database configuration — async SQLAlchemy 2.0 engine + session factory.
"""
from __future__ import annotations

import os
from collections.abc import AsyncGenerator

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

# Naming convention for Alembic autogenerate
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Base(DeclarativeBase):
    metadata = metadata


def get_database_url() -> str | None:
    """
    Return the database URL from the environment, or ``None`` if not set.

    Checks ``DATABASE_URL`` first (standard Heroku/Render convention), then
    ``A2A_DATABASE_URL`` (the library-specific prefix).  Returns ``None`` when
    neither variable is present — callers should raise a clear ``ValueError``
    rather than silently connecting to a non-existent local database.

    Normalises ``postgres://`` → ``postgresql+asyncpg://`` for asyncpg compat.
    """
    raw = os.getenv("DATABASE_URL") or os.getenv("A2A_DATABASE_URL")
    if raw is None:
        return None
    return raw.replace("postgres://", "postgresql+asyncpg://")


def create_engine(database_url: str | None = None):
    """Create an async SQLAlchemy engine. Raises ``ValueError`` if no URL is resolved."""
    url = database_url or get_database_url()
    if url is None:
        raise ValueError(
            "No database URL found. Set the DATABASE_URL or A2A_DATABASE_URL "
            "environment variable, or pass database_url= to FastApiA2A()."
        )
    return create_async_engine(
        url,
        pool_size=20,
        max_overflow=10,
        pool_timeout=30,
        pool_pre_ping=True,
        echo=os.getenv("SQL_DEBUG", "false").lower() == "true",
    )


def create_session_factory(engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


async def get_session(session_factory: async_sessionmaker[AsyncSession]) -> AsyncGenerator[AsyncSession, None]:
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
