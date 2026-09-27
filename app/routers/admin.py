from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Annotated, Any

import segno
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from markupsafe import Markup
from pydantic import ValidationError
from sqlalchemy import String, and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_session
from app.models import (
    AdminUser,
    AuditLog,
    Ban,
    Course,
    CourseItem,
    DailyStat,
    NgWord,
    ProcessingJob,
    Report,
    Sound,
    SoundStatus,
    utcnow,
)
from app.schemas import JST, Tag
from app.services import auth, moderation, runtime_settings
from app.services import sounds as sound_svc
from app.services.auth import AdminSession, audit, require_admin, require_staff
from app.services.geo import PREFECTURES
from app.services.queue import enqueue
from app.services.redis import get_redis
from app.services.security import client_ip, hash_ip, ip_hash_of
from app.services.turnstile import verify_token
from app.web import render

router = APIRouter(prefix="/admin", include_in_schema=False)
Session = Annotated[AsyncSession, Depends(get_session)]
Staff = Annotated[AdminSession, Depends(require_staff)]
Admin = Annotated[AdminSession, Depends(require_admin)]

PER_PAGE = 50
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")


def _page(request: Request, name: str, admin: AdminSession | None, **ctx: Any) -> HTMLResponse:
    return render(request, f"admin/{name}", {"admin": admin, "msg": request.query_params.get("msg"), **ctx})


def _redirect(url: str, msg: str | None = None) -> RedirectResponse:
    if msg:
        url += ("&" if "?" in url else "?") + f"msg={msg}"
    return RedirectResponse(url, status_code=303)


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request) -> Response:
    sess = await auth.load_session(request)
    if sess and sess.is_authenticated:
        return _redirect("/admin")
    error = request.query_params.get("error")
    if sess is not None and sess.stage == "anon":
        return _page(request, "login.html", None, csrf=sess.csrf, error=error)
    # CSRF トークンを持たせるためのログイン前セッション
    sid, new = await auth.start_session()
    resp = _page(request, "login.html", None, csrf=new.csrf, error=error)
    auth.attach_cookie(resp, sid)
    return resp


@router.post("/login")
async def login_submit(
    request: Request,
    session: Session,
    username: Annotated[str, Form(max_length=64)],
    password: Annotated[str, Form(max_length=256)],
    csrf_token: Annotated[str, Form()] = "",
) -> Response:
    sess = await auth.load_session(request)
    if sess is None or not auth.csrf_ok(sess, csrf_token):
        return _redirect("/admin/login?error=csrf")
    form = await request.form()
    token = form.get("cf-turnstile-response")
    ts = await verify_token(token if isinstance(token, str) else "", client_ip(request), "login")
    if not ts.success:
        return _redirect("/admin/login?error=turnstile")
    ip_ident = "ip:" + ip_hash_of(request)
    user_ident = "user:" + username.lower()
    if await auth.login_locked(ip_ident) or await auth.login_locked(user_ident):
        return _redirect("/admin/login?error=locked")
    user = (
        await session.execute(select(AdminUser).where(AdminUser.username == username))
    ).scalar_one_or_none()
    if user is None or not user.is_active:
        auth.verify_dummy(password)
        await auth.record_login_failure(ip_ident, user_ident)
        return _redirect("/admin/login?error=invalid")
    if not auth.verify_password(user.password_hash, password):
        await auth.record_login_failure(ip_ident, user_ident)
        return _redirect("/admin/login?error=invalid")
    await auth.clear_login_failures(ip_ident, user_ident)
    resp = _redirect("/admin/login/totp" if user.totp_enabled else "/admin")
    await get_redis().delete(f"adm:sess:{sess.sid_hash}")
    stage = "totp" if user.totp_enabled else "full"
    new = await auth.create_session(resp, **auth.user_session_fields(user, stage))
    if stage == "full":
        user.last_login_at = utcnow()
        await audit(session, request, new, "login")
        await session.commit()
    return resp


@router.get("/login/totp", response_class=HTMLResponse)
async def totp_form(request: Request) -> Response:
    sess = await auth.load_session(request)
    if sess is None or sess.stage != "totp":
        return _redirect("/admin/login")
    return _page(request, "totp.html", None, csrf=sess.csrf, error=request.query_params.get("error"))


