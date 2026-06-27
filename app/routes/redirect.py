"""Read path: the hot redirect endpoint.

This is the latency-critical path and where the cache + async analytics pay off:

    GET /{code}
      -> cache GET                          (Redis, ~sub-ms)
         HIT (real url)  -> emit click (async) -> 30x redirect
         HIT (negative)  -> 404
         MISS            -> DB read -> populate cache -> ... -> 30x / 404
      -> expired link    -> 410 Gone

The DB is touched only on a cache miss. Click analytics are pushed to a Redis
Stream and never block the response (see app/analytics.py).
"""
from __future__ import annotations

import time

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app import analytics, cache, metrics, service
from app.config import get_settings
from app.database import get_session
from app.middleware import client_ip
from app.redis_client import get_redis

router = APIRouter(tags=["redirect"])
log = structlog.get_logger(__name__)


@router.get("/{code}")
async def redirect(code: str, request: Request, session: AsyncSession = Depends(get_session)):
    settings = get_settings()
    redis = get_redis()

    long_url: str | None = None
    link_id: int | None = None

    # 1) Cache-aside read.
    cached = await cache.get(redis, code)
    if cached is not None:
        if cache.is_negative(cached):
            metrics.CACHE_EVENTS.labels("negative_hit").inc()
            metrics.REDIRECTS.labels("404").inc()
            return JSONResponse(status_code=404, content={"detail": "code not found"})
        metrics.CACHE_EVENTS.labels("hit").inc()
        long_url = cached
        # link_id unknown on a pure cache hit; analytics worker resolves by code.
    else:
        # 2) Cache miss -> DB.
        metrics.CACHE_EVENTS.labels("miss").inc()
        resolved = await service.resolve_code(session, code)
        if resolved is None:
            await cache.set_negative(redis, code)   # anti-penetration
            metrics.REDIRECTS.labels("404").inc()
            return JSONResponse(status_code=404, content={"detail": "code not found"})
        if resolved.is_expired:
            metrics.REDIRECTS.labels("410").inc()
            return JSONResponse(status_code=410, content={"detail": "link has expired"})
        long_url = resolved.long_url
        link_id = resolved.link_id
        await cache.set(redis, code, long_url)      # populate cache

    # 3) Fire-and-forget analytics (never blocks the redirect).
    await analytics.emit_click(
        redis,
        code=code,
        link_id=link_id if link_id is not None else -1,
        ts=time.time(),
        ip=client_ip(request),
        referer=request.headers.get("referer"),
        user_agent=request.headers.get("user-agent"),
    )

    status = settings.redirect_status_code
    metrics.REDIRECTS.labels(str(status)).inc()
    return RedirectResponse(url=long_url, status_code=status)
