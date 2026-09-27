from __future__ import annotations

import httpx
from sqlalchemy import select

from app.db import sessionmaker
from app.models import Report, ReporterTrust, Sound, SoundStatus
from app.services import moderation, ratelimit, runtime_settings

from .conftest import create_sound, ip_headers


async def report(client: httpx.AsyncClient, sid: str, ip: str, reason: str = "spam") -> httpx.Response:
    return await client.post(
        f"/api/sounds/{sid}/reports",
        json={"reason": reason, "detail": "", "cf-turnstile-response": "ok"},
        headers=ip_headers(ip),
    )


async def get_sound(sid: str) -> Sound:
    async with sessionmaker()() as session:
        s = await session.get(Sound, sid)
        assert s is not None
        return s


async def test_reports_hide_sound_at_threshold(client: httpx.AsyncClient) -> None:
    sound = await create_sound()
    for i in range(2):
        r = await report(client, sound.id, f"203.0.113.{i + 1}")
        assert r.status_code == 201
    assert (await get_sound(sound.id)).status == SoundStatus.PUBLISHED
    r = await report(client, sound.id, "203.0.113.3")
    assert r.status_code == 201
    s = await get_sound(sound.id)
    assert s.status == SoundStatus.PENDING
    assert s.hidden_reason == "reports"
    assert (await client.get(f"/api/sounds/{sound.id}")).status_code == 404


async def test_duplicate_report_from_same_ip_is_rejected(client: httpx.AsyncClient) -> None:
    sound = await create_sound()
    assert (await report(client, sound.id, "203.0.113.50")).status_code == 201
    r = await report(client, sound.id, "203.0.113.50", reason="voice")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "already_reported"
    for _ in range(3):
        await report(client, sound.id, "203.0.113.50")
    assert (await get_sound(sound.id)).status == SoundStatus.PUBLISHED
    async with sessionmaker()() as session:
        rows = (await session.execute(select(Report).where(Report.sound_id == sound.id))).scalars().all()
    assert len(rows) == 1


async def test_other_requires_detail(client: httpx.AsyncClient) -> None:
    sound = await create_sound()
    r = await report(client, sound.id, "203.0.113.60", reason="other")
    assert r.status_code == 422


async def test_low_trust_reporters_have_less_weight(client: httpx.AsyncClient) -> None:
    sound = await create_sound()
    from app.services.security import hash_ip

    ips = [f"192.0.2.{i}" for i in range(1, 6)]
    async with sessionmaker()() as session:
        for ip in ips:
            for _ in range(9):
                await moderation.update_trust(session, [hash_ip(ip)], upheld=False)
        await session.commit()
        trust = await session.get(ReporterTrust, hash_ip(ips[0]))
        assert trust is not None and trust.trust < 0.2
    for ip in ips:
        assert (await report(client, sound.id, ip)).status_code == 201
    assert (await get_sound(sound.id)).status == SoundStatus.PUBLISHED


def test_trust_formula() -> None:
    assert moderation.compute_trust(0, 0) == 1.0
    assert moderation.compute_trust(5, 0) == 1.0
    assert moderation.compute_trust(0, 1) == 0.5
    assert moderation.compute_trust(0, 50) == moderation.MIN_TRUST


async def test_banned_ip_cannot_report_or_post(client: httpx.AsyncClient) -> None:
    from app.models import Ban
    from app.services.security import hash_ip

    sound = await create_sound()
    async with sessionmaker()() as session:
        session.add(Ban(ip_hash=hash_ip("203.0.113.99"), reason="test"))
        await session.commit()
    r = await report(client, sound.id, "203.0.113.99")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "banned"
    r = await client.post(
        "/api/sounds",
        data={"title": "x", "lat": "35", "lng": "139", "agree": "true", "cf-turnstile-response": "ok"},
        files={"file": ("a.wav", b"RIFF0000", "audio/wav")},
        headers=ip_headers("203.0.113.99"),
    )
    assert r.status_code == 403


async def test_sliding_window() -> None:
    for i in range(3):
        res = await ratelimit.hit("t", "ip1", 3, 60)
        assert res.allowed and res.count == i + 1
    res = await ratelimit.hit("t", "ip1", 3, 60)
    assert not res.allowed
    assert 0 < res.retry_after_ms <= 60_000
    assert (await ratelimit.hit("t", "ip2", 3, 60)).allowed


async def test_window_slides(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    now = [1_000_000.0]
    monkeypatch.setattr(ratelimit.time, "time", lambda: now[0])
    for _ in range(2):
        assert (await ratelimit.hit("s", "ip", 2, 10)).allowed
    assert not (await ratelimit.hit("s", "ip", 2, 10)).allowed
    now[0] += 11
    assert (await ratelimit.hit("s", "ip", 2, 10)).allowed


async def test_post_rate_limit(client: httpx.AsyncClient) -> None:
    rs = runtime_settings.defaults()
    ip = "203.0.113.200"
    codes = []
    for _ in range(rs.rate_post_per_hour + 1):
        r = await client.post(
            "/api/sounds",
            data={
                "title": "x",
                "lat": "35.6",
                "lng": "139.7",
                "agree": "true",
                "cf-turnstile-response": "ok",
            },
            files={"file": ("a.wav", b"RIFF0000WAVEfmt ", "audio/wav")},
            headers=ip_headers(ip),
        )
        codes.append(r.status_code)
    assert codes[:-1] == [202] * rs.rate_post_per_hour
    assert codes[-1] == 429


async def test_report_rate_limit(client: httpx.AsyncClient) -> None:
    rs = runtime_settings.defaults()
    ip = "203.0.113.201"
    sounds = [await create_sound() for _ in range(rs.rate_report_per_hour + 1)]
    codes = [(await report(client, s.id, ip)).status_code for s in sounds]
    assert codes[-1] == 429
    assert all(c == 201 for c in codes[:-1])


async def test_pins_rate_limit(client: httpx.AsyncClient) -> None:
    async with sessionmaker()() as session:
        rs = runtime_settings.defaults()
        rs.rate_pins_per_minute = 10
        await runtime_settings.save(session, rs)
    codes = [
        (
            await client.get("/api/pins?z=10&bbox=139,35,140,36", headers=ip_headers("203.0.113.202"))
        ).status_code
        for _ in range(11)
    ]
    assert codes[:10] == [200] * 10
    assert codes[10] == 429
