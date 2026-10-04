"""FastAPI application factory + entrypoint."""
from __future__ import annotations

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from app.config import get_settings
from app.database import dispose_engine, init_models
from app.logging_config import configure_logging
from app.middleware import RateLimitMiddleware, RequestContextMiddleware
from app.redis_client import close_redis
from app.routes import frontend, health, redirect, shorten

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    await init_models()
    log.info("startup", environment=get_settings().environment)
    yield
    await close_redis()
    await dispose_engine()
    log.info("shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        description="Production-grade URL shortener: counter+base62 codes, Redis "
        "cache-aside, token-bucket rate limiting, async Streams analytics.",
        lifespan=lifespan,
    )

    # Order matters: RequestContext (outermost) wraps everything so even
    # rate-limited 429s get a request id, access log, and metrics.
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(RequestContextMiddleware)

    # Ops routes first; redirect's catch-all "/{code}" is registered LAST so it
    # never shadows /api/*, /health, /metrics.
    app.include_router(health.router)
    app.include_router(shorten.router)
    app.include_router(frontend.router)
    app.include_router(redirect.router)
    return app


app = create_app()
