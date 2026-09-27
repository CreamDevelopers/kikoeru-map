from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any

import orjson

from app.services.redis import get_redis

log = logging.getLogger(__name__)

PREFIX = "ev:"
CHANNEL_NEW = "new"
STATUS_TTL = 60 * 60 * 24


def sound_channel(sound_id: str) -> str:
    return f"sound:{sound_id}"


async def publish(channel: str, payload: dict[str, Any]) -> None:
    await get_redis().publish(PREFIX + channel, orjson.dumps(payload))


async def set_status(sound_id: str, payload: dict[str, Any]) -> None:
    data = orjson.dumps(payload)
    redis = get_redis()
    await redis.set(f"status:{sound_id}", data, ex=STATUS_TTL)
    await redis.publish(PREFIX + sound_channel(sound_id), data)


async def get_status(sound_id: str) -> dict[str, Any] | None:
    raw = await get_redis().get(f"status:{sound_id}")
    return orjson.loads(raw) if raw else None


class Broadcaster:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue[bytes]]] = defaultdict(set)
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        self._task = None

    async def _run(self) -> None:
        while True:
            pubsub = get_redis().pubsub()
            try:
                await pubsub.psubscribe(PREFIX + "*")
                async for msg in pubsub.listen():
                    if msg.get("type") != "pmessage":
                        continue
                    channel = msg["channel"].decode()[len(PREFIX) :]
                    for q in list(self._subs.get(channel, ())):
                        if q.qsize() < 100:
                            q.put_nowait(msg["data"])
            except asyncio.CancelledError:
                await pubsub.aclose()
                raise
            except Exception:
                log.exception("broadcaster connection lost; retrying")
                with contextlib.suppress(Exception):
                    await pubsub.aclose()
                await asyncio.sleep(1)

    @contextlib.asynccontextmanager
    async def subscribe(self, channel: str) -> AsyncIterator[asyncio.Queue[bytes]]:
        q: asyncio.Queue[bytes] = asyncio.Queue()
        self._subs[channel].add(q)
        try:
            yield q
        finally:
            self._subs[channel].discard(q)
            if not self._subs[channel]:
                self._subs.pop(channel, None)


broadcaster = Broadcaster()
