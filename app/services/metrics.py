from __future__ import annotations

import os

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)
from prometheus_client import multiprocess as prom_mp

from app.services.redis import get_redis

REQUESTS = Counter("kikoeru_http_requests_total", "HTTP requests", ["method", "route", "status"])
LATENCY = Histogram(
    "kikoeru_http_request_duration_seconds",
    "HTTP request latency",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.02, 0.05, 0.1, 0.15, 0.25, 0.5, 1.0, 2.5, 5.0),
)

# worker は別コンテナなので、ジョブ件数は Redis に数えて /metrics で合わせて出す
JOB_KEY = "metrics:jobs"


async def record_job(name: str, status: str) -> None:
    await get_redis().hincrby(JOB_KEY, f"{name}|{status}", 1)


def _registry() -> CollectorRegistry | None:
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        reg = CollectorRegistry()
        prom_mp.MultiProcessCollector(reg)
        return reg
    return None


async def render() -> tuple[bytes, str]:
    reg = _registry()
    body = generate_latest(reg) if reg is not None else generate_latest()
    jobs = await get_redis().hgetall(JOB_KEY)
    lines = [
        "# HELP kikoeru_jobs_total Background jobs processed by the worker",
        "# TYPE kikoeru_jobs_total counter",
    ]
    for key, value in sorted(jobs.items()):
        name, _, status = key.decode().partition("|")
        lines.append(f'kikoeru_jobs_total{{job="{name}",status="{status}"}} {int(value)}')
    return body + ("\n".join(lines) + "\n").encode(), CONTENT_TYPE_LATEST