@router.post("/login/totp")
async def totp_submit(
    request: Request,
    session: Session,
    code: Annotated[str, Form(max_length=12)],
    csrf_token: Annotated[str, Form()] = "",
) -> Response:
    sess = await auth.load_session(request)
    if sess is None or sess.stage != "totp" or sess.user_id is None or not auth.csrf_ok(sess, csrf_token):
        return _redirect("/admin/login")
    ident = f"totp:{sess.user_id}"
    if await auth.login_locked(ident):
        return _redirect("/admin/login/totp?error=locked")
    user = await session.get(AdminUser, sess.user_id)
    if (
        user is None
        or not user.totp_secret
        or not auth.verify_totp(user.totp_secret, code)
        or not await auth.totp_code_unused(user.id, code.strip())
    ):
        await auth.record_login_failure(ident)
        return _redirect("/admin/login/totp?error=invalid")
    await auth.clear_login_failures(ident)
    resp = _redirect("/admin")
    await get_redis().delete(f"adm:sess:{sess.sid_hash}")
    new = await auth.create_session(resp, **auth.user_session_fields(user, "full"))
    user.last_login_at = utcnow()
    await audit(session, request, new, "login", detail={"totp": True})
    await session.commit()
    return resp


@router.post("/logout")
async def logout(request: Request, admin: Staff, session: Session) -> Response:
    await audit(session, request, admin, "logout")
    await session.commit()
    resp = _redirect("/admin/login")
    await auth.destroy_session(request, resp)
    return resp


@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request, admin: Staff, session: Session) -> HTMLResponse:
    today = dt.datetime.now(JST).date()
    total = (
        await session.execute(select(func.count()).where(Sound.status == SoundStatus.PUBLISHED))
    ).scalar_one()
    today_row = await session.get(DailyStat, today)
    open_reports = (
        await session.execute(
            select(func.count(func.distinct(Report.sound_id))).where(Report.status == "open")
        )
    ).scalar_one()
    pending = (
        await session.execute(select(func.count()).where(Sound.status == SoundStatus.PENDING))
    ).scalar_one()
    plays = (await session.execute(select(func.coalesce(func.sum(Sound.play_count), 0)))).scalar_one()
    pending_plays = sum(int(v) for v in (await get_redis().hvals("plays:pending")))
    return _page(
        request,
        "dashboard.html",
        admin,
        stats={
            "total": total,
            "today": today_row.posts if today_row else 0,
            "open_reports": open_reports,
            "pending": pending,
            "plays": int(plays) + pending_plays,
        },
    )


@router.get("/api/stats")
async def stats(admin: Staff, session: Session) -> dict[str, Any]:
    today = dt.datetime.now(JST).date()
    start = today - dt.timedelta(days=29)
    rows = (
        await session.execute(select(DailyStat).where(DailyStat.date >= start).order_by(DailyStat.date))
    ).scalars()
    by_day = {r.date: r for r in rows}
    days = [start + dt.timedelta(days=i) for i in range(30)]
    prefs = (
        await session.execute(
            select(Sound.pref_code, func.count())
            .where(Sound.status == SoundStatus.PUBLISHED, Sound.pref_code.is_not(None))
            .group_by(Sound.pref_code)
        )
    ).all()
    counts = {int(c): int(n) for c, n in prefs if c is not None}
    return {
        "days": [d.isoformat() for d in days],
        "posts": [by_day[d].posts if d in by_day else 0 for d in days],
        "plays": [by_day[d].plays if d in by_day else 0 for d in days],
        "prefs": [
            {"code": p.code, "name": p.ja, "lat": p.lat, "lng": p.lng, "count": counts.get(p.code, 0)}
            for p in PREFECTURES
        ],
    }


def _sound_filters(
    status: str | None,
    tag: str | None,
    flag: str | None,
    q: str | None,
    date_from: str | None,
    date_to: str | None,
) -> list[Any]:
    conds: list[Any] = []
    if status and status in SoundStatus.ALL:
        conds.append(Sound.status == status)
    if tag and tag in {t.value for t in Tag}:
        conds.append(Sound.tags.contains([tag]))
    if flag == "voice":
        conds.append(Sound.voice_flag.is_(True))
    elif flag == "clipping":
        conds.append(Sound.clipping_warning.is_(True))
    elif flag == "game_ok":
        conds.append(Sound.game_ok.is_(True))
    elif flag == "reported":
        conds.append(Sound.id.in_(select(Report.sound_id).where(Report.status == "open")))
    if q:
        like = f"%{q[:40]}%"
        conds.append(or_(Sound.title.ilike(like), Sound.id == q, Sound.city_name.ilike(like)))
    for value, op in ((date_from, "ge"), (date_to, "lt")):
        if value:
            try:
                d = dt.date.fromisoformat(value)
            except ValueError:
                continue
            t = dt.datetime.combine(d if op == "ge" else d + dt.timedelta(days=1), dt.time(), JST)
            conds.append(Sound.created_at >= t if op == "ge" else Sound.created_at < t)
    return conds


