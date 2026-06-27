"""Locust load test for the redirect hot path.

Two user classes let you measure the cache's impact directly:

  * CachedRedirectUser  — hammers a SMALL set of codes. After the first request
    each code is served from Redis; this measures the cache-HIT path.
  * UniqueRedirectUser  — requests a LARGE spread of codes so most requests miss
    the cache and hit Postgres; this measures the cache-MISS path.

Usage (examples in README):
  locust -f locustfile.py --host http://localhost:8000

Headless, cache-hit profile:
  locust -f locustfile.py --host http://localhost:8000 \
      CachedRedirectUser --headless -u 100 -r 50 -t 30s --csv hit

Set SEED_CODES (comma-separated) to reuse pre-created codes; otherwise the test
seeds its own on start.
"""
from __future__ import annotations

import os
import random

from locust import HttpUser, between, events, task

# Codes seeded once and shared across users (populated in on_test_start).
SEED: list[str] = []


def _seed_codes(host: str, n: int) -> list[str]:
    import json
    import urllib.request

    codes: list[str] = []
    for i in range(n):
        body = json.dumps({"url": f"https://example.com/landing/{i}"}).encode()
        req = urllib.request.Request(
            f"{host}/api/shorten", data=body, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                codes.append(json.loads(resp.read())["code"])
        except Exception:
            pass
    return codes


@events.test_start.add_listener
def on_test_start(environment, **_kwargs):
    global SEED
    env_codes = os.getenv("SEED_CODES")
    if env_codes:
        SEED = env_codes.split(",")
        return
    host = environment.host or "http://localhost:8000"
    count = int(os.getenv("SEED_COUNT", "500"))
    SEED = _seed_codes(host, count)
    print(f"[locust] seeded {len(SEED)} codes")


class CachedRedirectUser(HttpUser):
    """Cache-HIT path: a hot set of ~10 codes served from Redis."""

    wait_time = between(0, 0)

    @task
    def hot_redirect(self):
        if not SEED:
            return
        code = random.choice(SEED[:10])
        # allow_redirects=False: we measure OUR latency, not the destination's.
        self.client.get(f"/{code}", allow_redirects=False, name="/{code} [cache hit]")


class UniqueRedirectUser(HttpUser):
    """Cache-MISS path: spread across the whole seeded set -> mostly Postgres."""

    wait_time = between(0, 0)

    @task
    def cold_redirect(self):
        if not SEED:
            return
        code = random.choice(SEED)
        self.client.get(f"/{code}", allow_redirects=False, name="/{code} [mixed/miss]")
