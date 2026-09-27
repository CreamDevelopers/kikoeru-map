from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

from fastapi import HTTPException

from app.services.redis import get_redis

_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local count = redis.call('ZCARD', key)
if count >= limit then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  local retry = window
  if oldest[2] then retry = tonumber(oldest[2]) + window - now end
  return {0, count, retry}
end
redis.call('ZADD', key, now, member)
redis.call('PEXPIRE', key, window)
return {1, count + 1, 0}
"""


@dataclass
class RateResult:
    allowed: bool
    count: int
    retry_after_ms: int


async def hit(bucket: str, ident: str, limit: int, window_seconds: int) -> RateResult:
    redis = get_redis()
    now_ms = int(time.time() * 1000)
    member = f"{now_ms}-{secrets.token_hex(4)}"
    res = await redis.eval(_SCRIPT, 1, f"rl:{bucket}:{ident}", now_ms, window_seconds * 1000, limit, member)
    allowed, count, retry = (int(x) for x in res)
    return RateResult(bool(allowed), count, retry)


async def enforce(bucket: str, ident: str, limit: int, window_seconds: int) -> None:
    result = await hit(bucket, ident, limit, window_seconds)
    if not result.allowed:
        retry_s = max(1, result.retry_after_ms // 1000)
        raise HTTPException(
            status_code=429,
            detail={"code": "rate_limited", "retry_after": retry_s},
            headers={"Retry-After": str(retry_s)},
        )


async def reset(bucket: str, ident: str) -> None:
    await get_redis().delete(f"rl:{bucket}:{ident}")