@router.get("/sounds", response_class=HTMLResponse)
async def sounds_list(
    request: Request,
    admin: Staff,
    session: Session,
    status: str | None = None,
    tag: str | None = None,
    flag: str | None = None,
    q: Annotated[str | None, Query(max_length=40)] = None,
    date_from: str | None = None,
    date_to: str | None = None,
    page: Annotated[int, Query(ge=1, le=1000)] = 1,
) -> HTMLResponse:
    conds = _sound_filters(status, tag, flag, q, date_from, date_to)
    stmt = (
        select(Sound)
        .where(and_(*conds))
        .order_by(Sound.created_at.desc())
        .offset((page - 1) * PER_PAGE)
        .limit(PER_PAGE + 1)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    ids = [s.id for s in rows]
    report_counts: dict[str, int] = {}
    if ids:
        rc = await session.execute(
            select(Report.sound_id, func.count())
            .where(Report.sound_id.in_(ids), Report.status == "open")
            .group_by(Report.sound_id)
        )
        report_counts = {sid: int(n) for sid, n in rc.all()}
    return _page(
        request,
        "sounds.html",
        admin,
        items=rows[:PER_PAGE],
        has_next=len(rows) > PER_PAGE,
        page=page,
        report_counts=report_counts,
        filters={
            "status": status or "",
            "tag": tag or "",
            "flag": flag or "",
            "q": q or "",
            "date_from": date_from or "",
            "date_to": date_to or "",
        },
        statuses=SoundStatus.ALL,
        tags=[t.value for t in Tag],
    )


async def _apply_sound_action(
    session: AsyncSession, request: Request, admin: AdminSession, sound: Sound, action: str
) -> Sound:
    before = sound.status
    if action == "publish":
        await sound_svc.set_status(session, sound, SoundStatus.PUBLISHED)
        await _resolve_reports(session, admin, sound.id, upheld=False)
    elif action == "hide":
        await sound_svc.set_status(session, sound, SoundStatus.HIDDEN, "admin")
    elif action == "delete":
        await sound_svc.set_status(session, sound, SoundStatus.DELETED, "admin")
    elif action == "reject":
        await sound_svc.set_status(session, sound, SoundStatus.REJECTED, "moderator")
        sound.reject_reason = "moderator"
        await _resolve_reports(session, admin, sound.id, upheld=True)
    elif action == "game_on":
        sound.game_ok = True
    elif action == "game_off":
        sound.game_ok = False
    else:
        raise HTTPException(422, detail={"code": "invalid_action"})
    await audit(session, request, admin, f"sound.{action}", "sound", sound.id, {"before": before})
    return sound


async def _after_actions(sounds: list[Sound]) -> None:
    for s in sounds:
        await sound_svc.after_visibility_change(s)


@router.post("/sounds/bulk")
async def sounds_bulk(request: Request, admin: Staff, session: Session) -> Response:
    form = await request.form()
    action = str(form.get("action") or "")
    ids = [str(i) for i in form.getlist("ids")][:200]
    rows = list((await session.execute(select(Sound).where(Sound.id.in_(ids)))).scalars().all())
    for s in rows:
        await _apply_sound_action(session, request, admin, s, action)
    await session.commit()
    await _after_actions(rows)
    back = str(form.get("back") or "/admin/sounds")
    return _redirect(back if back.startswith("/admin") else "/admin/sounds", "done")


@router.post("/sounds/{sound_id}/action")
async def sound_action(sound_id: str, request: Request, admin: Staff, session: Session) -> Response:
    form = await request.form()
    action = str(form.get("row_action") or form.get("action") or "")
    sound = await session.get(Sound, sound_id)
    if sound is None:
        raise HTTPException(404, detail={"code": "not_found"})
    await _apply_sound_action(session, request, admin, sound, action)
    await session.commit()
    await _after_actions([sound])
    if "application/json" in request.headers.get("accept", ""):
        return Response(b'{"ok":true}', media_type="application/json")
    back = str(form.get("back") or "/admin/sounds")
    return _redirect(back if back.startswith("/admin") else "/admin/sounds", "done")


def _admin_sound_json(s: Sound, reports: list[Report] | None = None) -> dict[str, Any]:
    data = sound_svc.serialize(s)
    data.update(
        status=s.status,
        hidden_reason=s.hidden_reason,
        voice_ratio=s.voice_ratio,
        voice_flag=s.voice_flag,
        clipping_ratio=s.clipping_ratio,
        clipping_warning=s.clipping_warning,
        silence_ratio=s.silence_ratio,
        game_ok=s.game_ok,
        spectrogram_url=f"/media/{s.id}/{s.spectrogram_hash}.png" if s.spectrogram_hash else None,
        created_at=s.created_at.isoformat(),
        ip_hash=s.ip_hash[:12],
        reports=[
            {
                "reason": r.reason,
                "detail": r.detail,
                "weight": r.weight,
                "created_at": r.created_at.isoformat(),
            }
            for r in reports or []
        ],
    )
    return data


@router.get("/review", response_class=HTMLResponse)
async def review_page(request: Request, admin: Staff, session: Session) -> HTMLResponse:
    count = (
        await session.execute(select(func.count()).where(Sound.status == SoundStatus.PENDING))
    ).scalar_one()
    return _page(request, "review.html", admin, count=count)


@router.get("/api/review/next")
async def review_next(
    admin: Staff, session: Session, after: Annotated[str | None, Query(max_length=16)] = None
) -> dict[str, Any]:
    stmt = select(Sound).where(Sound.status == SoundStatus.PENDING)
    if after:
        prev = await session.get(Sound, after)
        if prev is not None:
            stmt = stmt.where(
                or_(
                    Sound.created_at > prev.created_at,
                    and_(Sound.created_at == prev.created_at, Sound.id > prev.id),
                )
            )
    stmt = stmt.order_by(Sound.created_at, Sound.id).limit(1)
    sound = (await session.execute(stmt)).scalar_one_or_none()
    remaining = (
        await session.execute(select(func.count()).where(Sound.status == SoundStatus.PENDING))
    ).scalar_one()
    if sound is None:
        return {"item": None, "remaining": remaining}
    reports = (
        (await session.execute(select(Report).where(Report.sound_id == sound.id, Report.status == "open")))
        .scalars()
        .all()
    )
    return {"item": _admin_sound_json(sound, list(reports)), "remaining": remaining}


@router.post("/api/review/{sound_id}")
async def review_decide(sound_id: str, request: Request, admin: Staff, session: Session) -> dict[str, Any]:
    body = await request.json()
    decision = body.get("decision") if isinstance(body, dict) else None
    sound = await session.get(Sound, sound_id)
    if sound is None:
        raise HTTPException(404, detail={"code": "not_found"})
    if decision == "approve":
        await _apply_sound_action(session, request, admin, sound, "publish")
    elif decision == "reject":
        await _apply_sound_action(session, request, admin, sound, "reject")
    else:
        raise HTTPException(422, detail={"code": "invalid_decision"})
    await session.commit()
    await _after_actions([sound])
    return {"ok": True, "status": sound.status}


async def _resolve_reports(
    session: AsyncSession, admin: AdminSession, sound_id: str, *, upheld: bool
) -> None:
    reports = (
        (await session.execute(select(Report).where(Report.sound_id == sound_id, Report.status == "open")))
        .scalars()
        .all()
    )
    if not reports:
        return
    now = utcnow()
    for r in reports:
        r.status = "actioned" if upheld else "dismissed"
        r.resolved_at = now
        r.resolved_by = admin.user_id
    await moderation.update_trust(session, [r.ip_hash for r in reports], upheld=upheld)


@router.get("/reports", response_class=HTMLResponse)
async def reports_page(request: Request, admin: Staff, session: Session) -> HTMLResponse:
    sub = (
        select(
            Report.sound_id,
            func.count().label("n"),
            func.sum(Report.weight).label("w"),
            func.min(Report.created_at).label("first"),
        )
        .where(Report.status == "open")
        .group_by(Report.sound_id)
        .order_by(func.sum(Report.weight).desc())
        .limit(100)
        .subquery()
    )
    rows = (
        await session.execute(select(Sound, sub.c.n, sub.c.w).join(sub, sub.c.sound_id == Sound.id))
    ).all()
    ids = [r[0].id for r in rows]
    reports: dict[str, list[Report]] = {i: [] for i in ids}
    if ids:
        for rep in (
            await session.execute(
                select(Report)
                .where(Report.sound_id.in_(ids), Report.status == "open")
                .order_by(Report.created_at)
            )
        ).scalars():
            reports[rep.sound_id].append(rep)
    groups = [
        {"sound": s, "count": n, "weight": round(float(w), 2), "reports": reports[s.id]} for s, n, w in rows
    ]
    groups.sort(key=lambda g: -g["weight"])
    return _page(request, "reports.html", admin, groups=groups)


@router.post("/reports/{sound_id}/resolve")
async def resolve_reports(
    sound_id: str,
    request: Request,
    admin: Staff,
    session: Session,
    decision: Annotated[str, Form()],
    ban_days: Annotated[int, Form(ge=0, le=3650)] = 0,
) -> Response:
    sound = await session.get(Sound, sound_id)
    if sound is None:
        raise HTTPException(404, detail={"code": "not_found"})
    if decision == "ok":
        await _resolve_reports(session, admin, sound.id, upheld=False)
        if sound.status == SoundStatus.PENDING and sound.hidden_reason == "reports":
            await sound_svc.set_status(session, sound, SoundStatus.PUBLISHED)
    elif decision == "hide":
        await _resolve_reports(session, admin, sound.id, upheld=True)
        await sound_svc.set_status(session, sound, SoundStatus.HIDDEN, "reports")
    elif decision == "delete":
        await _resolve_reports(session, admin, sound.id, upheld=True)
        await sound_svc.set_status(session, sound, SoundStatus.DELETED, "reports")
    elif decision == "ban":
        await _resolve_reports(session, admin, sound.id, upheld=True)
        await sound_svc.set_status(session, sound, SoundStatus.DELETED, "reports")
        session.add(
            Ban(
                ip_hash=sound.ip_hash,
                reason=f"report:{sound.id}",
                expires_at=moderation.ban_expiry(ban_days),
                created_by=admin.user_id,
            )
        )
        await moderation.invalidate_ban(sound.ip_hash)
    else:
        raise HTTPException(422, detail={"code": "invalid_decision"})
    await audit(session, request, admin, f"report.{decision}", "sound", sound.id, {"ban_days": ban_days})
    await session.commit()
    await sound_svc.after_visibility_change(sound)
    return _redirect("/admin/reports", "done")


@router.get("/bans", response_class=HTMLResponse)
async def bans_page(request: Request, admin: Staff, session: Session) -> HTMLResponse:
    rows = (await session.execute(select(Ban).order_by(Ban.created_at.desc()).limit(200))).scalars().all()
    return _page(request, "bans.html", admin, bans=rows, now=utcnow())


@router.post("/bans")
async def bans_create(
    request: Request,
    admin: Staff,
    session: Session,
    target: Annotated[str, Form(max_length=128)],
    reason: Annotated[str, Form(max_length=200)] = "",
    days: Annotated[int, Form(ge=0, le=3650)] = 0,
) -> Response:
    target = target.strip()
    if re.fullmatch(r"[0-9a-f]{64}", target):
        ip_hash = target
    elif re.fullmatch(r"[A-Za-z0-9]{8,16}", target):
        sound = await session.get(Sound, target)
        if sound is None:
            return _redirect("/admin/bans", "not_found")
        ip_hash = sound.ip_hash
    else:
        ip_hash = hash_ip(target)
    ban = Ban(
        ip_hash=ip_hash, reason=reason, expires_at=moderation.ban_expiry(days), created_by=admin.user_id
    )
    session.add(ban)
    await audit(
        session, request, admin, "ban.create", "ip_hash", ip_hash[:12], {"days": days, "reason": reason}
    )
    await session.commit()
    await moderation.invalidate_ban(ip_hash)
    return _redirect("/admin/bans", "done")


@router.post("/bans/{ban_id}/lift")
async def bans_lift(ban_id: int, request: Request, admin: Staff, session: Session) -> Response:
    ban = await session.get(Ban, ban_id)
    if ban is None:
        return _redirect("/admin/bans", "not_found")
    ban.lifted_at = utcnow()
    await audit(session, request, admin, "ban.lift", "ban", ban.id)
    await session.commit()
    await moderation.invalidate_ban(ban.ip_hash)
    return _redirect("/admin/bans", "done")


@router.get("/ngwords", response_class=HTMLResponse)
async def ngwords_page(request: Request, admin: Staff, session: Session) -> HTMLResponse:
    rows = (await session.execute(select(NgWord).order_by(NgWord.created_at.desc()))).scalars().all()
    return _page(request, "ngwords.html", admin, words=rows)


@router.post("/ngwords")
async def ngwords_create(
    request: Request,
    admin: Staff,
    session: Session,
    pattern: Annotated[str, Form(min_length=1, max_length=200)],
    is_regex: Annotated[bool, Form()] = False,
) -> Response:
    if is_regex:
        try:
            re.compile(pattern)
        except re.error:
            return _redirect("/admin/ngwords", "invalid_regex")
    session.add(NgWord(pattern=pattern, is_regex=is_regex))
    await audit(
        session, request, admin, "ngword.create", "ngword", None, {"pattern": pattern, "regex": is_regex}
    )
    await session.commit()
    await moderation.invalidate_ngwords()
    return _redirect("/admin/ngwords", "done")


@router.post("/ngwords/{word_id}/delete")
async def ngwords_delete(word_id: int, request: Request, admin: Staff, session: Session) -> Response:
    word = await session.get(NgWord, word_id)
    if word is not None:
        await session.delete(word)
        await audit(session, request, admin, "ngword.delete", "ngword", word_id, {"pattern": word.pattern})
        await session.commit()
        await moderation.invalidate_ngwords()
    return _redirect("/admin/ngwords", "done")


@router.get("/courses", response_class=HTMLResponse)
async def courses_page(request: Request, admin: Staff, session: Session) -> HTMLResponse:
    rows = (await session.execute(select(Course).order_by(Course.updated_at.desc()))).scalars().all()
    counts = dict(
        (
            await session.execute(select(CourseItem.course_id, func.count()).group_by(CourseItem.course_id))
        ).all()
    )
    return _page(request, "courses.html", admin, courses=rows, counts=counts)


@router.post("/courses")
async def courses_create(
    request: Request,
    admin: Staff,
    session: Session,
    slug: Annotated[str, Form(max_length=64)],
    title: Annotated[str, Form(min_length=1, max_length=80)],
) -> Response:
    if not SLUG_RE.match(slug):
        return _redirect("/admin/courses", "invalid_slug")
    exists = (await session.execute(select(Course.id).where(Course.slug == slug))).scalar_one_or_none()
    if exists:
        return _redirect("/admin/courses", "slug_taken")
    course = Course(slug=slug, title=title)
    session.add(course)
    await session.flush()
    await audit(session, request, admin, "course.create", "course", course.id, {"slug": slug})
    await session.commit()
    return _redirect(f"/admin/courses/{course.id}")


async def _course(session: AsyncSession, course_id: int) -> Course:
    stmt = (
        select(Course)
        .where(Course.id == course_id)
        .options(selectinload(Course.items).selectinload(CourseItem.sound))
    )
    course = (await session.execute(stmt)).scalar_one_or_none()
    if course is None:
        raise HTTPException(404, detail={"code": "not_found"})
    return course


@router.get("/courses/{course_id}", response_class=HTMLResponse)
async def course_edit(course_id: int, request: Request, admin: Staff, session: Session) -> HTMLResponse:
    course = await _course(session, course_id)
    return _page(request, "course_edit.html", admin, course=course)


@router.post("/courses/{course_id}")
async def course_update(
    course_id: int,
    request: Request,
    admin: Staff,
    session: Session,
    title: Annotated[str, Form(min_length=1, max_length=80)],
    description: Annotated[str, Form(max_length=500)] = "",
    is_public: Annotated[bool, Form()] = False,
    is_featured: Annotated[bool, Form()] = False,
) -> Response:
    course = await _course(session, course_id)
    course.title, course.description = title, description
    course.is_public, course.is_featured = is_public, is_featured
    await audit(session, request, admin, "course.update", "course", course.id, {"public": is_public})
    await session.commit()
    return _redirect(f"/admin/courses/{course_id}", "saved")


@router.post("/courses/{course_id}/delete")
async def course_delete(course_id: int, request: Request, admin: Staff, session: Session) -> Response:
    course = await _course(session, course_id)
    await session.delete(course)
    await audit(session, request, admin, "course.delete", "course", course_id, {"slug": course.slug})
    await session.commit()
    return _redirect("/admin/courses", "done")


@router.post("/courses/{course_id}/items")
async def course_add_item(
    course_id: int,
    request: Request,
    admin: Staff,
    session: Session,
    sound_id: Annotated[str, Form(max_length=200)],
    note: Annotated[str, Form(max_length=200)] = "",
) -> Response:
    course = await _course(session, course_id)
    sid = sound_id.strip().rstrip("/").rsplit("/", 1)[-1]
    sound = await session.get(Sound, sid)
    if sound is None or sound.status != SoundStatus.PUBLISHED:
        return _redirect(f"/admin/courses/{course_id}", "sound_not_found")
    pos = max((it.position for it in course.items), default=0) + 1
    session.add(CourseItem(course_id=course.id, sound_id=sound.id, position=pos, note=note))
    await audit(session, request, admin, "course.add_item", "course", course.id, {"sound_id": sound.id})
    await session.commit()
    return _redirect(f"/admin/courses/{course_id}", "saved")


@router.post("/courses/{course_id}/items/{item_id}/move")
async def course_move_item(
    course_id: int,
    item_id: int,
    request: Request,
    admin: Staff,
    session: Session,
    direction: Annotated[str, Form()],
) -> Response:
    course = await _course(session, course_id)
    items = list(course.items)
    idx = next((i for i, it in enumerate(items) if it.id == item_id), None)
    if idx is not None:
        j = idx - 1 if direction == "up" else idx + 1
        if 0 <= j < len(items):
            items[idx], items[j] = items[j], items[idx]
            for pos, it in enumerate(items, start=1):
                it.position = pos
            await audit(session, request, admin, "course.move_item", "course", course.id, {"item": item_id})
            await session.commit()
    return _redirect(f"/admin/courses/{course_id}")


@router.post("/courses/{course_id}/items/{item_id}/delete")
async def course_delete_item(
    course_id: int, item_id: int, request: Request, admin: Staff, session: Session
) -> Response:
    await session.execute(
        delete(CourseItem).where(CourseItem.id == item_id, CourseItem.course_id == course_id)
    )
    await audit(session, request, admin, "course.delete_item", "course", course_id, {"item": item_id})
    await session.commit()
    return _redirect(f"/admin/courses/{course_id}")


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, admin: Admin) -> HTMLResponse:
    current = await runtime_settings.load()
    return _page(request, "settings.html", admin, s=current, error=None)


