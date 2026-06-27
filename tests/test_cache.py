"""Cache-aside tests: hit/miss/negative semantics and TTLs."""
from __future__ import annotations

import pytest

from app import cache
from app.config import get_settings

pytestmark = pytest.mark.asyncio


async def test_miss_returns_none(redis) -> None:
    assert await cache.get(redis, "nope") is None


async def test_set_then_hit(redis) -> None:
    await cache.set(redis, "abc", "https://example.com")
    assert await cache.get(redis, "abc") == "https://example.com"


async def test_negative_cache_distinguished_from_miss(redis) -> None:
    await cache.set_negative(redis, "ghost")
    value = await cache.get(redis, "ghost")
    assert value is not None          # it's a HIT...
    assert cache.is_negative(value)   # ...of the known-absent kind


async def test_positive_ttl_applied(redis) -> None:
    settings = get_settings()
    await cache.set(redis, "ttlkey", "https://example.com")
    ttl = await redis.ttl(cache._key("ttlkey"))
    assert 0 < ttl <= settings.cache_ttl_seconds


async def test_negative_ttl_is_short(redis) -> None:
    settings = get_settings()
    await cache.set_negative(redis, "neg")
    ttl = await redis.ttl(cache._key("neg"))
    assert 0 < ttl <= settings.cache_negative_ttl_seconds


async def test_invalidate(redis) -> None:
    await cache.set(redis, "x", "https://example.com")
    await cache.invalidate(redis, "x")
    assert await cache.get(redis, "x") is None
