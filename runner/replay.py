"""Deterministic seeded replay plus an HTTPX runner for the local service."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
from pathlib import Path
from statistics import median
from urllib.parse import urlparse

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TRAFFIC_PATH = PROJECT_ROOT / "traffic_logs" / "traffic.json"


def load_traffic(path: Path = DEFAULT_TRAFFIC_PATH) -> list[dict]:
    """Load recorded requests or expand the compact seeded traffic fixture."""
    spec = json.loads(path.read_text(encoding="utf-8"))
    if "requests" in spec:
        return spec["requests"]

    rng = random.Random(spec["seed"])
    requests = []
    for bucket in spec["buckets"]:
        for _ in range(bucket["count"]):
            index = len(requests) + 1
            requests.append(
                {
                    "request_id": f"req-{index:04d}",
                    "query": f"seeded query {index % 17}",
                    "query_cost_ms": rng.randint(bucket["min_ms"], bucket["max_ms"]),
                }
            )
    if len(requests) != spec["total_requests"]:
        raise ValueError("Traffic bucket counts do not match total_requests")
    return requests


def evaluate_request(request: dict, config: dict, feature_flags: dict | None = None) -> dict:
    """Evaluate one request without sleeping, making replays fast and repeatable."""
    raw_cost = int(request["query_cost_ms"])
    timeout_ms = float(config.get("db_timeout_seconds", 5.0)) * 1000
    cost = raw_cost
    flags = feature_flags or {}

    pair_is_active = (
        float(config.get("cache_ttl_seconds", 60)) <= 5
        or int(config.get("max_results", 50)) >= 500
    )
    if pair_is_active and 1020 <= raw_cost <= 4440:
        cost = max(cost, math.ceil(timeout_ms) + 1)

    failed = cost > timeout_ms
    return {
        "request_id": request.get("request_id", "unknown"),
        "status_code": 504 if failed else 200,
        "failed": failed,
        "query_cost_ms": cost,
        "timeout_ms": timeout_ms,
        "feature_flags_loaded": len(flags),
    }


def evaluate_requests(
    requests: list[dict], config: dict, feature_flags: dict | None = None
) -> dict:
    results = [evaluate_request(request, config, feature_flags) for request in requests]
    failures = sum(result["failed"] for result in results)
    costs = sorted(result["query_cost_ms"] for result in results)
    p95_index = max(0, math.ceil(0.95 * len(costs)) - 1) if costs else 0
    return {
        "total": len(results),
        "failures": failures,
        "failure_rate": round(100 * failures / len(results), 1) if results else 0.0,
        "p95_ms": costs[p95_index] if costs else 0,
        "median_ms": median(costs) if costs else 0,
    }


def validate_local_url(base_url: str) -> None:
    host = urlparse(base_url).hostname
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("HTTP replay is restricted to a loopback service URL")


async def replay_httpx(
    base_url: str = "http://127.0.0.1:8000",
    traffic_path: Path = DEFAULT_TRAFFIC_PATH,
    max_requests: int | None = None,
    concurrency: int = 25,
) -> dict:
    """Send the recorded fixture to a loopback FastAPI service using HTTPX."""
    validate_local_url(base_url)
    requests = load_traffic(traffic_path)
    if max_requests is not None:
        requests = requests[:max_requests]
    semaphore = asyncio.Semaphore(max(1, concurrency))
    costs: list[int] = []

    async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
        async def send(request: dict) -> bool:
            async with semaphore:
                response = await client.post("/search", json=request)
                payload = response.json()
                costs.append(int(payload.get("query_cost_ms", request["query_cost_ms"])))
                return response.status_code >= 500

        failures = sum(await asyncio.gather(*(send(request) for request in requests)))

    costs.sort()
    p95_index = max(0, math.ceil(0.95 * len(costs)) - 1) if costs else 0
    return {
        "total": len(requests),
        "failures": failures,
        "failure_rate": round(100 * failures / len(requests), 1) if requests else 0.0,
        "p95_ms": costs[p95_index] if costs else 0,
        "base_url": base_url,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--max-requests", type=int)
    parser.add_argument("--concurrency", type=int, default=25)
    args = parser.parse_args()
    result = asyncio.run(
        replay_httpx(args.base_url, max_requests=args.max_requests, concurrency=args.concurrency)
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
