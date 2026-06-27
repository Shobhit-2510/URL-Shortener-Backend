"""Centralised, environment-driven configuration.

The service supports two interchangeable backend profiles so the *same* code
runs under Docker (Postgres + Redis) and in a lightweight local/CI context
(SQLite + fakeredis). Backend selection is purely a function of the URLs/flags
below — there are no `if local:` branches scattered through the app.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Service ---
    app_name: str = "url-shortener"
    environment: str = Field(default="development")
    log_level: str = Field(default="INFO")
    # 301 = permanent (aggressively cached by browsers/CDNs — fewer analytics hits),
    # 302 = temporary (every click reaches us — better analytics). Configurable.
    redirect_status_code: int = Field(default=302)

    # --- Persistence ---
    # e.g. postgresql+asyncpg://user:pass@db:5432/shortener
    #      sqlite+aiosqlite:///./shortener.db
    database_url: str = Field(default="sqlite+aiosqlite:///./shortener.db")
    db_pool_size: int = Field(default=20)
    db_max_overflow: int = Field(default=10)
    # LOAD-TEST ONLY (both default to off; MUST stay off in prod/tests).
    # Faithfully emulate a networked Postgres on the cache-MISS path when
    # benchmarking on a single box with in-process SQLite (which reads from the
    # OS page cache in microseconds, so a bare cache shows no win locally):
    #   * db_read_latency_ms : per-query round-trip latency.
    #   * db_sim_pool        : max concurrent queries the "DB" serves (a
    #                          connection pool). DB throughput ceiling is then
    #                          db_sim_pool / (db_read_latency_ms/1000). The cache
    #                          lets hot reads bypass this ceiling entirely.
    # Documented in README "Load test".
    db_read_latency_ms: float = Field(default=0.0)
    db_sim_pool: int = Field(default=0)

    # --- Redis ---
    redis_url: str = Field(default="redis://localhost:6379/0")
    # When true, use an in-process fakeredis instead of a real connection.
    # Set automatically in tests / local benchmark; never in Docker.
    use_fake_redis: bool = Field(default=False)

    # --- Short codes ---
    # IDs come from a monotonic counter (DB identity/sequence). We offset the
    # counter before base62-encoding so the *first* code is already ~4 chars and
    # not a guessable "1, 2, 3". This is obfuscation, NOT security (see README).
    code_offset: int = Field(default=1_000_000)
    code_alphabet: str = Field(
        default="0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    )
    custom_alias_min_len: int = Field(default=3)
    custom_alias_max_len: int = Field(default=32)
    max_url_length: int = Field(default=2048)

    # --- Cache (cache-aside) ---
    # Toggle to bypass the cache entirely (every lookup hits Postgres). Used to
    # measure the "before cache" baseline in the load test; true in production.
    cache_enabled: bool = Field(default=True)
    cache_ttl_seconds: int = Field(default=3600)          # positive lookups
    cache_negative_ttl_seconds: int = Field(default=30)   # 404s — anti-penetration

    # --- Rate limiting (token bucket, per IP) ---
    rate_limit_enabled: bool = Field(default=True)
    rate_limit_capacity: int = Field(default=100)         # bucket size / burst
    rate_limit_refill_per_sec: float = Field(default=20.0)  # sustained rate

    # --- Analytics queue (Redis Streams) ---
    clicks_stream: str = Field(default="clicks")
    clicks_consumer_group: str = Field(default="analytics")
    clicks_stream_maxlen: int = Field(default=1_000_000)  # approximate cap
    worker_batch_size: int = Field(default=200)
    worker_block_ms: int = Field(default=2000)


@lru_cache
def get_settings() -> Settings:
    return Settings()
