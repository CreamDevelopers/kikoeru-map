from __future__ import annotations

import datetime as dt
import re
import secrets
from typing import Annotated, Any

import orjson
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.db import get_session, sessionmaker
from app.models import Course, CourseItem, ProcessingJob, Report, Sound, SoundStatus
from app.schemas import (
    License,
    Precision,
    ReportIn,
    Season,
    SoundEdit,
    SoundMeta,
    Tag,
    TimeOfDay,
    Weather,
)
from app.services import cache, events, moderation, ratelimit, runtime_settings
from app.services import sounds as sound_svc
from app.services.geo import PREFECTURES, REGIONS, blur_point, in_japan
from app.services.queue import enqueue
from app.services.redis import get_redis
from app.services.security import ip_hash_of, new_sound_id, new_token, sha256_hex, token_matches
from app.services.turnstile import TurnstileResult, require_turnstile

router = APIRouter(prefix="/api", tags=["api"])

ID_RE = re.compile(r"^[A-Za-z0-9]{8,16}$")
PLAY_DEDUPE_SECONDS = 30 * 60
Session = Annotated[AsyncSession, Depends(get_session)]


def _err(status: int, code: str, **extra: Any) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, **extra})


def _check_id(sound_id: str) -> None:
    if not ID_RE.match(sound_id):
        raise _err(404, "not_found")


