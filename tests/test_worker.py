"""Worker aggregation test: events on the stream -> aggregated counts in the DB."""
from __future__ import annotations

import time

import pytest

from app import analytics, service
from app.config import get_settings
from app.database import get_sessionmaker
from app.worker import _ensure_group, _flush

pytestmark = pytest.mark.asyncio


async def test_worker_aggregates_clicks(db, redis) -> None:
    settings = get_settings()
    # Create a link so the worker can resolve code -> link_id.
    async with get_sessionmaker()() as session:
        link = await service.create_link(session, long_url="https://example.com")
        code, link_id = link.code, link.id

    # Enqueue 5 clicks via the real producer.
    for _ in range(5):
        await analytics.emit_click(redis, code=code, link_id=link_id, ts=time.time())

    await _ensure_group(redis)
    resp = await redis.xreadgroup(
        settings.clicks_consumer_group, "test", {settings.clicks_stream: ">"}, count=100
    )
    messages = [(mid, data) for _stream, entries in resp for mid, data in entries]
    assert len(messages) == 5

    await _flush(messages, {})

    async with get_sessionmaker()() as session:
        refreshed = await service.get_link(session, code)
        assert refreshed.click_count == 5


async def test_worker_resolves_link_id_by_code_on_cache_hit_events(db, redis) -> None:
    """Cache-hit redirects emit link_id=-1; worker must resolve by code."""
    async with get_sessionmaker()() as session:
        link = await service.create_link(session, long_url="https://example.com/y")
        code = link.code

    # link_id = -1 simulates an event emitted from a pure cache hit.
    await analytics.emit_click(redis, code=code, link_id=-1, ts=time.time())

    settings = get_settings()
    await _ensure_group(redis)
    resp = await redis.xreadgroup(
        settings.clicks_consumer_group, "test", {settings.clicks_stream: ">"}, count=100
    )
    messages = [(mid, data) for _stream, entries in resp for mid, data in entries]
    await _flush(messages, {})

    async with get_sessionmaker()() as session:
        assert (await service.get_link(session, code)).click_count == 1
