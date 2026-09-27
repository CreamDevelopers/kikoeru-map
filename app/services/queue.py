from __future__ import annotations

from typing import Any, Protocol

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.config import get_settings


class Enqueuer(Protocol):
    async def enqueue_job(self, function: str, *args: Any, **kwargs: Any) -> Any: ...


_pool: Enqueuer | None = None


def redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(get_settings().redis_url)


async def get_pool() -> Enqueuer:
    global _pool
    if _pool is None:
        _pool = await create_pool(redis_settings())
    return _pool


def set_pool(pool: Enqueuer | None) -> None:
    global _pool
    _pool = pool


async def close_pool() -> None:
    global _pool
    if isinstance(_pool, ArqRedis):
        await _pool.aclose()
    _pool = None


async def enqueue(function: str, *args: Any, **kwargs: Any) -> Any:
    pool = await get_pool()
    return await pool.enqueue_job(function, *args, **kwargs)
