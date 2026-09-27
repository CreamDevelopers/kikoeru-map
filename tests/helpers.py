from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from app import worker
from app.db import sessionmaker
from app.models import ProcessingJob, Sound

from .conftest import FakeQueue, ip_headers, unique_ip


async def upload(
    client: httpx.AsyncClient,
    path: Path,
    *,
    filename: str | None = None,
    content_type: str = "audio/wav",
    ip: str | None = None,
    **fields: str,
) -> httpx.Response:
    data = {
        "title": "テストの音",
        "lat": "35.681",
        "lng": "139.767",
        "agree": "true",
        "cf-turnstile-response": "ok",
        "tags": "city",
    }
    data.update(fields)
    return await client.post(
        "/api/sounds",
        data=data,
        files={"file": (filename or path.name, path.read_bytes(), content_type)},
        headers=ip_headers(ip or unique_ip()),
    )


async def run_process(fake_queue: FakeQueue, sound_id: str) -> Sound:
    async with sessionmaker()() as session:
        from sqlalchemy import select

        job = (
            await session.execute(select(ProcessingJob).where(ProcessingJob.sound_id == sound_id))
        ).scalar_one()
    ctx: dict[str, Any] = {"job_try": 1, "redis": fake_queue}
    await worker.process_sound(ctx, job.id)
    async with sessionmaker()() as session:
        sound = await session.get(Sound, sound_id)
        assert sound is not None
        return sound


async def upload_and_process(
    client: httpx.AsyncClient, fake_queue: FakeQueue, path: Path, **kw: Any
) -> Sound:
    r = await upload(client, path, **kw)
    assert r.status_code == 202, r.text
    return await run_process(fake_queue, r.json()["id"])
