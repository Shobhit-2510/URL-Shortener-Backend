"""End-to-end flow tests against the ASGI app (httpx ASGITransport).

Covers: shorten -> redirect, cache hit on second request, custom alias +
collision (409), URL validation (422), expiry (410), unknown code (404),
rate-limit headers, and that a click event lands on the Redis Stream.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.config import get_settings
from app.main import create_app
from app.redis_client import get_redis

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def client(db, redis):
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=False
    ) as ac:
        # Trigger lifespan (init_models) — ASGITransport doesn't run it.
        async with app.router.lifespan_context(app):
            yield ac


async def test_shorten_and_redirect(client) -> None:
    settings = get_settings()
    r = await client.post("/api/shorten", json={"url": "https://example.com/page"})
    assert r.status_code == 201
    body = r.json()
    code = body["code"]
    assert body["short_url"].endswith(f"/{code}")

    r2 = await client.get(f"/{code}")
    assert r2.status_code == settings.redirect_status_code
    assert r2.headers["location"] == "https://example.com/page"


async def test_second_redirect_is_cache_hit(client) -> None:
    r = await client.post("/api/shorten", json={"url": "https://example.com/x"})
    code = r.json()["code"]
    # Write path warms the cache, so even the first GET is a hit; do two GETs.
    await client.get(f"/{code}")
    await client.get(f"/{code}")
    metrics = (await client.get("/metrics")).text
    assert 'cache_events_total{result="hit"}' in metrics


async def test_custom_alias_and_collision(client) -> None:
    r = await client.post(
        "/api/shorten", json={"url": "https://example.com", "custom_alias": "promo"}
    )
    assert r.status_code == 201
    assert r.json()["code"] == "promo"

    dup = await client.post(
        "/api/shorten", json={"url": "https://other.com", "custom_alias": "promo"}
    )
    assert dup.status_code == 409


async def test_invalid_url_rejected(client) -> None:
    bad = await client.post("/api/shorten", json={"url": "ftp://nope.com"})
    assert bad.status_code == 422
    missing_host = await client.post("/api/shorten", json={"url": "http://"})
    assert missing_host.status_code == 422


async def test_unknown_code_404(client) -> None:
    assert (await client.get("/doesnotexist")).status_code == 404


async def test_expired_link_returns_410(client) -> None:
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    r = await client.post(
        "/api/shorten", json={"url": "https://example.com", "expires_at": past}
    )
    code = r.json()["code"]
    # Drop the warm cache entry so the DB expiry check runs.
    await get_redis().delete(f"url:{code}")
    resp = await client.get(f"/{code}")
    assert resp.status_code == 410


async def test_rate_limit_headers_present(client) -> None:
    r = await client.get("/doesnotexist")
    assert "X-RateLimit-Limit" in r.headers
    assert "X-RateLimit-Remaining" in r.headers


async def test_click_event_enqueued(client) -> None:
    settings = get_settings()
    r = await client.post("/api/shorten", json={"url": "https://example.com/click"})
    code = r.json()["code"]
    await client.get(f"/{code}")
    length = await get_redis().xlen(settings.clicks_stream)
    assert length >= 1


async def test_health_and_metrics(client) -> None:
    h = await client.get("/health")
    assert h.status_code == 200
    assert h.json()["checks"]["database"] == "ok"
    m = await client.get("/metrics")
    assert m.status_code == 200
    assert "http_requests_total" in m.text


async def test_root_serves_frontend(client) -> None:
    r = await client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    assert "TrimURL" in r.text
