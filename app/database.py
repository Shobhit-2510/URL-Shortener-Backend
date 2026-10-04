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


def _sanitize_db_url(url: str) -> str:
    """Normalize database URL for async drivers and strip libpq-only parameters."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    if url.startswith("postgres://"):
        url = "postgresql+asyncpg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://") and not url.startswith("postgresql+"):
        url = "postgresql+asyncpg://" + url[len("postgresql://"):]

    if url.startswith("postgresql+asyncpg"):
        parts = urlsplit(url)
        query_params = parse_qsl(parts.query)
        cleaned_params: list[tuple[str, str]] = []
        has_ssl = False
        for k, v in query_params:
            if k == "sslmode":
                cleaned_params.append(("ssl", "require" if v == "require" else v))
                has_ssl = True
            elif k == "ssl":
                cleaned_params.append(("ssl", v))
                has_ssl = True
            elif k in ("channel_binding", "target_session_attrs", "gssencmode"):
                # libpq-specific parameters unsupported by asyncpg
                continue
            else:
                cleaned_params.append((k, v))
        if not has_ssl and "neon.tech" in parts.netloc:
            cleaned_params.append(("ssl", "require"))
        new_query = urlencode(cleaned_params)
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))
    return url


def _create_engine() -> AsyncEngine:
    settings = get_settings()
    url = _sanitize_db_url(settings.database_url)
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
