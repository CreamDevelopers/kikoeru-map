from __future__ import annotations

from typing import Any

import orjson
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import sessionmaker
from app.models import Setting
from app.services.redis import get_redis

CACHE_KEY = "settings:v1"


class RuntimeSettings(BaseModel):
    report_threshold: float = Field(ge=1, le=100)
    vad_threshold: float = Field(ge=0.05, le=1.0)
    max_duration_sec: int = Field(ge=10, le=60)
    preapproval: bool = False
    posting_paused: bool = False
    rate_post_per_hour: int = Field(ge=1, le=1000)
    rate_report_per_hour: int = Field(ge=1, le=1000)
    rate_pins_per_minute: int = Field(ge=10, le=10000)


def defaults() -> RuntimeSettings:
    s = get_settings()
    return RuntimeSettings(
        report_threshold=s.default_report_threshold,
        vad_threshold=s.default_vad_threshold,
        max_duration_sec=s.default_max_duration_sec,
        rate_post_per_hour=s.rate_post_per_hour,
        rate_report_per_hour=s.rate_report_per_hour,
        rate_pins_per_minute=s.rate_pins_per_minute,
    )


async def load() -> RuntimeSettings:
    redis = get_redis()
    cached = await redis.get(CACHE_KEY)
    if cached:
        return RuntimeSettings.model_validate(orjson.loads(cached))
    data: dict[str, Any] = defaults().model_dump()
    async with sessionmaker()() as session:
        rows = (await session.execute(select(Setting))).scalars().all()
    for row in rows:
        if row.key in data:
            data[row.key] = row.value
    value = RuntimeSettings.model_validate(data)
    await redis.set(CACHE_KEY, orjson.dumps(value.model_dump()), ex=30)
    return value


async def save(session: AsyncSession, new: RuntimeSettings) -> None:
    for key, value in new.model_dump().items():
        stmt = insert(Setting).values(key=key, value=value)
        stmt = stmt.on_conflict_do_update(index_elements=[Setting.key], set_={"value": value})
        await session.execute(stmt)
    await session.commit()
    await get_redis().delete(CACHE_KEY)
