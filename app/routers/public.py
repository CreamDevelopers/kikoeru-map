from __future__ import annotations

import json
import re
from typing import Annotated

import orjson
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models import Course, Sound, SoundStatus
from app.services import media
from app.services import sounds as sound_svc
from app.services.auth import load_session
from app.services.geo import PREF_BY_CODE, PREFECTURES, REGIONS
from app.services.i18n import pick_lang
from app.services.redis import get_redis
from app.web import STATIC_DIR, asset, asset_version, render

router = APIRouter(include_in_schema=False)
Session = Annotated[AsyncSession, Depends(get_session)]

FILE_RE = re.compile(r"^([A-Za-z0-9-]{8,24})\.(webm|m4a|json|png)$")
ID_RE = re.compile(r"^[A-Za-z0-9]{8,16}$")
IMMUTABLE = "public, max-age=31536000, immutable"


def _not_found(request: Request) -> HTMLResponse:
    return render(request, "pages/error.html", {"code": 404}, status_code=404)


def _app_page(request: Request, **ctx: object) -> HTMLResponse:
    return render(
        request,
        "index.html",
        {
            "regions": REGIONS,
            "prefs": PREFECTURES,
            "boot": {"mode": ctx.pop("mode", None), **ctx},
        },
    )


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return _app_page(request)


@router.get("/game", response_class=HTMLResponse)
async def game_page(request: Request) -> HTMLResponse:
    return _app_page(request, mode="game")


@router.get("/walk", response_class=HTMLResponse)
async def walk_page(request: Request) -> HTMLResponse:
    return _app_page(request, mode="walk")


@router.get("/course/{slug}", response_class=HTMLResponse)
async def course_page(slug: str, request: Request, session: Session) -> HTMLResponse:
    exists = (
        await session.execute(select(Course.id).where(Course.slug == slug, Course.is_public.is_(True)))
    ).scalar_one_or_none()
    if exists is None:
        return _not_found(request)
    return _app_page(request, mode="course", course=slug)


@router.get("/s/{sound_id}", response_class=HTMLResponse)
async def sound_page(sound_id: str, request: Request, session: Session) -> HTMLResponse:
    if not ID_RE.match(sound_id):
        return _not_found(request)
    sound = await session.get(Sound, sound_id)
    if sound is None or sound.status != SoundStatus.PUBLISHED:
        return _not_found(request)
    base = get_settings().base_url.rstrip("/")
    ogp = media.url(sound.id, sound.ogp_hash, "png") or media.url(sound.id, sound.spectrogram_hash, "png")
    return render(
        request,
        "sound.html",
        {
            "sound": sound,
            "data": sound_svc.serialize(sound),
            "og_image": f"{base}{ogp}" if ogp else None,
            "canonical": f"{base}/s/{sound.id}",
        },
    )


@router.get("/list/{kind}", response_class=HTMLResponse)
async def list_page(
    kind: str,
    request: Request,
    session: Session,
    page: Annotated[int, Query(ge=1, le=100)] = 1,
) -> HTMLResponse:
    per = 30
    q = sound_svc.public_query()
    if kind == "new":
        q = q.order_by(Sound.published_at.desc())
    elif kind == "popular":
        q = q.order_by(Sound.play_count.desc(), Sound.published_at.desc())
    elif kind == "prefs":
        rows = (
            await session.execute(
                select(Sound.pref_code, func.count())
                .where(Sound.status == SoundStatus.PUBLISHED, Sound.pref_code.is_not(None))
                .group_by(Sound.pref_code)
            )
        ).all()
        counts = {int(c): int(n) for c, n in rows if c is not None}
        return render(request, "prefs.html", {"prefs": PREFECTURES, "regions": REGIONS, "counts": counts})
    else:
        return _not_found(request)
    items = list((await session.execute(q.offset((page - 1) * per).limit(per + 1))).scalars().all())
    return render(
        request,
        "list.html",
        {"kind": kind, "items": items[:per], "page": page, "has_next": len(items) > per, "pref": None},
    )


@router.get("/list/pref/{code}", response_class=HTMLResponse)
async def pref_page(
    code: int,
    request: Request,
    session: Session,
    page: Annotated[int, Query(ge=1, le=100)] = 1,
) -> HTMLResponse:
    pref = PREF_BY_CODE.get(code)
    if pref is None:
        return _not_found(request)
    per = 30
    q = (
        sound_svc.public_query()
        .where(Sound.pref_code == code)
        .order_by(Sound.published_at.desc())
        .offset((page - 1) * per)
        .limit(per + 1)
    )
    items = list((await session.execute(q)).scalars().all())
    return render(
        request,
        "list.html",
        {"kind": "pref", "items": items[:per], "page": page, "has_next": len(items) > per, "pref": pref},
    )


