from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import orjson
import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import AdminUser, AuditLog
from app.services.redis import get_redis
from app.services.security import ip_hash_of

COOKIE = "kk_admin"
ROLES = ("admin", "moderator")

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


# 存在しないユーザーでも検証して、応答時間からユーザー名の有無を推測されないようにする
_DUMMY_HASH = _hasher.hash(secrets.token_hex(16))


def verify_dummy(password: str) -> None:
    verify_password(_DUMMY_HASH, password)


def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, username: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name="きこえる地図")


def verify_totp(secret: str, code: str) -> bool:
    code = code.strip().replace(" ", "")
    if not code.isdigit() or len(code) != 6:
        return False
    return pyotp.TOTP(secret).verify(code, valid_window=1)


@dataclass
class AdminSession:
    sid_hash: str
    csrf: str
    user_id: int | None = None
    username: str | None = None
    role: str | None = None
    stage: str = "anon"

    @property
    def is_admin(self) -> bool:
        return self.stage == "full" and self.role == "admin"

    @property
    def is_authenticated(self) -> bool:
        return self.stage == "full" and self.user_id is not None

    def to_json(self) -> bytes:
        d: dict[str, Any] = {
            "csrf": self.csrf,
            "user_id": self.user_id,
            "username": self.username,
            "role": self.role,
            "stage": self.stage,
        }
        return orjson.dumps(d)


def _sid_hash(sid: str) -> str:
    return hmac.new(get_settings().session_secret.encode(), sid.encode(), hashlib.sha256).hexdigest()


def _key(sid_hash: str) -> str:
    return f"adm:sess:{sid_hash}"


async def load_session(request: Request) -> AdminSession | None:
    sid = request.cookies.get(COOKIE)
    if not sid or len(sid) > 128:
        return None
    sid_hash = _sid_hash(sid)
    raw = await get_redis().get(_key(sid_hash))
    if not raw:
        return None
    d = orjson.loads(raw)
    return AdminSession(sid_hash=sid_hash, **d)


async def save_session(sess: AdminSession) -> None:
    await get_redis().set(_key(sess.sid_hash), sess.to_json(), ex=get_settings().session_ttl_seconds)


async def start_session(**fields: Any) -> tuple[str, AdminSession]:
    # セッション固定攻撃を防ぐため、ログイン段階が変わるたびに ID を作り直す
    sid = secrets.token_urlsafe(32)
    sess = AdminSession(sid_hash=_sid_hash(sid), csrf=secrets.token_urlsafe(32), **fields)
    await save_session(sess)
    return sid, sess


def attach_cookie(response: Response, sid: str) -> None:
    settings = get_settings()
    response.set_cookie(
        COOKIE,
        sid,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )


async def create_session(response: Response, **fields: Any) -> AdminSession:
    sid, sess = await start_session(**fields)
    attach_cookie(response, sid)
    return sess


async def destroy_session(request: Request, response: Response) -> None:
    sess = await load_session(request)
    if sess:
        await get_redis().delete(_key(sess.sid_hash))
    response.delete_cookie(COOKIE, path="/")


async def destroy_user_sessions(user_id: int) -> None:
    redis = get_redis()
    async for key in redis.scan_iter(match="adm:sess:*", count=500):
        raw = await redis.get(key)
        if raw and orjson.loads(raw).get("user_id") == user_id:
            await redis.delete(key)


def csrf_ok(sess: AdminSession, token: str | None) -> bool:
    return bool(token) and hmac.compare_digest(sess.csrf, token or "")


async def check_csrf(request: Request, sess: AdminSession) -> None:
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    token = request.headers.get("x-csrf-token")
    if not token:
        ctype = request.headers.get("content-type", "")
        if ctype.startswith(("multipart/form-data", "application/x-www-form-urlencoded")):
            form = await request.form()
            value = form.get("csrf_token")
            token = value if isinstance(value, str) else None
    if not csrf_ok(sess, token):
        raise HTTPException(status_code=403, detail={"code": "csrf_failed"})


class LoginRequired(Exception):
    pass


def require_role(*roles: str) -> Callable[[Request], Awaitable[AdminSession]]:
    async def dependency(request: Request) -> AdminSession:
        sess = await load_session(request)
        if sess is None or not sess.is_authenticated:
            if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
                raise LoginRequired()
            raise HTTPException(status_code=401, detail={"code": "login_required"})
        if roles and sess.role not in roles:
            raise HTTPException(status_code=403, detail={"code": "forbidden"})
        await check_csrf(request, sess)
        await save_session(sess)
        request.state.admin = sess
        return sess

    return dependency


require_staff = require_role()
require_admin = require_role("admin")


async def login_locked(ident: str) -> bool:
    raw = await get_redis().get(f"login:fail:{ident}")
    return bool(raw) and int(raw) >= get_settings().login_max_failures


async def record_login_failure(*idents: str) -> None:
    redis = get_redis()
    pipe = redis.pipeline(transaction=False)
    for ident in idents:
        pipe.incr(f"login:fail:{ident}")
        pipe.expire(f"login:fail:{ident}", get_settings().login_lock_seconds)
    await pipe.execute()


async def clear_login_failures(*idents: str) -> None:
    await get_redis().delete(*(f"login:fail:{i}" for i in idents))


async def audit(
    session: AsyncSession,
    request: Request,
    admin: AdminSession,
    action: str,
    target_type: str | None = None,
    target_id: str | int | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            admin_id=admin.user_id,
            admin_username=admin.username or "?",
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            detail=detail or {},
            ip_hash=ip_hash_of(request),
        )
    )


def user_session_fields(user: AdminUser, stage: str) -> dict[str, Any]:
    return {"user_id": user.id, "username": user.username, "role": user.role, "stage": stage}


async def totp_code_unused(user_id: int, code: str) -> bool:
    return bool(await get_redis().set(f"totp:used:{user_id}:{code}", b"1", nx=True, ex=120))
