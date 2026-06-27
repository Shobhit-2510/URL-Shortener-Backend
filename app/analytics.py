"""Analytics producer: enqueue a click event onto a Redis Stream.

The whole point of this module is what it does NOT do: it does not touch
Postgres on the redirect path. A redirect's job is to be fast. We `XADD` a small
event to a Redis Stream (an append-only log with consumer-group semantics) and
return immediately; a separate worker (app/worker.py) consumes, aggregates, and
writes counts to Postgres out of band.

XADD with an approximate MAXLEN (`~`) caps the stream's memory without the O(N)
cost of exact trimming. If Redis is momentarily unavailable we swallow the error:
losing an analytics event must never fail a user's redirect.
"""
from __future__ import annotations

import hashlib
from typing import Any

import structlog

from app.config import get_settings

log = structlog.get_logger(__name__)


def _hash_ip(ip: str) -> str:
    """Store a salted hash, not the raw IP (privacy / GDPR-friendlier)."""
    return hashlib.sha256(ip.encode("utf-8")).hexdigest()[:16]


async def emit_click(
    redis: Any,
    *,
    code: str,
    link_id: int,
    ts: float,
    ip: str | None = None,
    referer: str | None = None,
    user_agent: str | None = None,
) -> None:
    settings = get_settings()
    event = {
        "code": code,
        "link_id": str(link_id),
        "ts": f"{ts:.3f}",
        "ip_hash": _hash_ip(ip) if ip else "",
        "referer": (referer or "")[:512],
        "ua": (user_agent or "")[:512],
    }
    try:
        await redis.xadd(
            settings.clicks_stream,
            event,
            maxlen=settings.clicks_stream_maxlen,
            approximate=True,
        )
    except Exception as exc:  # pragma: no cover - best-effort, never block redirect
        log.warning("click_emit_failed", code=code, error=str(exc))
