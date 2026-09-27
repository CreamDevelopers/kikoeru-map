from __future__ import annotations

import datetime as dt
import hashlib
import math
import random
import secrets
from dataclasses import asdict, dataclass, field
from typing import Any

import orjson
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DailyChallenge, Sound, SoundStatus
from app.schemas import JST
from app.services import sounds as sound_svc
from app.services.geo import haversine_m
from app.services.redis import get_redis

ROUNDS = 5
MAX_SCORE = 5000
SCALE_KM = 150.0
PERFECT_M = 50.0
GAME_TTL = 60 * 60 * 3


def score_for_distance(distance_m: float) -> int:
    if distance_m <= PERFECT_M:
        return MAX_SCORE
    return round(MAX_SCORE * math.exp(-(distance_m / 1000.0) / SCALE_KM))


@dataclass
class GameState:
    id: str
    mode: str
    date: str | None
    sound_ids: list[str]
    guesses: list[dict[str, Any]] = field(default_factory=list)
    submitted: bool = False

    @property
    def round(self) -> int:
        return len(self.guesses)

    @property
    def finished(self) -> bool:
        return len(self.guesses) >= len(self.sound_ids)

    @property
    def total(self) -> int:
        return sum(int(g["score"]) for g in self.guesses)


def today_jst() -> dt.date:
    return dt.datetime.now(JST).date()


async def save(state: GameState) -> None:
    await get_redis().set(f"game:{state.id}", orjson.dumps(asdict(state)), ex=GAME_TTL)


async def load(game_id: str) -> GameState | None:
    if not game_id or len(game_id) > 64:
        return None
    raw = await get_redis().get(f"game:{game_id}")
    return GameState(**orjson.loads(raw)) if raw else None


async def eligible_ids(session: AsyncSession) -> list[str]:
    stmt = (
        select(Sound.id)
        .where(Sound.status == SoundStatus.PUBLISHED, Sound.game_ok.is_(True), Sound.precision == "exact")
        .order_by(Sound.id)
    )
    return list((await session.execute(stmt)).scalars().all())


async def daily_sound_ids(session: AsyncSession, day: dt.date) -> list[str] | None:
    existing = await session.get(DailyChallenge, day)
    if existing:
        return list(existing.sound_ids)
    pool = await eligible_ids(session)
    if len(pool) < ROUNDS:
        return None
    seed = int.from_bytes(hashlib.sha256(day.isoformat().encode()).digest()[:8], "big")
    chosen = random.Random(seed).sample(pool, ROUNDS)
    stmt = insert(DailyChallenge).values(date=day, sound_ids=chosen).on_conflict_do_nothing()
    await session.execute(stmt)
    await session.commit()
    row = await session.get(DailyChallenge, day, populate_existing=True)
    return list(row.sound_ids) if row else chosen


async def random_sound_ids(session: AsyncSession) -> list[str] | None:
    chosen: list[str] = []
    for _ in range(ROUNDS * 3):
        s = await sound_svc.random_pick(session, game_only=True, exclude=chosen)
        if s is None:
            break
        chosen.append(s.id)
        if len(chosen) == ROUNDS:
            return chosen
    return None


async def start(session: AsyncSession, mode: str) -> GameState | None:
    if mode == "daily":
        day = today_jst()
        ids = await daily_sound_ids(session, day)
        date: str | None = day.isoformat()
    else:
        ids = await random_sound_ids(session)
        date = None
    if not ids:
        return None
    state = GameState(id=secrets.token_urlsafe(18), mode=mode, date=date, sound_ids=ids)
    await save(state)
    return state


def round_info(state: GameState, sound: Sound | None) -> dict[str, Any]:
    n = state.round
    base = f"/api/game/{state.id}/audio/{n}"
    return {
        "game_id": state.id,
        "mode": state.mode,
        "round": n + 1,
        "rounds": len(state.sound_ids),
        "total": state.total,
        "finished": state.finished,
        "audio": None
        if state.finished or sound is None
        else {"webm": base + ".webm", "m4a": base + ".m4a", "duration": sound.duration_sec},
    }


def answer_of(sound: Sound) -> dict[str, Any]:
    return {
        "id": sound.id,
        "lat": sound.lat,
        "lng": sound.lng,
        "title": sound.title,
        "pref_name": sound.pref_name,
        "city_name": sound.city_name,
        "page_url": f"/s/{sound.id}",
    }


def record_guess(state: GameState, sound: Sound, lat: float, lng: float) -> dict[str, Any]:
    distance = haversine_m(lat, lng, sound.lat, sound.lng)
    score = score_for_distance(distance)
    entry = {
        "lat": lat,
        "lng": lng,
        "score": score,
        "distance_m": round(distance),
        "answer": answer_of(sound),
    }
    state.guesses.append(entry)
    return entry


def share_text(state: GameState, base_url: str) -> str:
    bars = []
    for g in state.guesses:
        s = int(g["score"])
        bars.append("■" * max(0, round(s / 1000)) + "□" * (5 - max(0, round(s / 1000))))
    label = f"みみあて {state.date}" if state.mode == "daily" else "みみあて"
    return (
        f"{label} {state.total}/{MAX_SCORE * len(state.sound_ids)}\n" + "\n".join(bars) + f"\n{base_url}/game"
    )
