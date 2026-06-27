"""Liveness/readiness + Prometheus metrics endpoints."""
from __future__ import annotations

import json

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from app.database import get_sessionmaker
from app.redis_client import get_redis

router = APIRouter(tags=["ops"])


@router.get("/health/live")
async def live() -> dict:
    """Liveness: the process is up. No dependencies checked."""
    return {"status": "ok"}


@router.get("/health")
@router.get("/health/ready")
async def ready() -> Response:
    """Readiness: can we actually serve traffic? Checks DB + Redis."""
    checks: dict[str, str] = {}
    healthy = True

    try:
        async with get_sessionmaker()() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # pragma: no cover - failure path
        checks["database"] = f"error: {exc}"
        healthy = False

    try:
        await get_redis().ping()
        checks["redis"] = "ok"
    except Exception as exc:  # pragma: no cover - failure path
        checks["redis"] = f"error: {exc}"
        healthy = False

    status_code = 200 if healthy else 503
    body = {"status": "ok" if healthy else "degraded", "checks": checks}
    return Response(
        content=json.dumps(body),
        media_type="application/json",
        status_code=status_code,
    )


@router.get("/metrics")
async def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
