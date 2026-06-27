"""Analytics worker — the asynchronous half of the click pipeline.

Runs as its own process/container. It consumes click events from the Redis
Stream using a **consumer group** (so work is partitioned and at-least-once
delivered, with explicit ACKs), batches them, and folds them into Postgres:

    * links.click_count        -- running total per link
    * click_stats(link_id,day) -- per-day buckets

Why a consumer group (XREADGROUP) instead of a plain list (LPUSH/BRPOP)?
  * At-least-once delivery: unacked entries stay in the Pending Entries List, so
    a crash mid-batch doesn't lose clicks — they're redelivered.
  * Horizontal scale: add more worker replicas to the same group and Redis fans
    entries out across them.

Aggregating in batches (one UPDATE per link per flush, not per click) is what
keeps Postgres write load proportional to the number of *distinct links*, not
the raw click volume.
"""
from __future__ import annotations

import asyncio
import signal
from collections import defaultdict
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.database import dispose_engine, get_sessionmaker, init_models
from app.logging_config import configure_logging
from app.models import ClickStat, Link
from app.redis_client import close_redis, get_redis

log = structlog.get_logger(__name__)

_CONSUMER_NAME = "worker-1"
_shutdown = asyncio.Event()


async def _ensure_group(redis) -> None:
    settings = get_settings()
    try:
        # MKSTREAM creates the stream if it doesn't exist yet; id="0" reads from
        # the beginning the first time the group is created.
        await redis.xgroup_create(
            settings.clicks_stream, settings.clicks_consumer_group, id="0", mkstream=True
        )
        log.info("consumer_group_created", group=settings.clicks_consumer_group)
    except Exception as exc:
        # BUSYGROUP -> group already exists; any other error is real.
        if "BUSYGROUP" not in str(exc):
            raise


async def _resolve_link_id(session, code: str, cache: dict[str, int]) -> int | None:
    """code -> link_id, memoised (codes map immutably to ids)."""
    if code in cache:
        return cache[code]
    link_id = (await session.execute(select(Link.id).where(Link.code == code))).scalar_one_or_none()
    if link_id is not None:
        cache[code] = link_id
    return link_id


async def _flush(messages: list[tuple[str, dict]], code_cache: dict[str, int]) -> None:
    """Aggregate a batch of click events and persist them."""
    if not messages:
        return

    # Aggregate in memory first: (link_id) -> total, (link_id, day) -> total.
    totals: dict[int, int] = defaultdict(int)
    per_day: dict[tuple[int, str], int] = defaultdict(int)

    async with get_sessionmaker()() as session:
        for _msg_id, data in messages:
            code = data.get("code", "")
            raw_id = int(data.get("link_id", "-1"))
            link_id = raw_id if raw_id > 0 else await _resolve_link_id(session, code, code_cache)
            if link_id is None:
                continue
            ts = float(data.get("ts", "0"))
            day = datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%d")
            totals[link_id] += 1
            per_day[(link_id, day)] += 1

        # Running totals: one UPDATE per distinct link.
        for link_id, delta in totals.items():
            await session.execute(
                Link.__table__.update().where(Link.id == link_id).values(
                    click_count=Link.click_count + delta
                )
            )

        # Per-day upserts (Postgres ON CONFLICT; SQLite handled by fallback).
        for (link_id, day), delta in per_day.items():
            await _upsert_day(session, link_id, day, delta)

        await session.commit()

    log.info("batch_flushed", events=len(messages), links=len(totals))


async def _upsert_day(session, link_id: int, day: str, delta: int) -> None:
    bind = session.get_bind()
    if bind.dialect.name == "postgresql":
        stmt = pg_insert(ClickStat).values(link_id=link_id, day=day, count=delta)
        stmt = stmt.on_conflict_do_update(
            index_elements=[ClickStat.link_id, ClickStat.day],
            set_={"count": ClickStat.count + delta},
        )
        await session.execute(stmt)
    else:
        # SQLite / fallback: read-modify-write (single-writer worker, so safe).
        existing = (
            await session.execute(
                select(ClickStat).where(ClickStat.link_id == link_id, ClickStat.day == day)
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(ClickStat(link_id=link_id, day=day, count=delta))
            try:
                await session.flush()
            except IntegrityError:
                await session.rollback()
        else:
            existing.count += delta


async def run() -> None:
    configure_logging()
    settings = get_settings()
    await init_models()
    redis = get_redis()
    await _ensure_group(redis)
    code_cache: dict[str, int] = {}
    log.info("worker_started", stream=settings.clicks_stream)

    while not _shutdown.is_set():
        try:
            resp = await redis.xreadgroup(
                settings.clicks_consumer_group,
                _CONSUMER_NAME,
                {settings.clicks_stream: ">"},
                count=settings.worker_batch_size,
                block=settings.worker_block_ms,
            )
        except Exception as exc:  # pragma: no cover - transient redis errors
            log.warning("xreadgroup_failed", error=str(exc))
            await asyncio.sleep(1)
            continue

        if not resp:
            continue  # block timed out with no new entries

        for _stream, entries in resp:
            messages = [(msg_id, data) for msg_id, data in entries]
            await _flush(messages, code_cache)
            # ACK only after a successful flush — at-least-once semantics.
            await redis.xack(
                settings.clicks_stream,
                settings.clicks_consumer_group,
                *[msg_id for msg_id, _ in messages],
            )

    await close_redis()
    await dispose_engine()
    log.info("worker_stopped")


def _install_signal_handlers(loop: asyncio.AbstractEventLoop) -> None:
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown.set)
        except NotImplementedError:  # pragma: no cover - Windows
            signal.signal(sig, lambda *_: _shutdown.set())


def main() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _install_signal_handlers(loop)
    loop.run_until_complete(run())


if __name__ == "__main__":
    main()
