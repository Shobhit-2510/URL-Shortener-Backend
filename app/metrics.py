"""Prometheus metrics definitions.

Exposed at GET /metrics (see routes). A middleware records request count and
latency; the redirect path additionally records cache hit/miss and rate-limit
rejections so the cache effectiveness is observable in Grafana, not just in a
one-off load test.
"""
from __future__ import annotations

from prometheus_client import Counter, Histogram

REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests.",
    ["method", "endpoint", "status"],
)

REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency (seconds).",
    ["method", "endpoint"],
    buckets=(0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
)

CACHE_EVENTS = Counter(
    "cache_events_total",
    "Cache-aside outcomes for code lookups.",
    ["result"],  # hit | miss | negative_hit
)

RATE_LIMITED = Counter(
    "rate_limited_total",
    "Requests rejected by the rate limiter (429).",
)

REDIRECTS = Counter(
    "redirects_total",
    "Successful redirects served.",
    ["status"],  # 301 | 302 | 410 | 404
)
