from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models import LeaderboardEntry, Sound
from app.schemas import GuessIn, LeaderboardIn
from app.services import game, media, moderation
from app.services.security import ip_hash_of
from app.services.turnstile import TurnstileResult, require_turnstile

router = APIRouter(prefix="/api/game", tags=["game"])
Session = Annotated[AsyncSession, Depends(get_session)]


class StartIn(BaseModel):
    mode: str = "random"


def _err(status: int, code: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code})


async def _state(game_id: str) -> game.GameState:
    state = await game.load(game_id)
    if state is None:
        raise _err(404, "game_not_found")
    return state


async def _current_sound(session: AsyncSession, state: game.GameState) -> Sound | None:
    if state.finished:
        return None
    return await session.get(Sound, state.sound_ids[state.round])


@router.post("/start")
async def start(body: StartIn, session: Session) -> dict[str, Any]:
    if body.mode not in ("random", "daily"):
        raise _err(422, "invalid_mode")
    state = await game.start(session, body.mode)
    if state is None:
        raise _err(409, "not_enough_sounds")
    return game.round_info(state, await _current_sound(session, state))


@router.get("/daily/ranking")
async def ranking(session: Session) -> dict[str, Any]:
    day = game.today_jst()
    rows = (
        await session.execute(
            select(LeaderboardEntry.nickname, LeaderboardEntry.score)
            .where(LeaderboardEntry.date == day)
            .order_by(LeaderboardEntry.score.desc(), LeaderboardEntry.created_at)
            .limit(50)
        )
    ).all()
    return {"date": day.isoformat(), "items": [{"nickname": n, "score": s} for n, s in rows]}


@router.get("/{game_id}")
async def current(game_id: str, session: Session) -> dict[str, Any]:
    state = await _state(game_id)
    return game.round_info(state, await _current_sound(session, state))


@router.get("/{game_id}/audio/{n}.{ext}")
async def round_audio(game_id: str, n: int, ext: str, session: Session) -> FileResponse:
    # 正解が推測されないよう、投稿 ID を含まない URL で出題中の音だけを配信する
    state = await _state(game_id)
    if n != state.round or state.finished or ext not in ("webm", "m4a"):
        raise _err(404, "not_found")
    sound = await session.get(Sound, state.sound_ids[n])
    h = (sound.webm_hash if ext == "webm" else sound.m4a_hash) if sound else None
    if sound is None or not h:
        raise _err(404, "not_found")
    path = media.file_path(sound.id, h, ext)
    return FileResponse(
        path,
        media_type=media.EXT_TYPES[ext],
        headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"},
        content_disposition_type="inline",
    )


@router.post("/{game_id}/guess")
async def guess(game_id: str, body: GuessIn, session: Session) -> dict[str, Any]:
    state = await _state(game_id)
    if state.finished:
        raise _err(409, "game_finished")
    sound = await _current_sound(session, state)
    if sound is None:
        raise _err(410, "sound_unavailable")
    entry = game.record_guess(state, sound, body.lat, body.lng)
    await game.save(state)
    info = game.round_info(state, await _current_sound(session, state))
    return {**info, "result": entry}


@router.get("/{game_id}/result")
async def result(game_id: str) -> dict[str, Any]:
    state = await _state(game_id)
    if not state.finished:
        raise _err(409, "game_not_finished")
    return {
        "mode": state.mode,
        "date": state.date,
        "total": state.total,
        "max": game.MAX_SCORE * len(state.sound_ids),
        "guesses": state.guesses,
        "share_text": game.share_text(state, get_settings().base_url.rstrip("/")),
        "can_submit": state.mode == "daily"
        and not state.submitted
        and state.date == game.today_jst().isoformat(),
    }


@router.post("/{game_id}/ranking", status_code=201)
async def submit_ranking(
    game_id: str,
    body: LeaderboardIn,
    request: Request,
    session: Session,
    _ts: TurnstileResult = Depends(require_turnstile("ranking")),
) -> dict[str, Any]:
    state = await _state(game_id)
    if state.mode != "daily" or not state.finished or state.submitted:
        raise _err(409, "cannot_submit")
    if state.date != game.today_jst().isoformat():
        raise _err(409, "challenge_expired")
    ip_hash = ip_hash_of(request)
    await moderation.ensure_not_banned(session, ip_hash)
    await moderation.check_ngwords(session, body.nickname)
    session.add(
        LeaderboardEntry(date=game.today_jst(), nickname=body.nickname, score=state.total, ip_hash=ip_hash)
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise _err(409, "already_submitted") from exc
    state.submitted = True
    await game.save(state)
    return {"ok": True}
