from __future__ import annotations

import datetime as dt
import random
from typing import Any

import orjson
from geoalchemy2 import Geography, WKTElement
from sqlalchemy import Select, and_, cast, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DailyStat, Sound, SoundStatus, utcnow
from app.schemas import TAG_CODES
from app.services import cache, events, media
from app.services.geo import region_pref_codes

OVERLAP_RADIUS_M = 50
MAX_PINS = 60_000


def point(lat: float, lng: float) -> WKTElement:
    return WKTElement(f"POINT({lng} {lat})", srid=4326)


def envelope(west: float, south: float, east: float, north: float) -> Any:
    return cast(func.ST_MakeEnvelope(west, south, east, north, 4326), Geography(srid=4326))


def serialize(s: Sound, *, include_location: bool = True) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": s.id,
        "title": s.title,
        "comment": s.comment,
        "recorded_at": s.recorded_at.isoformat() if s.recorded_at else None,
        "time_of_day": s.time_of_day,
        "weather": s.weather,
        "season": s.season,
        "tags": list(s.tags or []),
        "direction": s.direction,
        "license": s.license,
        "pref_name": s.pref_name,
        "city_name": s.city_name,
        "duration_sec": s.duration_sec,
        "webm_url": media.url(s.id, s.webm_hash, "webm"),
        "m4a_url": media.url(s.id, s.m4a_hash, "m4a"),
        "peaks_url": media.url(s.id, s.peaks_hash, "json"),
        "play_count": s.play_count,
        "nearby_count": s.nearby_count,
        "published_at": s.published_at.isoformat() if s.published_at else None,
        "page_url": f"/s/{s.id}",
    }
    if include_location:
        data.update(lat=s.lat, lng=s.lng, precision=s.precision)
    return data


async def pins_payload(session: AsyncSession, west: float, south: float, east: float, north: float) -> bytes:
    stmt = (
        select(Sound.id, Sound.lat, Sound.lng, Sound.tags, Sound.nearby_count)
        .where(
            Sound.status == SoundStatus.PUBLISHED,
            Sound.location.op("&&")(envelope(west, south, east, north)),
        )
        .order_by(Sound.published_at.desc())
        .limit(MAX_PINS)
    )
    rows = (await session.execute(stmt)).all()
    pins = [
        [
            r.id,
            r.lat,
            r.lng,
            TAG_CODES.get(r.tags[0], TAG_CODES["other"]) if r.tags else TAG_CODES["other"],
            1 if r.nearby_count > 0 else 0,
        ]
        for r in rows
    ]
    return orjson.dumps({"f": ["id", "lat", "lng", "tag", "flags"], "p": pins})


async def heat_points(session: AsyncSession) -> bytes:
    stmt = text(
        """
        SELECT round(lat::numeric, 2) AS la, round(lng::numeric, 2) AS ln, count(*) AS c
        FROM sounds WHERE status = 'published'
        GROUP BY 1, 2
        """
    )
    rows = (await session.execute(stmt)).all()
    return orjson.dumps({"p": [[float(r.la), float(r.ln), int(r.c)] for r in rows]})


def public_query() -> Select[Sound]:
    return select(Sound).where(Sound.status == SoundStatus.PUBLISHED)


