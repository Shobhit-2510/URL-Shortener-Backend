"""Cache-aside layer for code -> long_url lookups.

Pattern (read path):
    1. GET url:{code} from Redis.
    2. HIT  -> return value (sentinel "" means a cached negative / 404).
    3. MISS -> caller loads from Postgres, then calls `set` (or `set_negative`).

Negative caching (storing a short-TTL sentinel for unknown codes) stops cache
*penetration*: a flood of requests for non-existent codes would otherwise miss
the cache every time and hammer Postgres. TTLs:
  * positive: long (settings.cache_ttl_seconds) — mappings are immutable.
  * negative: short (settings.cache_negative_ttl_seconds) — a code may be created.
"""
from __future__ import annotations

from typing import Any

from app.config import get_settings

_PREFIX = "url:"
_NEG_SENTINEL = ""  # empty string distinguishes "known absent" from "not cached" (None)


def _key(code: str) -> str:
    return f"{_PREFIX}{code}"


async def get(redis: Any, code: str) -> str | None | object:
    """Return the cached long_url, the negative sentinel, or None on a miss.

    * str (non-empty) -> cache hit, real URL.
    * ""              -> cache hit, known-absent (404).
    * None            -> cache miss; caller must consult the DB.
    """
    if not get_settings().cache_enabled:
        return None
    return await redis.get(_key(code))


async def set(redis: Any, code: str, long_url: str) -> None:
    settings = get_settings()
    if not settings.cache_enabled:
        return
    await redis.set(_key(code), long_url, ex=settings.cache_ttl_seconds)


async def set_negative(redis: Any, code: str) -> None:
    settings = get_settings()
    if not settings.cache_enabled:
        return
    await redis.set(_key(code), _NEG_SENTINEL, ex=settings.cache_negative_ttl_seconds)


async def invalidate(redis: Any, code: str) -> None:
    await redis.delete(_key(code))


def is_negative(value: Any) -> bool:
    return value == _NEG_SENTINEL
