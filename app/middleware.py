"""ASGI middleware: request context/metrics + per-IP rate limiting."""
from __future__ import annotations

import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app import metrics, ratelimit
from app.redis_client import get_redis

log = structlog.get_logger(__name__)

# Paths exempt from rate limiting (infra/observability must always be reachable).
_RL_EXEMPT = {"/health", "/health/live", "/health/ready", "/metrics"}


def client_ip(request: Request) -> str:
    """Best-effort client IP, honouring the first X-Forwarded-For hop."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _route_template(request: Request) -> str:
    """Low-cardinality label for metrics (route pattern, not the raw path)."""
    route = request.scope.get("route")
    return getattr(route, "path", request.url.path)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Bind a request_id, emit a structured access log, record metrics."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled_exception")
            metrics.REQUEST_COUNT.labels(request.method, request.url.path, "500").inc()
            raise
        elapsed = time.perf_counter() - start

        endpoint = _route_template(request)
        metrics.REQUEST_COUNT.labels(request.method, endpoint, str(response.status_code)).inc()
        metrics.REQUEST_LATENCY.labels(request.method, endpoint).observe(elapsed)

        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time-ms"] = f"{elapsed * 1000:.2f}"
        log.info("request", status=response.status_code, duration_ms=round(elapsed * 1000, 2))
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-IP token-bucket limiting with standard rate-limit headers."""

    async def dispatch(self, request: Request, call_next):
        if request.url.path in _RL_EXEMPT:
            return await call_next(request)

        identity = client_ip(request)
        result = await ratelimit.check(get_redis(), identity, now=time.time())

        if not result.allowed:
            metrics.RATE_LIMITED.inc()
            log.warning("rate_limited", ip=identity, retry_after=result.retry_after)
            resp = JSONResponse(
                status_code=429,
                content={"detail": "Too Many Requests", "retry_after": result.retry_after},
            )
            _set_rl_headers(resp, result)
            resp.headers["Retry-After"] = str(result.retry_after)
            return resp

        response = await call_next(request)
        _set_rl_headers(response, result)
        return response


def _set_rl_headers(response: Response, result: ratelimit.RateLimitResult) -> None:
    response.headers["X-RateLimit-Limit"] = str(result.limit)
    response.headers["X-RateLimit-Remaining"] = str(max(0, result.remaining))
    response.headers["X-RateLimit-Reset"] = str(result.reset_after)
