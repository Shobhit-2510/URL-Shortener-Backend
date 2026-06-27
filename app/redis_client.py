"""Single shared async Redis client.

`redis.asyncio` and `fakeredis.aioredis` expose the same API, so the rest of the
app is backend-agnostic. fakeredis is used in tests and the local benchmark;
a real Redis is used under Docker.
"""
from __future__ import annotations

from typing import Any

from app.config import get_settings

_client: Any | None = None


def get_redis() -> Any:
    global _client
    if _client is None:
        settings = get_settings()
        if settings.use_fake_redis:
            import fakeredis.aioredis as fakeredis

            _client = fakeredis.FakeRedis(decode_responses=True)
        else:
            import redis.asyncio as redis

            _client = redis.from_url(settings.redis_url, decode_responses=True)
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
    _client = None


def reset_redis_for_tests() -> None:
    """Drop the cached client so a fresh fakeredis is created (test isolation)."""
    global _client
    _client = None
