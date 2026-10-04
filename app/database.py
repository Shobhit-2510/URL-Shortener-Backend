"""Async SQLAlchemy engine/session management.

Works against Postgres (asyncpg) in production and SQLite (aiosqlite) locally.
The only backend-specific concern — connection pooling — is handled here.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.config import get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _create_engine() -> AsyncEngine:
    settings = get_settings()
    url = settings.database_url
    if url.startswith("sqlite"):
        # In-memory SQLite gives each new connection its own private database, so
        # a StaticPool (one shared connection) is required for tables to persist
        # across sessions. File-based SQLite uses the default pool.
        if ":memory:" in url:
            return create_async_engine(
                url,
                future=True,
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )
        return create_async_engine(url, future=True, pool_pre_ping=True)
    if url.startswith("postgresql+asyncpg"):
        url = url.replace("sslmode=require", "ssl=require")

    return create_async_engine(
        url,
        future=True,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
    )


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = _create_engine()
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            get_engine(), expire_on_commit=False, class_=AsyncSession
        )
    return _sessionmaker


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding a session per request."""
    async with get_sessionmaker()() as session:
        yield session


async def init_models() -> None:
    """Create tables if they do not exist (dev/CI convenience).

    In a real deployment this is Alembic's job; kept here so `docker compose up`
    and the local benchmark are zero-step.
    """
    from app.models import Base

    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
