"""Write path: create short links and read their stats."""
from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import cache, service
from app.database import get_session
from app.redis_client import get_redis
from app.schemas import LinkStatsResponse, ShortenRequest, ShortenResponse

router = APIRouter(tags=["links"])
log = structlog.get_logger(__name__)


def _short_url(request: Request, code: str) -> str:
    base = str(request.base_url).rstrip("/")
    return f"{base}/{code}"


@router.post("/api/shorten", response_model=ShortenResponse, status_code=201)
async def shorten(
    payload: ShortenRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> ShortenResponse:
    try:
        link = await service.create_link(
            session,
            long_url=payload.url,
            custom_alias=payload.custom_alias,
            expires_at=payload.expires_at,
        )
    except service.AliasTakenError as exc:
        raise HTTPException(status_code=409, detail="custom_alias already in use") from exc

    # Warm the cache on write so the first redirect is already a hit.
    await cache.set(get_redis(), link.code, link.long_url)
    log.info("link_created", code=link.code, custom=link.is_custom_alias)

    return ShortenResponse(
        code=link.code,
        short_url=_short_url(request, link.code),
        long_url=link.long_url,
        is_custom_alias=link.is_custom_alias,
        created_at=link.created_at,
        expires_at=link.expires_at,
    )


@router.get("/api/stats/{code}", response_model=LinkStatsResponse)
async def stats(code: str, session: AsyncSession = Depends(get_session)) -> LinkStatsResponse:
    link = await service.get_link(session, code)
    if link is None:
        raise HTTPException(status_code=404, detail="code not found")
    return LinkStatsResponse(
        code=link.code,
        long_url=link.long_url,
        click_count=link.click_count,
        created_at=link.created_at,
        expires_at=link.expires_at,
    )
