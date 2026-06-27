"""Per-IP token-bucket rate limiter backed by Redis.

Why token bucket (vs fixed-window counter)?
  * Smooths bursts: a client may spend up to `capacity` tokens instantly, then is
    throttled to the steady refill rate — no thundering herd at window boundaries
    (the classic fixed-window flaw where 2x the limit slips through across a
    boundary).
  * Cheap state: two numbers per client (token count + last-refill timestamp).

Why a Lua script?
  * Atomicity. Read-modify-write of the bucket must be a single round-trip or two
    concurrent requests can both observe the same token count and double-spend.
    Redis runs the whole script atomically, server-side, in one RTT.

Returns (allowed, remaining, retry_after_seconds, reset_after_seconds) so the
caller can set X-RateLimit-* and Retry-After headers.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from app.config import get_settings

# KEYS[1] = bucket key
# ARGV[1] = capacity, ARGV[2] = refill_per_sec, ARGV[3] = now (sec, float),
# ARGV[4] = requested tokens, ARGV[5] = ttl (sec)
_TOKEN_BUCKET_LUA = """
local key       = KEYS[1]
local capacity  = tonumber(ARGV[1])
local refill    = tonumber(ARGV[2])
local now       = tonumber(ARGV[3])
local requested = tonumber(ARGV[4])
local ttl       = tonumber(ARGV[5])

local data = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(data[1])
local ts = tonumber(data[2])

if tokens == nil then
  tokens = capacity
  ts = now
end

-- Refill based on elapsed time, capped at capacity.
local elapsed = math.max(0, now - ts)
tokens = math.min(capacity, tokens + elapsed * refill)

local allowed = 0
if tokens >= requested then
  allowed = 1
  tokens = tokens - requested
end

redis.call('HMSET', key, 'tokens', tokens, 'ts', now)
redis.call('EXPIRE', key, ttl)

-- NOTE: Redis converts Lua numbers to integers on return (the fraction is lost),
-- so we round to whole seconds *inside* the script. retry_after uses ceil (don't
-- tell the client to retry too early); remaining uses floor (don't over-report).
local retry_after = 0
if allowed == 0 then
  retry_after = math.ceil((requested - tokens) / refill)
end
local reset_after = math.ceil((capacity - tokens) / refill)
local remaining = math.floor(tokens)

return {allowed, remaining, retry_after, reset_after}
"""


@dataclass(slots=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    retry_after: int      # seconds, rounded up
    reset_after: int      # seconds, rounded up


_script_sha_cache: dict[int, str] = {}


async def check(redis: Any, identity: str, *, now: float, cost: int = 1) -> RateLimitResult:
    """Consume `cost` tokens for `identity`; never raises if limiter is disabled."""
    settings = get_settings()
    if not settings.rate_limit_enabled:
        cap = settings.rate_limit_capacity
        return RateLimitResult(True, cap, cap, 0, 0)

    capacity = settings.rate_limit_capacity
    refill = settings.rate_limit_refill_per_sec
    ttl = max(1, math.ceil(capacity / refill) * 2)
    key = f"rl:{identity}"

    allowed, tokens, retry_after, reset_after = await redis.eval(
        _TOKEN_BUCKET_LUA, 1, key, capacity, refill, now, cost, ttl
    )

    return RateLimitResult(
        allowed=bool(int(allowed)),
        limit=capacity,
        remaining=int(float(tokens)),
        retry_after=math.ceil(float(retry_after)),
        reset_after=math.ceil(float(reset_after)),
    )
