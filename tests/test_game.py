from __future__ import annotations

import json

import httpx
import pytest

from app.services import game

from .conftest import create_sound, ip_headers

PLACES = [
    (43.0642, 141.3469),
    (35.6812, 139.7671),
    (34.7025, 135.4959),
    (33.5902, 130.4207),
    (26.2124, 127.6809),
    (38.2682, 140.8694),
]


def test_score_is_max_when_close_and_decays_exponentially() -> None:
    assert game.score_for_distance(0) == 5000
    assert game.score_for_distance(30) == 5000
    s10 = game.score_for_distance(10_000)
    s100 = game.score_for_distance(100_000)
    s500 = game.score_for_distance(500_000)
    assert 5000 > s10 > s100 > s500 >= 0
    r1 = game.score_for_distance(200_000) / game.score_for_distance(100_000)
    r2 = game.score_for_distance(300_000) / game.score_for_distance(200_000)
    assert r1 == pytest.approx(r2, rel=0.01)
    assert game.score_for_distance(3_000_000) == 0


async def _eligible(n: int = 6, **kw: object) -> list:
    return [
        await create_sound(lat=lat, lng=lng, game_ok=True, title=f"Q{i}", **kw)
        for i, (lat, lng) in enumerate(PLACES[:n])
    ]


async def test_not_enough_sounds(client: httpx.AsyncClient) -> None:
    await _eligible(3)
    r = await client.post("/api/game/start", json={"mode": "random"})
    assert r.status_code == 409


async def test_only_game_ok_and_exact_sounds_are_used(client: httpx.AsyncClient) -> None:
    await _eligible(4)
    await create_sound(game_ok=False)
    await create_sound(game_ok=True, precision="500m")
    r = await client.post("/api/game/start", json={"mode": "random"})
    assert r.status_code == 409


async def test_answer_is_not_leaked_before_guess(client: httpx.AsyncClient) -> None:
    sounds = await _eligible()
    ids = {s.id for s in sounds}
    r = await client.post("/api/game/start", json={"mode": "random"})
    assert r.status_code == 200
    body = r.json()
    raw = json.dumps(body)
    for s in sounds:
        assert s.id not in raw
        assert str(s.lat) not in raw and str(s.lng) not in raw

    def keys(obj: object) -> set[str]:
        if isinstance(obj, dict):
            return set(obj) | {k for v in obj.values() for k in keys(v)}
        return set()

    assert not keys(body) & {"lat", "lng", "id", "title", "answer", "page_url", "sound_ids"}
    assert body["round"] == 1 and body["rounds"] == 5
    gid = body["game_id"]

    assert all(x not in body["audio"]["webm"] for x in ids)
    assert (await client.get(f"/api/game/{gid}")).json() == body
    assert (await client.get(f"/api/game/{gid}/audio/1.webm")).status_code == 404
    assert (await client.get(f"/api/game/{gid}/result")).status_code == 409


async def test_full_game_scoring(client: httpx.AsyncClient) -> None:
    sounds = await _eligible()
    by_id = {s.id: s for s in sounds}
    gid = (await client.post("/api/game/start", json={"mode": "random"})).json()["game_id"]
    state = await game.load(gid)
    assert state is not None
    total = 0
    for i in range(5):
        target = by_id[state.sound_ids[i]]
        guess = {"lat": target.lat, "lng": target.lng} if i == 0 else {"lat": 35.0, "lng": 135.0}
        r = await client.post(f"/api/game/{gid}/guess", json=guess)
        assert r.status_code == 200
        res = r.json()["result"]
        assert res["answer"]["id"] == target.id
        assert res["answer"]["lat"] == target.lat
        if i == 0:
            assert res["score"] == 5000
        total += res["score"]
    assert (await client.post(f"/api/game/{gid}/guess", json={"lat": 35, "lng": 135})).status_code == 409
    result = (await client.get(f"/api/game/{gid}/result")).json()
    assert result["total"] == total
    assert result["max"] == 25000
    assert "みみあて" in result["share_text"]
    assert result["can_submit"] is False


async def test_daily_challenge_is_same_for_everyone_and_ranking(client: httpx.AsyncClient) -> None:
    await _eligible()
    g1 = (await client.post("/api/game/start", json={"mode": "daily"})).json()["game_id"]
    g2 = (await client.post("/api/game/start", json={"mode": "daily"})).json()["game_id"]
    s1, s2 = await game.load(g1), await game.load(g2)
    assert s1 and s2 and s1.sound_ids == s2.sound_ids

    for _ in range(5):
        await client.post(f"/api/game/{g1}/guess", json={"lat": 35.0, "lng": 135.0})
    res = (await client.get(f"/api/game/{g1}/result")).json()
    assert res["can_submit"] is True

    from app.db import sessionmaker
    from app.models import NgWord

    async with sessionmaker()() as session:
        session.add(NgWord(pattern="ばか", is_regex=False))
        await session.commit()
    r = await client.post(
        f"/api/game/{g1}/ranking",
        json={"nickname": "バカ", "cf-turnstile-response": "ok"},
        headers=ip_headers(),
    )
    assert r.status_code == 422
    r = await client.post(
        f"/api/game/{g1}/ranking",
        json={"nickname": "たろう", "cf-turnstile-response": "ok"},
        headers=ip_headers(),
    )
    assert r.status_code == 201
    r = await client.post(
        f"/api/game/{g1}/ranking",
        json={"nickname": "たろう", "cf-turnstile-response": "ok"},
        headers=ip_headers(),
    )
    assert r.status_code == 409
    ranking = (await client.get("/api/game/daily/ranking")).json()
    assert ranking["items"] == [{"nickname": "たろう", "score": res["total"]}]
