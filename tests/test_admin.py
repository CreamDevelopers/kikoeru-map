from __future__ import annotations

import re

import httpx
import pyotp
import pytest
from sqlalchemy import select

from app.db import sessionmaker
from app.models import AdminUser, AuditLog, Report, Sound, SoundStatus
from app.services import runtime_settings, turnstile
from app.services.auth import hash_password

from .conftest import create_sound, ip_headers

PASSWORD = "correct horse battery staple"


async def make_user(username: str, role: str, totp_secret: str | None = None) -> AdminUser:
    async with sessionmaker()() as session:
        u = AdminUser(
            username=username,
            password_hash=hash_password(PASSWORD),
            role=role,
            totp_secret=totp_secret,
            totp_enabled=totp_secret is not None,
        )
        session.add(u)
        await session.commit()
        return u


def csrf_from(html: str) -> str:
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert m, "csrf token not found"
    return m.group(1)


async def login(
    client: httpx.AsyncClient, username: str, password: str = PASSWORD, ip: str = "203.0.113.30"
) -> httpx.Response:
    page = await client.get("/admin/login", headers=ip_headers(ip))
    token = csrf_from(page.text)
    return await client.post(
        "/admin/login",
        data={"username": username, "password": password, "csrf_token": token, "cf-turnstile-response": "ok"},
        headers=ip_headers(ip),
    )


async def admin_csrf(client: httpx.AsyncClient) -> str:
    page = await client.get("/admin/ngwords", headers={"Accept": "text/html"})
    assert page.status_code == 200
    return csrf_from(page.text)


async def test_admin_requires_login(client: httpx.AsyncClient) -> None:
    r = await client.get("/admin", headers={"Accept": "text/html"})
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/login"
    r = await client.get("/admin/api/stats")
    assert r.status_code == 401
    r = await client.get("/metrics")
    assert r.status_code == 403


async def test_login_success_sets_secure_cookie_and_audits(client: httpx.AsyncClient) -> None:
    await make_user("alice", "admin")
    r = await login(client, "alice")
    assert r.status_code == 303 and r.headers["location"] == "/admin"
    cookie = r.headers["set-cookie"]
    assert (
        "kk_admin=" in cookie
        and "HttpOnly" in cookie
        and "Secure" in cookie
        and "SameSite=lax" in cookie.replace("Lax", "lax")
    )
    assert (await client.get("/admin", headers={"Accept": "text/html"})).status_code == 200
    assert (await client.get("/metrics")).status_code == 200
    async with sessionmaker()() as session:
        logs = (await session.execute(select(AuditLog.action))).scalars().all()
    assert "login" in logs


async def test_login_wrong_password_and_lockout(client: httpx.AsyncClient) -> None:
    await make_user("bob", "admin")
    for _ in range(5):
        r = await login(client, "bob", "wrong-password", ip="203.0.113.31")
        assert r.headers["location"].endswith("error=invalid")
    r = await login(client, "bob", ip="203.0.113.31")
    assert r.headers["location"].endswith("error=locked")


async def test_login_requires_turnstile(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    await make_user("carol", "admin")
    monkeypatch.setattr(
        turnstile,
        "client_factory",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"success": False}))
        ),
    )
    r = await login(client, "carol")
    assert r.headers["location"].endswith("error=turnstile")


async def test_login_requires_csrf(client: httpx.AsyncClient) -> None:
    await make_user("dave", "admin")
    await client.get("/admin/login")
    r = await client.post(
        "/admin/login",
        data={
            "username": "dave",
            "password": PASSWORD,
            "csrf_token": "forged",
            "cf-turnstile-response": "ok",
        },
    )
    assert r.headers["location"].endswith("error=csrf")


async def test_totp_flow(client: httpx.AsyncClient) -> None:
    secret = pyotp.random_base32()
    await make_user("erin", "admin", totp_secret=secret)
    r = await login(client, "erin")
    assert r.headers["location"] == "/admin/login/totp"
    assert (await client.get("/admin", headers={"Accept": "text/html"})).status_code == 303
    page = await client.get("/admin/login/totp")
    token = csrf_from(page.text)
    r = await client.post("/admin/login/totp", data={"code": "000000", "csrf_token": token})
    assert r.headers["location"].endswith("error=invalid")
    code = pyotp.TOTP(secret).now()
    r = await client.post("/admin/login/totp", data={"code": code, "csrf_token": token})
    assert r.headers["location"] == "/admin"
    assert (await client.get("/admin", headers={"Accept": "text/html"})).status_code == 200


