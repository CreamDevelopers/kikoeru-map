from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from app.db import sessionmaker
from app.models import Sound
from app.services.geo import blur_point, haversine_m, in_japan

from .conftest import ip_headers, noise_audio


@pytest.mark.parametrize(
    ("lat", "lng", "ok"),
    [
        (35.681, 139.767, True),
        (26.212, 127.681, True),
        (45.52, 141.93, True),
        (20.42, 136.08, True),
        (19.9, 140.0, False),
        (46.1, 142.0, False),
        (35.0, 121.9, False),
        (35.0, 154.1, False),
        (40.7, -74.0, False),
    ],
)
def test_in_japan(lat: float, lng: float, ok: bool) -> None:
    assert in_japan(lat, lng) is ok


@pytest.mark.parametrize("precision,meters", [("100m", 100), ("500m", 500)])
def test_blur_moves_within_cell_and_is_deterministic(precision: str, meters: int) -> None:
    lat, lng = 35.6812345, 139.7671234
    blat, blng = blur_point(lat, lng, precision)
    assert (blat, blng) != (round(lat, 6), round(lng, 6))
    assert haversine_m(lat, lng, blat, blng) <= meters * 0.75
    assert blur_point(lat + 0.00001, lng + 0.00001, precision) == (blat, blng)


def test_exact_is_not_blurred() -> None:
    assert blur_point(35.6812345, 139.7671234, "exact") == (35.681235, 139.767123)


def _form(**kw: str) -> dict[str, str]:
    base = {
        "title": "テスト",
        "lat": "35.6812345",
        "lng": "139.7671234",
        "agree": "true",
        "precision": "exact",
        "cf-turnstile-response": "ok",
    }
    base.update(kw)
    return base


async def test_post_outside_japan_rejected(client: httpx.AsyncClient) -> None:
    f = noise_audio("geo", 6, seed=40)
    r = await client.post(
        "/api/sounds",
        data=_form(lat="37.77", lng="-122.42"),
        files={"file": ("a.wav", f.read_bytes(), "audio/wav")},
        headers=ip_headers(),
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "outside_japan"


async def test_post_with_blur_never_stores_exact_coordinates(client: httpx.AsyncClient) -> None:
    f = noise_audio("geo", 6, seed=41)
    r = await client.post(
        "/api/sounds",
        data=_form(precision="500m"),
        files={"file": ("a.wav", f.read_bytes(), "audio/wav")},
        headers=ip_headers(),
    )
    assert r.status_code == 202, r.text
    sid = r.json()["id"]
    async with sessionmaker()() as session:
        s = (await session.execute(select(Sound).where(Sound.id == sid))).scalar_one()
        stored = (s.lat, s.lng)
        wkt = (await session.execute(select(Sound.location.ST_AsText()).where(Sound.id == sid))).scalar_one()
    assert stored == blur_point(35.6812345, 139.7671234, "500m")
    assert "35.6812345" not in wkt and "139.7671234" not in wkt
    assert s.precision == "500m"