async def nearby(session: AsyncSession, lat: float, lng: float, radius_m: float, limit: int) -> list[Sound]:
    pt = point(lat, lng)
    stmt = (
        public_query()
        .where(func.ST_DWithin(Sound.location, pt, radius_m))
        .order_by(Sound.location.op("<->")(pt))
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


async def same_place(session: AsyncSession, sound: Sound) -> list[Sound]:
    stmt = (
        public_query()
        .where(
            func.ST_DWithin(Sound.location, point(sound.lat, sound.lng), OVERLAP_RADIUS_M),
        )
        .order_by(Sound.recorded_at.desc().nulls_last())
        .limit(30)
    )
    return list((await session.execute(stmt)).scalars().all())


async def refresh_nearby_counts(session: AsyncSession, lat: float, lng: float) -> None:
    await session.execute(
        text(
            """
            UPDATE sounds s SET nearby_count = (
                SELECT count(*) FROM sounds o
                WHERE o.status = 'published' AND o.id <> s.id
                  AND ST_DWithin(o.location, s.location, :r)
            )
            WHERE ST_DWithin(s.location, ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography, :r)
            """
        ),
        {"lat": lat, "lng": lng, "r": OVERLAP_RADIUS_M},
    )


async def random_pick(
    session: AsyncSession,
    *,
    region: str | None = None,
    weather: str | None = None,
    time_of_day: str | None = None,
    season: str | None = None,
    tag: str | None = None,
    exclude: list[str] | None = None,
    game_only: bool = False,
) -> Sound | None:
    # ORDER BY random() の全件ソートを避け、インデックス付きの乱数列 rand で選ぶ
    conds = [Sound.status == SoundStatus.PUBLISHED]
    if game_only:
        conds += [Sound.game_ok.is_(True), Sound.precision == "exact"]
    if region:
        codes = region_pref_codes(region)
        if codes:
            conds.append(Sound.pref_code.in_(codes))
    if weather:
        conds.append(Sound.weather == weather)
    if time_of_day:
        conds.append(Sound.time_of_day == time_of_day)
    if season:
        conds.append(Sound.season == season)
    if tag:
        conds.append(Sound.tags.contains([tag]))
    if exclude:
        conds.append(Sound.id.not_in(exclude[:50]))
    r = random.random()
    for cond in (Sound.rand >= r, Sound.rand < r):
        stmt = select(Sound).where(and_(*conds, cond)).order_by(Sound.rand).limit(1)
        found = (await session.execute(stmt)).scalar_one_or_none()
        if found:
            return found
    return None


async def bump_daily(
    session: AsyncSession, *, posts: int = 0, plays: int = 0, day: dt.date | None = None
) -> None:
    from sqlalchemy.dialects.postgresql import insert

    day = day or utcnow().astimezone(dt.timezone(dt.timedelta(hours=9))).date()
    stmt = insert(DailyStat).values(date=day, posts=posts, plays=plays)
    stmt = stmt.on_conflict_do_update(
        index_elements=[DailyStat.date],
        set_={"posts": DailyStat.posts + posts, "plays": DailyStat.plays + plays},
    )
    await session.execute(stmt)


async def publish(session: AsyncSession, sound: Sound) -> None:
    first_time = sound.published_at is None
    sound.status = SoundStatus.PUBLISHED
    sound.hidden_reason = None
    if first_time:
        sound.published_at = utcnow()
        await bump_daily(session, posts=1)
    await session.flush()
    await refresh_nearby_counts(session, sound.lat, sound.lng)


async def after_visibility_change(sound: Sound, *, announce: bool = False) -> None:
    await cache.invalidate_point(sound.lat, sound.lng)
    await media_state_changed(sound.id)
    if announce:
        await events.publish(
            events.CHANNEL_NEW,
            {
                "id": sound.id,
                "lat": sound.lat,
                "lng": sound.lng,
                "tag": TAG_CODES.get(sound.tags[0], 6) if sound.tags else 6,
                "title": sound.title,
            },
        )


async def media_state_changed(sound_id: str) -> None:
    from app.services.redis import get_redis

    await get_redis().delete(f"mediaok:{sound_id}")


async def set_status(session: AsyncSession, sound: Sound, status: str, reason: str | None = None) -> None:
    was_public = sound.status == SoundStatus.PUBLISHED
    if status == SoundStatus.PUBLISHED:
        await publish(session, sound)
    else:
        sound.status = status
        sound.hidden_reason = reason
        if status == SoundStatus.DELETED:
            sound.deleted_at = utcnow()
        await session.flush()
        if was_public:
            await refresh_nearby_counts(session, sound.lat, sound.lng)


async def flush_play_counts(session: AsyncSession, counts: dict[str, int]) -> int:
    total = 0
    for sound_id, n in counts.items():
        await session.execute(
            update(Sound).where(Sound.id == sound_id).values(play_count=Sound.play_count + n)
        )
        total += n
    if total:
        await bump_daily(session, plays=total)
    return total