STATIC_PAGES = {"guidelines", "terms", "privacy", "attribution", "help"}


@router.get("/about/{name}", response_class=HTMLResponse)
async def static_page(name: str, request: Request) -> HTMLResponse:
    if name not in STATIC_PAGES:
        return _not_found(request)
    lang = pick_lang(request)
    return render(request, f"pages/{name}.{lang}.html", {"page": name})


@router.get("/media/{sound_id}/{filename}")
async def media_file(sound_id: str, filename: str, request: Request, session: Session) -> Response:
    m = FILE_RE.match(filename)
    if not ID_RE.match(sound_id) or not m:
        raise HTTPException(404, detail={"code": "not_found"})
    content_hash, ext = m.group(1), m.group(2)
    path = media.file_path(sound_id, content_hash, ext)
    redis = get_redis()
    key = f"mediaok:{sound_id}"
    state = await redis.get(key)
    if state is None:
        sound = await session.get(Sound, sound_id)
        public = sound is not None and sound.status == SoundStatus.PUBLISHED
        state = b"1" if public else b"0"
        await redis.set(key, state, ex=300)
    public = state == b"1"
    if not public:
        sess = await load_session(request)
        if sess is None or not sess.is_authenticated:
            raise HTTPException(404, detail={"code": "not_found"})
    if not path.is_file():
        raise HTTPException(404, detail={"code": "not_found"})
    headers = {
        "Cache-Control": IMMUTABLE if public else "private, no-store",
        "X-Content-Type-Options": "nosniff",
        "Accept-Ranges": "bytes",
    }
    return FileResponse(
        path, media_type=media.EXT_TYPES[ext], headers=headers, content_disposition_type="inline"
    )


@router.get("/sw.js")
async def service_worker() -> Response:
    body = (STATIC_DIR / "sw.js").read_text(encoding="utf-8")
    shell = [
        asset("css/app.css"),
        *(asset(f"js/{p.name}") for p in sorted((STATIC_DIR / "js").glob("*.js")) if p.name != "admin.js"),
        asset("icons/icon-192.png"),
        asset("img/kikoeru-logo.png"),
    ]
    body = body.replace("__VERSION__", asset_version()).replace("__SHELL__", json.dumps(shell))
    return Response(
        body,
        media_type="text/javascript",
        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
    )


@router.get("/manifest.webmanifest")
async def manifest(request: Request) -> Response:
    lang = pick_lang(request)
    data = {
        "name": "きこえる地図" if lang == "ja" else "Kikoeru Map",
        "short_name": "きこえる地図" if lang == "ja" else "Kikoeru",
        "description": "日本各地の環境音を地図で旅する"
        if lang == "ja"
        else "Travel Japan by its soundscapes",
        "start_url": "/?source=pwa",
        "scope": "/",
        "display": "standalone",
        "orientation": "any",
        "background_color": "#f4f4f1",
        "theme_color": "#00796b",
        "lang": lang,
        "icons": [
            {"src": asset("icons/icon-192.png"), "sizes": "192x192", "type": "image/png"},
            {"src": asset("icons/icon-512.png"), "sizes": "512x512", "type": "image/png"},
        ],
    }
    return Response(
        orjson.dumps(data),
        media_type="application/manifest+json",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/.well-known/assetlinks.json")
async def assetlinks() -> Response:
    settings = get_settings()
    fingerprints = [f.strip().upper() for f in settings.android_cert_sha256.split(",") if f.strip()]
    data = (
        [
            {
                "relation": ["delegate_permission/common.handle_all_urls"],
                "target": {
                    "namespace": "android_app",
                    "package_name": settings.android_package,
                    "sha256_cert_fingerprints": fingerprints,
                },
            }
        ]
        if fingerprints
        else []
    )
    return Response(
        orjson.dumps(data),
        media_type="application/json",
        headers={"Cache-Control": "public, max-age=3600", "Access-Control-Allow-Origin": "*"},
    )


@router.get("/robots.txt")
async def robots() -> PlainTextResponse:
    return PlainTextResponse("User-agent: *\nDisallow: /admin\nDisallow: /api/\n")


@router.get("/favicon.ico")
async def favicon() -> FileResponse:
    return FileResponse(STATIC_DIR / "icons" / "icon-32.png", media_type="image/png")