async def test_csrf_required_for_admin_posts(client: httpx.AsyncClient) -> None:
    await make_user("frank", "admin")
    await login(client, "frank")
    r = await client.post("/admin/ngwords", data={"pattern": "spam"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "csrf_failed"
    r = await client.post("/admin/ngwords", data={"pattern": "spam", "csrf_token": "wrong"})
    assert r.status_code == 403
    token = await admin_csrf(client)
    r = await client.post("/admin/ngwords", data={"pattern": "spam", "csrf_token": token})
    assert r.status_code == 303
    s = await create_sound(status=SoundStatus.PENDING, hidden_reason="voice")
    r = await client.post(f"/admin/api/review/{s.id}", json={"decision": "approve"})
    assert r.status_code == 403
    r = await client.post(
        f"/admin/api/review/{s.id}", json={"decision": "approve"}, headers={"X-CSRF-Token": token}
    )
    assert r.status_code == 200 and r.json()["status"] == SoundStatus.PUBLISHED


async def test_moderator_cannot_change_settings_or_accounts(client: httpx.AsyncClient) -> None:
    await make_user("mod", "moderator")
    await login(client, "mod")
    token = await admin_csrf(client)
    r = await client.get("/admin/settings", headers={"Accept": "text/html"})
    assert r.status_code == 403
    r = await client.post("/admin/settings", data={"csrf_token": token, "posting_paused": "on"})
    assert r.status_code == 403
    assert (await runtime_settings.load()).posting_paused is False
    r = await client.post(
        "/admin/accounts",
        data={"csrf_token": token, "username": "evil", "password": "x" * 20, "role": "admin"},
    )
    assert r.status_code == 403
    s = await create_sound()
    r = await client.post(f"/admin/sounds/{s.id}/action", data={"csrf_token": token, "row_action": "hide"})
    assert r.status_code == 303
    async with sessionmaker()() as session:
        assert (await session.get(Sound, s.id)).status == SoundStatus.HIDDEN  # type: ignore[union-attr]


async def test_admin_can_change_settings(client: httpx.AsyncClient) -> None:
    await make_user("root", "admin")
    await login(client, "root")
    token = await admin_csrf(client)
    current = await runtime_settings.load()
    data = {k: str(v) for k, v in current.model_dump().items() if not isinstance(v, bool)}
    data.update(csrf_token=token, posting_paused="on", report_threshold="5")
    r = await client.post("/admin/settings", data=data)
    assert r.status_code == 303
    new = await runtime_settings.load()
    assert new.posting_paused is True and new.report_threshold == 5
    r = await client.post(
        "/api/sounds",
        data={"title": "x", "lat": "35.6", "lng": "139.7", "agree": "true", "cf-turnstile-response": "ok"},
        files={"file": ("a.wav", b"RIFF0000", "audio/wav")},
        headers=ip_headers("203.0.113.44"),
    )
    assert r.status_code == 503


async def test_report_resolution_ok_restores_and_lowers_trust(client: httpx.AsyncClient) -> None:
    await make_user("judge", "moderator")
    s = await create_sound()
    for i in range(3):
        await client.post(
            f"/api/sounds/{s.id}/reports",
            json={"reason": "spam", "cf-turnstile-response": "ok"},
            headers=ip_headers(f"203.0.113.{120 + i}"),
        )
    async with sessionmaker()() as session:
        assert (await session.get(Sound, s.id)).status == SoundStatus.PENDING  # type: ignore[union-attr]
    await login(client, "judge")
    token = await admin_csrf(client)
    r = await client.post(f"/admin/reports/{s.id}/resolve", data={"csrf_token": token, "decision": "ok"})
    assert r.status_code == 303
    async with sessionmaker()() as session:
        assert (await session.get(Sound, s.id)).status == SoundStatus.PUBLISHED  # type: ignore[union-attr]
        statuses = (await session.execute(select(Report.status))).scalars().all()
        assert set(statuses) == {"dismissed"}
        actions = (await session.execute(select(AuditLog.action))).scalars().all()
        assert "report.ok" in actions


async def test_ban_from_report_blocks_poster(client: httpx.AsyncClient) -> None:
    from app.services.security import hash_ip

    await make_user("banner", "admin")
    s = await create_sound(ip_hash=hash_ip("203.0.113.66"))
    await login(client, "banner")
    token = await admin_csrf(client)
    r = await client.post(
        f"/admin/reports/{s.id}/resolve", data={"csrf_token": token, "decision": "ban", "ban_days": "7"}
    )
    assert r.status_code == 303
    r = await client.post(
        "/api/sounds",
        data={"title": "x", "lat": "35.6", "lng": "139.7", "agree": "true", "cf-turnstile-response": "ok"},
        files={"file": ("a.wav", b"RIFF0000", "audio/wav")},
        headers=ip_headers("203.0.113.66"),
    )
    assert r.status_code == 403


async def test_non_public_media_only_for_staff(client: httpx.AsyncClient) -> None:
    from app.services import media

    s = await create_sound(status=SoundStatus.PENDING, webm_hash="d" * 16)
    path = media.file_path(s.id, "d" * 16, "webm")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x1a\x45\xdf\xa3" + b"\0" * 100)
    assert (await client.get(f"/media/{s.id}/{'d' * 16}.webm")).status_code == 404
    await make_user("viewer", "moderator")
    await login(client, "viewer")
    r = await client.get(f"/media/{s.id}/{'d' * 16}.webm")
    assert r.status_code == 200
    assert "no-store" in r.headers["cache-control"]
