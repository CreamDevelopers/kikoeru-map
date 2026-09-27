from __future__ import annotations

import datetime as dt
import re
import unicodedata

import orjson
from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Ban, NgWord, Report, ReporterTrust, Sound, SoundStatus, utcnow
from app.services.redis import get_redis

NG_CACHE_KEY = "ngwords:v1"
BAN_CACHE_PREFIX = "ban:"

MIN_TRUST = 0.1


_KATA_TO_HIRA = {c: c - 0x60 for c in range(ord("ァ"), ord("ヶ") + 1)}


def normalize_text(text: str) -> str:
    norm = unicodedata.normalize("NFKC", text).translate(_KATA_TO_HIRA)
    return re.sub(r"\s+", "", norm).lower()


async def _load_ngwords(session: AsyncSession) -> list[tuple[str, bool]]:
    redis = get_redis()
    raw = await redis.get(NG_CACHE_KEY)
    if raw:
        return [(p, bool(r)) for p, r in orjson.loads(raw)]
    rows = (await session.execute(select(NgWord.pattern, NgWord.is_regex))).all()
    words = [(p, bool(r)) for p, r in rows]
    await redis.set(NG_CACHE_KEY, orjson.dumps(words), ex=300)
    return words


async def invalidate_ngwords() -> None:
    await get_redis().delete(NG_CACHE_KEY)


def match_ngword(text: str, words: list[tuple[str, bool]]) -> str | None:
    if not text:
        return None
    norm = normalize_text(text)
    for pattern, is_regex in words:
        if is_regex:
            try:
                if re.search(pattern, text, re.IGNORECASE) or re.search(pattern, norm, re.IGNORECASE):
                    return pattern
            except re.error:
                continue
        elif normalize_text(pattern) and normalize_text(pattern) in norm:
            return pattern
    return None


async def check_ngwords(session: AsyncSession, *texts: str) -> None:
    words = await _load_ngwords(session)
    for text in texts:
        if match_ngword(text, words):
            raise HTTPException(status_code=422, detail={"code": "ng_word"})


async def is_banned(session: AsyncSession, ip_hash: str) -> bool:
    redis = get_redis()
    key = BAN_CACHE_PREFIX + ip_hash
    cached = await redis.get(key)
    if cached is not None:
        return cached == b"1"
    now = utcnow()
    stmt = (
        select(func.count())
        .select_from(Ban)
        .where(
            Ban.ip_hash == ip_hash,
            Ban.lifted_at.is_(None),
            or_(Ban.expires_at.is_(None), Ban.expires_at > now),
        )
    )
    banned = bool((await session.execute(stmt)).scalar_one())
    await redis.set(key, b"1" if banned else b"0", ex=60)
    return banned


async def invalidate_ban(ip_hash: str) -> None:
    await get_redis().delete(BAN_CACHE_PREFIX + ip_hash)


async def ensure_not_banned(session: AsyncSession, ip_hash: str) -> None:
    if await is_banned(session, ip_hash):
        raise HTTPException(status_code=403, detail={"code": "banned"})


def compute_trust(upheld: int, dismissed: int) -> float:
    total = upheld + dismissed
    if total == 0:
        return 1.0
    trust = (upheld + 1) / (total + 1)
    return max(MIN_TRUST, min(1.0, trust))


async def reporter_weight(session: AsyncSession, ip_hash: str) -> float:
    row = await session.get(ReporterTrust, ip_hash)
    return row.trust if row else 1.0


async def open_report_weight(session: AsyncSession, sound_id: str) -> float:
    stmt = select(func.coalesce(func.sum(Report.weight), 0.0)).where(
        Report.sound_id == sound_id, Report.status == "open"
    )
    return float((await session.execute(stmt)).scalar_one())


async def apply_report_threshold(session: AsyncSession, sound: Sound, threshold: float) -> bool:
    if sound.status != SoundStatus.PUBLISHED:
        return False
    total = await open_report_weight(session, sound.id)
    if total + 1e-9 >= threshold:
        sound.status = SoundStatus.PENDING
        sound.hidden_reason = "reports"
        return True
    return False


async def update_trust(session: AsyncSession, ip_hashes: list[str], *, upheld: bool) -> None:
    for ip_hash in set(ip_hashes):
        row = await session.get(ReporterTrust, ip_hash)
        if row is None:
            row = ReporterTrust(ip_hash=ip_hash, upheld=0, dismissed=0, trust=1.0)
            session.add(row)
        if upheld:
            row.upheld += 1
        else:
            row.dismissed += 1
        row.trust = compute_trust(row.upheld, row.dismissed)


def ban_expiry(days: int | None) -> dt.datetime | None:
    if not days:
        return None
    return utcnow() + dt.timedelta(days=days)
