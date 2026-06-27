"""Async load harness for the redirect hot path.

Equivalent in intent to locustfile.py (concurrent users hammering GET /{code}),
but built on asyncio+httpx so it runs cleanly on Python 3.14, where Locust's
gevent dependency is not yet supported. The Dockerized stack (Python 3.12) uses
Locust; this harness produced the numbers in the README on the local machine.

It seeds N codes, then drives `concurrency` workers against a HOT subset for
`duration` seconds, and reports throughput + latency percentiles as JSON.

  python loadtest/benchmark.py --host http://127.0.0.1:8000 \
      --seed 500 --hot 10 --concurrency 50 --duration 20 --label after
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time

import httpx


async def seed(client: httpx.AsyncClient, host: str, n: int) -> list[str]:
    codes: list[str] = []
    for i in range(n):
        r = await client.post(f"{host}/api/shorten", json={"url": f"https://example.com/p/{i}"})
        if r.status_code == 201:
            codes.append(r.json()["code"])
    return codes


async def worker(
    client: httpx.AsyncClient,
    host: str,
    codes: list[str],
    deadline: float,
    latencies: list[float],
    counters: dict[str, int],
    idx: int,
) -> None:
    n = len(codes)
    i = idx
    while time.perf_counter() < deadline:
        code = codes[i % n]
        i += 1
        t0 = time.perf_counter()
        try:
            r = await client.get(f"{host}/{code}", follow_redirects=False)
            latencies.append((time.perf_counter() - t0) * 1000.0)
            counters[str(r.status_code)] = counters.get(str(r.status_code), 0) + 1
        except Exception:
            counters["error"] = counters.get("error", 0) + 1


def pct(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    k = min(len(sorted_vals) - 1, int(round((p / 100.0) * (len(sorted_vals) - 1))))
    return sorted_vals[k]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="http://127.0.0.1:8000")
    ap.add_argument("--seed", type=int, default=500)
    ap.add_argument("--hot", type=int, default=10, help="size of the hot (cacheable) set")
    ap.add_argument("--concurrency", type=int, default=50)
    ap.add_argument("--duration", type=float, default=20.0)
    ap.add_argument("--label", default="run")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    pool = args.concurrency * 2
    limits = httpx.Limits(max_connections=pool, max_keepalive_connections=pool)
    async with httpx.AsyncClient(limits=limits, timeout=10.0) as client:
        all_codes = await seed(client, args.host, args.seed)
        if not all_codes:
            raise SystemExit("seeding failed — is the server up?")
        hot = all_codes[: args.hot]

        # Warm the hot set once so steady-state is measured (not the first miss).
        for c in hot:
            await client.get(f"{args.host}/{c}", follow_redirects=False)

        latencies: list[float] = []
        counters: dict[str, int] = {}
        deadline = time.perf_counter() + args.duration
        start = time.perf_counter()
        await asyncio.gather(
            *[
                worker(client, args.host, hot, deadline, latencies, counters, i)
                for i in range(args.concurrency)
            ]
        )
        elapsed = time.perf_counter() - start

    latencies.sort()
    total = len(latencies)
    result = {
        "label": args.label,
        "concurrency": args.concurrency,
        "duration_s": round(elapsed, 2),
        "requests": total,
        "throughput_rps": round(total / elapsed, 1) if elapsed else 0,
        "latency_ms": {
            "mean": round(sum(latencies) / total, 2) if total else 0,
            "p50": round(pct(latencies, 50), 2),
            "p95": round(pct(latencies, 95), 2),
            "p99": round(pct(latencies, 99), 2),
            "max": round(latencies[-1], 2) if latencies else 0,
        },
        "status_counts": counters,
    }
    print(json.dumps(result, indent=2))
    if args.out:
        with open(args.out, "w") as fh:
            json.dump(result, fh, indent=2)


if __name__ == "__main__":
    asyncio.run(main())
