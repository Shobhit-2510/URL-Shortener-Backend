"""Shared pytest fixtures.

Tests run against the lightweight backend profile — in-memory SQLite + fakeredis
— so the suite is hermetic and needs no Docker/Postgres/Redis. The application
code is identical; only the configured URLs differ.
"""
from __future__ import annotations

import os

import pytest
import pytest_asyncio

# Configure the backend profile BEFORE any app module reads settings.
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["USE_FAKE_REDIS"] = "true"
os.environ["RATE_LIMIT_ENABLED"] = "true"

from app.config import get_settings  # noqa: E402
from app.database import dispose_engine, get_engine, init_models  # noqa: E402
from app.models import Base  # noqa: E402
from app.redis_client import get_redis, reset_redis_for_tests  # noqa: E402

get_settings.cache_clear()


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest_asyncio.fixture
async def db():
    """Fresh schema per test (shared in-memory engine)."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await init_models()
    yield
    await dispose_engine()


@pytest_asyncio.fixture
async def redis():
    """Isolated fakeredis per test."""
    reset_redis_for_tests()
    client = get_redis()
    await client.flushall()
    yield client
    await client.flushall()
    reset_redis_for_tests()