@router.get("/pins")
async def pins(
    request: Request,
    z: Annotated[int, Query(ge=0, le=22)],
    bbox: Annotated[str, Query(max_length=120)],
) -> Response:
    try:
        west, south, east, north = (float(x) for x in bbox.split(","))
    except ValueError as exc:
        raise _err(422, "invalid_bbox") from exc
    rs = await runtime_settings.load()
    await ratelimit.enforce("pins", ip_hash_of(request), rs.rate_pins_per_minute, 60)

    tr = cache.tile_range_for(z, west, south, east, north)
    cached = await cache.get_cached(tr)
    if cached:
        etag, body = cached
        hit = "HIT"
    else:
        async with sessionmaker()() as session:
            body = await sound_svc.pins_payload(session, *tr.bbox())
        etag = await cache.store(tr, body, get_settings().pins_cache_ttl)
        hit = "MISS"
    headers = {"ETag": etag, "Cache-Control": "no-cache", "X-Cache": hit, "Vary": "Accept-Encoding"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(body, media_type="application/json", headers=headers)


@router.get("/heat")
async def heat(request: Request, session: Session) -> Response:
    redis = get_redis()
    gen = await cache.current_gen()
    key = f"heat:{gen}"
    body = await redis.get(key)
    if not body:
        body = await sound_svc.heat_points(session)
        await redis.set(key, body, ex=300)
    etag = cache.make_etag(body)
    headers = {"ETag": etag, "Cache-Control": "public, max-age=120"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(body, media_type="application/json", headers=headers)


def _meta_from_form(
    title: str,
    comment: str,
    recorded_at: str,
    time_of_day: str,
    weather: str,
    season: str,
    tags: list[str],
    direction: str,
    license_: str,
) -> SoundMeta:
    try:
        return SoundMeta.model_validate(
            {
                "title": title,
                "comment": comment,
                "recorded_at": recorded_at or None,
                "time_of_day": time_of_day or None,
                "weather": weather or None,
                "season": season or None,
                "tags": [t for t in tags if t],
                "direction": int(direction) if direction not in ("", None) else None,
                "license": license_ or License.CC_BY,
            }
        ).filled()
    except (ValidationError, ValueError) as exc:
        errors = exc.errors() if isinstance(exc, ValidationError) else [{"msg": str(exc)}]
        raise _err(
            422,
            "invalid_input",
            errors=[{"loc": [str(x) for x in e.get("loc", ())], "msg": e.get("msg", "")} for e in errors],
        ) from exc


@router.post("/sounds", status_code=202)
async def create_sound(
    request: Request,
    session: Session,
    file: Annotated[UploadFile, File()],
    title: Annotated[str, Form()],
    lat: Annotated[float, Form()],
    lng: Annotated[float, Form()],
    agree: Annotated[bool, Form()] = False,
    precision: Annotated[Precision, Form()] = Precision.EXACT,
    comment: Annotated[str, Form()] = "",
    recorded_at: Annotated[str, Form()] = "",
    time_of_day: Annotated[str, Form()] = "",
    weather: Annotated[str, Form()] = "",
    season: Annotated[str, Form()] = "",
    tags: Annotated[list[str] | None, Form()] = None,
    direction: Annotated[str, Form()] = "",
    license: Annotated[str, Form()] = "cc-by",
    _ts: TurnstileResult = Depends(require_turnstile("post")),
) -> dict[str, Any]:
    settings = get_settings()
    rs = await runtime_settings.load()
    if rs.posting_paused:
        raise _err(503, "posting_paused")
    ip_hash = ip_hash_of(request)
    await moderation.ensure_not_banned(session, ip_hash)
    if not agree:
        raise _err(422, "guidelines_not_accepted")
    if not in_japan(lat, lng):
        raise _err(422, "outside_japan")
    meta = _meta_from_form(
        title, comment, recorded_at, time_of_day, weather, season, tags or [], direction, license
    )
    await moderation.check_ngwords(session, meta.title, meta.comment)
    await ratelimit.enforce("post", ip_hash, rs.rate_post_per_hour, 3600)

    # ぼかす場合、正確な座標はここで捨てて保存しない
    blat, blng = blur_point(lat, lng, precision.value)
    del lat, lng

    sound_id = new_sound_id()
    job_id = secrets.token_hex(12)
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    upload_path = settings.upload_dir / f"{job_id}.bin"
    size = 0
    with upload_path.open("wb") as out:
        while chunk := await file.read(1 << 16):
            size += len(chunk)
            if size > settings.max_upload_bytes:
                out.close()
                upload_path.unlink(missing_ok=True)
                raise _err(413, "too_large")
            out.write(chunk)
    if size == 0:
        upload_path.unlink(missing_ok=True)
        raise _err(422, "empty_file")

    token = new_token()
    sound = Sound(
        id=sound_id,
        status=SoundStatus.PROCESSING,
        title=meta.title,
        comment=meta.comment,
        recorded_at=meta.recorded_at or dt.datetime.now(dt.UTC),
        time_of_day=meta.time_of_day.value if meta.time_of_day else None,
        weather=meta.weather.value if meta.weather else None,
        season=meta.season.value if meta.season else None,
        tags=[t.value for t in meta.tags] or [Tag.OTHER.value],
        direction=meta.direction,
        license=meta.license.value,
        location=sound_svc.point(blat, blng),
        lat=blat,
        lng=blng,
        precision=precision.value,
        delete_token_hash=sha256_hex(token),
        ip_hash=ip_hash,
    )
    session.add(sound)
    await session.flush()
    session.add(ProcessingJob(id=job_id, sound_id=sound_id, status="queued", upload_path=str(upload_path)))
    await session.commit()
    await events.set_status(sound_id, {"state": "queued"})
    await enqueue("process_sound", job_id, _job_id=f"proc:{job_id}")
    return {
        "id": sound_id,
        "delete_token": token,
        "status": "queued",
        "events_url": f"/api/sounds/{sound_id}/events",
        "status_url": f"/api/sounds/{sound_id}/status",
        "page_url": f"/s/{sound_id}",
    }


async def _owned_sound(session: AsyncSession, request: Request, sound_id: str) -> Sound:
    _check_id(sound_id)
    token = request.headers.get("x-delete-token", "")
    sound = await session.get(Sound, sound_id)
    if sound is None or sound.status in (SoundStatus.DELETED, SoundStatus.PURGED):
        raise _err(404, "not_found")
    if not token or not token_matches(token, sound.delete_token_hash):
        raise _err(403, "invalid_token")
    return sound


@router.get("/sounds/{sound_id}/status")
async def sound_status(sound_id: str, request: Request, session: Session) -> dict[str, Any]:
    sound = await _owned_sound(session, request, sound_id)
    live = await events.get_status(sound_id) or {}
    return {
        "id": sound.id,
        "status": sound.status,
        "reason": sound.reject_reason or sound.hidden_reason,
        "clipping_warning": sound.clipping_warning,
        "live": live,
    }


@router.patch("/sounds/{sound_id}")
async def edit_sound(sound_id: str, body: SoundEdit, request: Request, session: Session) -> dict[str, Any]:
    sound = await _owned_sound(session, request, sound_id)
    data = body.model_dump(exclude_unset=True)
    await moderation.check_ngwords(session, data.get("title") or "", data.get("comment") or "")
    title_changed = "title" in data and data["title"] != sound.title
    tags_changed = "tags" in data
    for key, value in data.items():
        if key == "tags":
            value = [Tag(v).value for v in (value or [])] or [Tag.OTHER.value]
        elif hasattr(value, "value"):
            value = value.value
        setattr(sound, key, value)
    await session.commit()
    if tags_changed and sound.status == SoundStatus.PUBLISHED:
        await cache.invalidate_point(sound.lat, sound.lng)
    if title_changed and sound.spectrogram_hash:
        await enqueue("render_ogp", sound.id)
    return {"ok": True, "sound": sound_svc.serialize(sound)}


@router.delete("/sounds/{sound_id}")
async def delete_sound(sound_id: str, request: Request, session: Session) -> dict[str, Any]:
    sound = await _owned_sound(session, request, sound_id)
    await sound_svc.set_status(session, sound, SoundStatus.DELETED, "owner")
    await session.commit()
    await sound_svc.after_visibility_change(sound)
    return {"ok": True}


@router.post("/mine")
async def my_sounds(request: Request, session: Session) -> dict[str, Any]:
    try:
        body = orjson.loads(await request.body())
        items = [(str(i["id"]), str(i["token"])) for i in body.get("items", [])][:100]
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise _err(422, "invalid_input") from exc
    ids = [i for i, _ in items if ID_RE.match(i)]
    if not ids:
        return {"items": []}
    rows = (await session.execute(select(Sound).where(Sound.id.in_(ids)))).scalars().all()
    by_id = {s.id: s for s in rows}
    out = []
    for sid, token in items:
        s = by_id.get(sid)
        if s is None or not token_matches(token, s.delete_token_hash):
            continue
        if s.status == SoundStatus.PURGED:
            continue
        out.append(
            {
                **sound_svc.serialize(s),
                "status": s.status,
                "reason": s.reject_reason or s.hidden_reason,
                "clipping_warning": s.clipping_warning,
            }
        )
    return {"items": out}


async def _public_sound(session: AsyncSession, sound_id: str) -> Sound:
    _check_id(sound_id)
    sound = await session.get(Sound, sound_id)
    if sound is None or sound.status != SoundStatus.PUBLISHED:
        raise _err(404, "not_found")
    return sound


@router.get("/sounds/{sound_id}")
async def get_sound(sound_id: str, session: Session) -> dict[str, Any]:
    sound = await _public_sound(session, sound_id)
    return sound_svc.serialize(sound)


@router.get("/sounds/{sound_id}/same-place")
async def same_place(sound_id: str, session: Session) -> dict[str, Any]:
    sound = await _public_sound(session, sound_id)
    items = await sound_svc.same_place(session, sound)
    return {"items": [sound_svc.serialize(s) for s in items]}


@router.post("/sounds/{sound_id}/play", status_code=204)
async def count_play(sound_id: str, request: Request) -> Response:
    _check_id(sound_id)
    redis = get_redis()
    if await redis.set(f"play:{sound_id}:{ip_hash_of(request)}", b"1", nx=True, ex=PLAY_DEDUPE_SECONDS):
        await redis.hincrby("plays:pending", sound_id, 1)
    return Response(status_code=204)


@router.get("/nearby")
async def nearby(
    session: Session,
    lat: Annotated[float, Query(ge=-90, le=90)],
    lng: Annotated[float, Query(ge=-180, le=180)],
    radius: Annotated[float, Query(ge=10, le=20000)] = 1500,
    limit: Annotated[int, Query(ge=1, le=8)] = 8,
) -> dict[str, Any]:
    items = await sound_svc.nearby(session, lat, lng, radius, limit)
    return {"items": [sound_svc.serialize(s) for s in items]}


@router.get("/walk/next")
async def walk_next(
    session: Session,
    region: Annotated[str | None, Query()] = None,
    weather: Annotated[Weather | None, Query()] = None,
    time_of_day: Annotated[TimeOfDay | None, Query()] = None,
    season: Annotated[Season | None, Query()] = None,
    tag: Annotated[Tag | None, Query()] = None,
    exclude: Annotated[str, Query(max_length=1000)] = "",
) -> dict[str, Any]:
    if region and region not in REGIONS:
        raise _err(422, "invalid_region")
    ex = [x for x in exclude.split(",") if ID_RE.match(x)]
    pick = await sound_svc.random_pick(
        session,
        region=region,
        weather=weather.value if weather else None,
        time_of_day=time_of_day.value if time_of_day else None,
        season=season.value if season else None,
        tag=tag.value if tag else None,
        exclude=ex,
    )
    if pick is None and ex:
        pick = await sound_svc.random_pick(
            session,
            region=region,
            weather=weather.value if weather else None,
            time_of_day=time_of_day.value if time_of_day else None,
            season=season.value if season else None,
            tag=tag.value if tag else None,
        )
    if pick is None:
        raise _err(404, "no_sound")
    return sound_svc.serialize(pick)


PAGE_SIZE = 30


@router.get("/lists/{kind}")
async def lists(
    kind: str,
    session: Session,
    page: Annotated[int, Query(ge=1, le=100)] = 1,
    pref: Annotated[int | None, Query(ge=1, le=47)] = None,
) -> dict[str, Any]:
    q = sound_svc.public_query()
    if kind == "new":
        q = q.order_by(Sound.published_at.desc())
    elif kind == "popular":
        q = q.order_by(Sound.play_count.desc(), Sound.published_at.desc())
    elif kind == "pref":
        if pref is None:
            raise _err(422, "pref_required")
        q = q.where(Sound.pref_code == pref).order_by(Sound.published_at.desc())
    else:
        raise _err(404, "not_found")
    q = q.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE + 1)
    rows = list((await session.execute(q)).scalars().all())
    return {
        "items": [sound_svc.serialize(s) for s in rows[:PAGE_SIZE]],
        "page": page,
        "has_next": len(rows) > PAGE_SIZE,
    }


@router.get("/prefs")
async def prefs(session: Session) -> dict[str, Any]:
    redis = get_redis()
    cached = await redis.get("prefcounts")
    if cached:
        return dict(orjson.loads(cached))
    rows = (
        await session.execute(
            select(Sound.pref_code, func.count())
            .where(Sound.status == SoundStatus.PUBLISHED, Sound.pref_code.is_not(None))
            .group_by(Sound.pref_code)
        )
    ).all()
    counts = {int(code): int(n) for code, n in rows if code is not None}
    data = {
        "items": [
            {"code": p.code, "ja": p.ja, "en": p.en, "region": p.region, "count": counts.get(p.code, 0)}
            for p in PREFECTURES
        ],
        "regions": [{"id": k, "ja": v[0], "en": v[1]} for k, v in REGIONS.items()],
    }
    await redis.set("prefcounts", orjson.dumps(data), ex=300)
    return data


@router.get("/courses")
async def courses(session: Session, featured: bool = False) -> dict[str, Any]:
    q = select(Course).where(Course.is_public.is_(True))
    if featured:
        q = q.where(Course.is_featured.is_(True))
    q = q.order_by(Course.is_featured.desc(), Course.updated_at.desc()).limit(50)
    rows = (await session.execute(q)).scalars().all()
    return {
        "items": [
            {"slug": c.slug, "title": c.title, "description": c.description, "featured": c.is_featured}
            for c in rows
        ]
    }


@router.get("/courses/{slug}")
async def course_detail(slug: str, session: Session) -> dict[str, Any]:
    q = (
        select(Course)
        .where(Course.slug == slug, Course.is_public.is_(True))
        .options(selectinload(Course.items).selectinload(CourseItem.sound))
    )
    course = (await session.execute(q)).scalar_one_or_none()
    if course is None:
        raise _err(404, "not_found")
    items = [
        {"note": it.note, "sound": sound_svc.serialize(it.sound)}
        for it in course.items
        if it.sound.status == SoundStatus.PUBLISHED
    ]
    return {"slug": course.slug, "title": course.title, "description": course.description, "items": items}


@router.post("/sounds/{sound_id}/reports", status_code=201)
async def report_sound(
    sound_id: str,
    body: ReportIn,
    request: Request,
    session: Session,
    _ts: TurnstileResult = Depends(require_turnstile("report")),
) -> dict[str, Any]:
    ip_hash = ip_hash_of(request)
    await moderation.ensure_not_banned(session, ip_hash)
    rs = await runtime_settings.load()
    await ratelimit.enforce("report", ip_hash, rs.rate_report_per_hour, 3600)
    sound = await _public_sound(session, sound_id)
    if body.reason.value == "other" and not body.detail:
        raise _err(422, "detail_required")
    weight = await moderation.reporter_weight(session, ip_hash)
    session.add(
        Report(
            sound_id=sound.id, ip_hash=ip_hash, reason=body.reason.value, detail=body.detail, weight=weight
        )
    )
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise _err(409, "already_reported") from exc
    hidden = await moderation.apply_report_threshold(session, sound, rs.report_threshold)
    if hidden:
        await session.flush()
        await sound_svc.refresh_nearby_counts(session, sound.lat, sound.lng)
    await session.commit()
    if hidden:
        await sound_svc.after_visibility_change(sound)
    return {"ok": True}
