"""Bounded HTTP load smoke test for a deployed FinSight operations route."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx


@dataclass(frozen=True, slots=True)
class RequestResult:
    """One request outcome without response content or credentials."""

    duration_ms: float
    succeeded: bool


async def execute_request(
    client: httpx.AsyncClient,
    *,
    path: str,
    semaphore: asyncio.Semaphore,
    headers: dict[str, str],
) -> RequestResult:
    """Execute one bounded GET and retain only latency and success state."""

    async with semaphore:
        started_at = time.perf_counter()
        try:
            response = await client.get(path, headers=headers)
            succeeded = 200 <= response.status_code < 300
        except httpx.HTTPError:
            succeeded = False
        return RequestResult(
            duration_ms=(time.perf_counter() - started_at) * 1_000,
            succeeded=succeeded,
        )


def percentile(values: list[float], probability: float) -> float:
    """Return the deterministic nearest-rank percentile for a non-empty sample."""

    ordered = sorted(values)
    index = max(0, math.ceil(probability * len(ordered)) - 1)
    return ordered[index]


async def run_load_smoke(
    *,
    base_url: str,
    path: str,
    requests: int,
    concurrency: int,
    timeout_seconds: float,
    token: str | None,
) -> dict[str, object]:
    """Run a bounded workload and return a machine-readable summary."""

    semaphore = asyncio.Semaphore(concurrency)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout_seconds) as client:
        results = await asyncio.gather(
            *(
                execute_request(
                    client,
                    path=path,
                    semaphore=semaphore,
                    headers=headers,
                )
                for _ in range(requests)
            )
        )

    durations = [result.duration_ms for result in results]
    succeeded = sum(result.succeeded for result in results)
    return {
        "requests": requests,
        "concurrency": concurrency,
        "succeeded": succeeded,
        "failed": requests - succeeded,
        "success_rate": succeeded / requests,
        "latency_ms": {
            "min": min(durations),
            "p50": percentile(durations, 0.50),
            "p95": percentile(durations, 0.95),
            "max": max(durations),
        },
    }


def parser() -> argparse.ArgumentParser:
    """Build the bounded load-smoke command contract."""

    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument("--base-url", default="http://127.0.0.1:8000")
    command.add_argument("--path", default="/health")
    command.add_argument("--requests", type=int, default=100)
    command.add_argument("--concurrency", type=int, default=10)
    command.add_argument("--timeout-seconds", type=float, default=5.0)
    command.add_argument("--max-p95-ms", type=float, default=500.0)
    return command


def main() -> int:
    """Validate inputs, run the workload, print JSON, and enforce thresholds."""

    arguments = parser().parse_args()
    parsed_url = urlparse(arguments.base_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise SystemExit("--base-url must be an absolute HTTP or HTTPS URL")
    if parsed_url.username or parsed_url.password:
        raise SystemExit("--base-url cannot contain credentials")
    if not arguments.path.startswith("/") or "?" in arguments.path:
        raise SystemExit("--path must be an absolute path without a query string")
    if not 1 <= arguments.requests <= 10_000:
        raise SystemExit("--requests must be between 1 and 10000")
    if not 1 <= arguments.concurrency <= min(arguments.requests, 500):
        raise SystemExit("--concurrency must be between 1 and the request count")

    report = asyncio.run(
        run_load_smoke(
            base_url=arguments.base_url,
            path=arguments.path,
            requests=arguments.requests,
            concurrency=arguments.concurrency,
            timeout_seconds=arguments.timeout_seconds,
            token=os.getenv("FINSIGHT_API_AUTH_TOKEN"),
        )
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    latency = report["latency_ms"]
    assert isinstance(latency, dict)
    p95 = latency["p95"]
    assert isinstance(p95, float)
    return 0 if report["failed"] == 0 and p95 <= arguments.max_p95_ms else 1


if __name__ == "__main__":
    raise SystemExit(main())
