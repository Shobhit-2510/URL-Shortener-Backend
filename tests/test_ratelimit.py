"""Token-bucket limiter tests: burst, exhaustion, refill, headers metadata."""
from __future__ import annotations

import pytest

from app import ratelimit
from app.config import get_settings

pytestmark = pytest.mark.asyncio


async def test_allows_within_capacity(redis) -> None:
    settings = get_settings()
    now = 1000.0
    for i in range(settings.rate_limit_capacity):
        res = await ratelimit.check(redis, "1.2.3.4", now=now)
        assert res.allowed, f"request {i} should be allowed"


async def test_blocks_when_exhausted(redis) -> None:
    settings = get_settings()
    now = 2000.0
    for _ in range(settings.rate_limit_capacity):
        await ratelimit.check(redis, "5.6.7.8", now=now)
    res = await ratelimit.check(redis, "5.6.7.8", now=now)
    assert not res.allowed
    assert res.remaining == 0
    assert res.retry_after >= 1


async def test_refills_over_time(redis) -> None:
    settings = get_settings()
    now = 3000.0
    for _ in range(settings.rate_limit_capacity):
        await ratelimit.check(redis, "9.9.9.9", now=now)
    blocked = await ratelimit.check(redis, "9.9.9.9", now=now)
    assert not blocked.allowed

    # Advance time enough to refill one token.
    later = now + (1.0 / settings.rate_limit_refill_per_sec) + 0.001
    refilled = await ratelimit.check(redis, "9.9.9.9", now=later)
    assert refilled.allowed


async def test_isolated_per_identity(redis) -> None:
    settings = get_settings()
    now = 4000.0
    for _ in range(settings.rate_limit_capacity):
        await ratelimit.check(redis, "a", now=now)
    assert not (await ratelimit.check(redis, "a", now=now)).allowed
    # A different IP has its own full bucket.
    assert (await ratelimit.check(redis, "b", now=now)).allowed


async def test_atomic_no_double_spend_under_concurrency(redis) -> None:
    """Fire capacity+N concurrent requests; never more than capacity succeed."""
    import asyncio

    settings = get_settings()
    now = 5000.0
    n = settings.rate_limit_capacity + 50
    results = await asyncio.gather(
        *[ratelimit.check(redis, "race", now=now) for _ in range(n)]
    )
    allowed = sum(1 for r in results if r.allowed)
    assert allowed == settings.rate_limit_capacity
