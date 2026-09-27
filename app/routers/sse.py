from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import orjson
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.db import sessionmaker
from app.models import Sound
from app.routers.api import ID_RE
from app.services import events
from app.services.events import broadcaster

router = APIRouter(prefix="/api", tags=["sse"])

HEARTBEAT = 15.0
TERMINAL = {"published", "pending_review", "rejected", "failed"}
SSE_HEADERS = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}


def _event(data: bytes, event: str | None = None) -> bytes:
    head = f"event: {event}\n".encode() if event else b""
    return head + b"data: " + data + b"\n\n"


@router.get("/sounds/{sound_id}/events")
async def sound_events(sound_id: str, request: Request) -> StreamingResponse:
    if not ID_RE.match(sound_id):
        raise HTTPException(404, detail={"code": "not_found"})

    async def stream() -> AsyncIterator[bytes]:
        async with broadcaster.subscribe(events.sound_channel(sound_id)) as q:
            current = await events.get_status(sound_id)
            if current is None:
                async with sessionmaker()() as session:
                    sound = await session.get(Sound, sound_id)
                if sound is None:
                    yield _event(b'{"state":"unknown"}', "status")
                    return
                current = {"state": sound.status, "reason": sound.reject_reason or sound.hidden_reason}
            yield b"retry: 3000\n" + _event(orjson.dumps(current), "status")
            if current.get("state") in TERMINAL:
                return
            while not await request.is_disconnected():
                try:
                    data = await asyncio.wait_for(q.get(), timeout=HEARTBEAT)
                except TimeoutError:
                    yield b": ping\n\n"
                    continue
                yield _event(data, "status")
                if orjson.loads(data).get("state") in TERMINAL:
                    return

    return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.get("/stream")
async def new_sounds(request: Request) -> StreamingResponse:
    async def stream() -> AsyncIterator[bytes]:
        async with broadcaster.subscribe(events.CHANNEL_NEW) as q:
            yield b"retry: 5000\n: connected\n\n"
            while not await request.is_disconnected():
                try:
                    data = await asyncio.wait_for(q.get(), timeout=HEARTBEAT)
                except TimeoutError:
                    yield b": ping\n\n"
                    continue
                yield _event(data, "sound")

    return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)
