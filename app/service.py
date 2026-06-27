"""Domain logic for creating and resolving short links."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import base62
from app.config import get_settings
from app.models import Link


class AliasTakenError(Exception):
    """Raised when a requested custom alias already exists."""


@dataclass(slots=True)
class ResolvedLink:
    long_url: str
    link_id: int
    expires_at: datetime | None

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        exp = self.expires_at
        # SQLite (and some drivers) hand back naive datetimes — treat as UTC.
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=UTC)
        return _now() >= exp


def _now() -> datetime:
    return datetime.now(UTC)


# LOAD-TEST ONLY: a semaphore standing in for a finite DB connection pool, so the
# simulated query latency actually contends on a bounded resource (see config).
_db_sim_sema: asyncio.Semaphore | None = None


def _db_sim_semaphore(limit: int) -> asyncio.Semaphore:
    global _db_sim_sema
    if _db_sim_sema is None:
        _db_sim_sema = asyncio.Semaphore(limit)
    return _db_sim_sema


async def _simulate_db_latency() -> None:
    settings = get_settings()
    lat = settings.db_read_latency_ms
    if lat <= 0:
        return
    delay = lat / 1000.0
    if settings.db_sim_pool > 0:
        async with _db_sim_semaphore(settings.db_sim_pool):
            await asyncio.sleep(delay)
    else:
        await asyncio.sleep(delay)


def code_for_id(link_id: int) -> str:
    settings = get_settings()
    return base62.encode(link_id + settings.code_offset, settings.code_alphabet)


async def create_link(
    session: AsyncSession,
    *,
    long_url: str,
    custom_alias: str | None = None,
    expires_at: datetime | None = None,
) -> Link:
    """Create a short link.

    * Generated code: insert, let the DB assign `id`, then derive
      `code = base62(id + offset)`. Collision-free — no retry loop.
    * Custom alias: the alias *is* the code. Uniqueness is enforced by the DB
      unique constraint; a clash raises AliasTakenError (HTTP 409).
    """
    if custom_alias is not None:
        # Pre-derive code from a placeholder; the unique constraint is the source
        # of truth for collisions (avoids a check-then-insert race).
        link = Link(
            code=custom_alias,
            long_url=long_url,
            is_custom_alias=True,
            expires_at=expires_at,
        )
        session.add(link)
        try:
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            raise AliasTakenError(custom_alias) from exc
        await session.commit()
        await session.refresh(link)
        return link

    # Generated code: two-step within one transaction.
    link = Link(code="", long_url=long_url, is_custom_alias=False, expires_at=expires_at)
    session.add(link)
    await session.flush()           # assigns link.id
    link.code = code_for_id(link.id)
    await session.commit()
    await session.refresh(link)
    return link


async def resolve_code(session: AsyncSession, code: str) -> ResolvedLink | None:
    """Load a link by code from the database (the cache-miss slow path)."""
    # No-op in production/tests (db_read_latency_ms defaults to 0).
    await _simulate_db_latency()
    row = (await session.execute(select(Link).where(Link.code == code))).scalar_one_or_none()
    if row is None:
        return None
    return ResolvedLink(long_url=row.long_url, link_id=row.id, expires_at=row.expires_at)


async def get_link(session: AsyncSession, code: str) -> Link | None:
    return (await session.execute(select(Link).where(Link.code == code))).scalar_one_or_none()


async def bump_click_count(session: AsyncSession, link_id: int, delta: int) -> None:
    """Used by the worker to apply aggregated counts."""
    await session.execute(
        update(Link).where(Link.id == link_id).values(click_count=Link.click_count + delta)
    )