@router.post("/settings")
async def settings_save(request: Request, admin: Admin, session: Session) -> Response:
    form = await request.form()
    current = await runtime_settings.load()
    data = current.model_dump()
    for key in data:
        if isinstance(data[key], bool):
            data[key] = form.get(key) in ("on", "true", "1")
        elif key in form:
            data[key] = form.get(key)
    try:
        new = runtime_settings.RuntimeSettings.model_validate(data)
    except ValidationError:
        return _page(request, "settings.html", admin, s=current, error="invalid")
    changes = {k: v for k, v in new.model_dump().items() if current.model_dump().get(k) != v}
    await runtime_settings.save(session, new)
    await audit(session, request, admin, "settings.update", "settings", None, changes)
    await session.commit()
    return _redirect("/admin/settings", "saved")


@router.get("/jobs", response_class=HTMLResponse)
async def jobs_page(
    request: Request, admin: Staff, session: Session, status: str | None = None
) -> HTMLResponse:
    stmt = select(ProcessingJob).order_by(ProcessingJob.created_at.desc()).limit(200)
    if status:
        stmt = stmt.where(ProcessingJob.status == status)
    rows = (await session.execute(stmt)).scalars().all()
    summary = dict(
        (
            await session.execute(select(ProcessingJob.status, func.count()).group_by(ProcessingJob.status))
        ).all()
    )
    queued = await get_redis().zcard("arq:queue")
    return _page(request, "jobs.html", admin, jobs=rows, summary=summary, status=status or "", queued=queued)


@router.post("/jobs/{job_id}/retry")
async def jobs_retry(job_id: str, request: Request, admin: Staff, session: Session) -> Response:
    job = await session.get(ProcessingJob, job_id)
    if job is None:
        return _redirect("/admin/jobs", "not_found")
    if job.status not in ("failed",) or not job.upload_path:
        return _redirect("/admin/jobs", "cannot_retry")
    sound = await session.get(Sound, job.sound_id)
    if sound is None:
        return _redirect("/admin/jobs", "not_found")
    if not Path(job.upload_path).exists():
        return _redirect("/admin/jobs", "upload_missing")
    sound.status = SoundStatus.PROCESSING
    sound.reject_reason = None
    job.status = "queued"
    job.error = None
    await audit(session, request, admin, "job.retry", "job", job.id)
    await session.commit()
    await enqueue("process_sound", job.id, _job_id=f"proc:{job.id}:{int(utcnow().timestamp())}")
    return _redirect("/admin/jobs", "done")


@router.get("/audit", response_class=HTMLResponse)
async def audit_page(
    request: Request,
    admin: Staff,
    session: Session,
    q: Annotated[str | None, Query(max_length=64)] = None,
    action: Annotated[str | None, Query(max_length=64)] = None,
    user: Annotated[str | None, Query(max_length=64)] = None,
    page: Annotated[int, Query(ge=1, le=1000)] = 1,
) -> HTMLResponse:
    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action.startswith(action))
    if user:
        stmt = stmt.where(AuditLog.admin_username == user)
    if q:
        stmt = stmt.where(or_(AuditLog.target_id == q, AuditLog.detail.cast(String).ilike(f"%{q}%")))
    stmt = stmt.order_by(AuditLog.created_at.desc()).offset((page - 1) * PER_PAGE).limit(PER_PAGE + 1)
    rows = list((await session.execute(stmt)).scalars().all())
    return _page(
        request,
        "audit.html",
        admin,
        logs=rows[:PER_PAGE],
        has_next=len(rows) > PER_PAGE,
        page=page,
        filters={"q": q or "", "action": action or "", "user": user or ""},
    )


@router.get("/accounts", response_class=HTMLResponse)
async def accounts_page(request: Request, admin: Admin, session: Session) -> HTMLResponse:
    rows = (await session.execute(select(AdminUser).order_by(AdminUser.id))).scalars().all()
    return _page(request, "accounts.html", admin, users=rows)


@router.post("/accounts")
async def accounts_create(
    request: Request,
    admin: Admin,
    session: Session,
    username: Annotated[str, Form(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")],
    password: Annotated[str, Form(min_length=12, max_length=256)],
    role: Annotated[str, Form()] = "moderator",
) -> Response:
    if role not in auth.ROLES:
        return _redirect("/admin/accounts", "invalid_role")
    exists = (
        await session.execute(select(AdminUser.id).where(AdminUser.username == username))
    ).scalar_one_or_none()
    if exists:
        return _redirect("/admin/accounts", "username_taken")
    user = AdminUser(username=username, password_hash=auth.hash_password(password), role=role)
    session.add(user)
    await session.flush()
    await audit(session, request, admin, "account.create", "admin_user", user.id, {"role": role})
    await session.commit()
    return _redirect("/admin/accounts", "done")


@router.post("/accounts/{user_id}")
async def accounts_update(
    user_id: int,
    request: Request,
    admin: Admin,
    session: Session,
    op: Annotated[str, Form()],
    role: Annotated[str, Form()] = "",
) -> Response:
    user = await session.get(AdminUser, user_id)
    if user is None:
        return _redirect("/admin/accounts", "not_found")
    if user.id == admin.user_id and op in ("disable", "role"):
        return _redirect("/admin/accounts", "cannot_change_self")
    if op == "disable":
        user.is_active = False
        await auth.destroy_user_sessions(user.id)
    elif op == "enable":
        user.is_active = True
    elif op == "role" and role in auth.ROLES:
        user.role = role
        await auth.destroy_user_sessions(user.id)
    elif op == "reset_totp":
        user.totp_enabled = False
        user.totp_secret = None
    else:
        return _redirect("/admin/accounts", "invalid")
    await audit(session, request, admin, f"account.{op}", "admin_user", user.id, {"role": role or None})
    await session.commit()
    return _redirect("/admin/accounts", "done")


@router.get("/security", response_class=HTMLResponse)
async def security_page(request: Request, admin: Staff, session: Session) -> HTMLResponse:
    user = await session.get(AdminUser, admin.user_id)
    assert user is not None
    qr = None
    secret = None
    if not user.totp_enabled:
        redis = get_redis()
        key = f"totp:pending:{user.id}"
        raw = await redis.get(key)
        secret = raw.decode() if raw else auth.new_totp_secret()
        await redis.set(key, secret.encode(), ex=900)
        uri = auth.totp_uri(secret, user.username)
        qr = Markup(segno.make(uri, micro=False).svg_inline(scale=5, dark="#111", light="#fff"))  # noqa: S704
    return _page(request, "security.html", admin, user=user, qr=qr, secret=secret)


@router.post("/security/totp/enable")
async def totp_enable(
    request: Request, admin: Staff, session: Session, code: Annotated[str, Form(max_length=12)]
) -> Response:
    user = await session.get(AdminUser, admin.user_id)
    assert user is not None
    raw = await get_redis().get(f"totp:pending:{user.id}")
    if not raw or not auth.verify_totp(raw.decode(), code):
        return _redirect("/admin/security", "invalid_code")
    user.totp_secret = raw.decode()
    user.totp_enabled = True
    await get_redis().delete(f"totp:pending:{user.id}")
    await audit(session, request, admin, "totp.enable", "admin_user", user.id)
    await session.commit()
    return _redirect("/admin/security", "totp_enabled")


@router.post("/security/totp/disable")
async def totp_disable(
    request: Request, admin: Staff, session: Session, code: Annotated[str, Form(max_length=12)]
) -> Response:
    user = await session.get(AdminUser, admin.user_id)
    assert user is not None
    if not user.totp_secret or not auth.verify_totp(user.totp_secret, code):
        return _redirect("/admin/security", "invalid_code")
    user.totp_enabled = False
    user.totp_secret = None
    await audit(session, request, admin, "totp.disable", "admin_user", user.id)
    await session.commit()
    return _redirect("/admin/security", "totp_disabled")


@router.post("/security/password")
async def change_password(
    request: Request,
    admin: Staff,
    session: Session,
    current: Annotated[str, Form(max_length=256)],
    new: Annotated[str, Form(min_length=12, max_length=256)],
) -> Response:
    user = await session.get(AdminUser, admin.user_id)
    assert user is not None
    if not auth.verify_password(user.password_hash, current):
        return _redirect("/admin/security", "invalid_password")
    user.password_hash = auth.hash_password(new)
    await audit(session, request, admin, "password.change", "admin_user", user.id)
    await session.commit()
    return _redirect("/admin/security", "password_changed")
