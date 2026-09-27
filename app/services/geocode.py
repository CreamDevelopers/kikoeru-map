from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import httpx
import orjson

from app.config import get_settings
from app.services.geo import PREF_BY_CODE
from app.services.redis import get_redis

log = logging.getLogger(__name__)

CACHE_TTL = 60 * 60 * 24 * 30


@dataclass
class Address:
    muni_code: str | None
    pref_code: int | None
    pref_name: str | None
    city_name: str | None


@lru_cache
def muni_table() -> dict[str, list]:
    path = Path(__file__).resolve().parent.parent / "data" / "muni.json"
    data: dict[str, list] = json.loads(path.read_text(encoding="utf-8"))
    return data


def lookup_muni(muni_cd: str) -> Address:
    try:
        key = str(int(muni_cd))
    except ValueError:
        return Address(None, None, None, None)
    row = muni_table().get(key)
    if row:
        return Address(muni_cd, int(row[0]), str(row[1]), str(row[2]))
    pref_code = int(key[:-3]) if len(key) > 3 else None
    pref = PREF_BY_CODE.get(pref_code or 0)
    return Address(muni_cd, pref_code, pref.ja if pref else None, None)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(10.0), headers={"User-Agent": "kikoeru-chizu/1.0"})


client_factory = _client


async def reverse_geocode(lat: float, lng: float) -> Address:
    redis = get_redis()
    key = f"geo:{lat:.4f}:{lng:.4f}"
    cached = await redis.get(key)
    if cached:
        d = orjson.loads(cached)
        return Address(**d)
    async with client_factory() as client:
        resp = await client.get(
            get_settings().geocoder_url, params={"lat": f"{lat:.6f}", "lon": f"{lng:.6f}"}
        )
        resp.raise_for_status()
        payload = resp.json() if resp.content else {}
    results = (payload or {}).get("results") or {}
    muni_cd = str(results.get("muniCd") or "")
    addr = lookup_muni(muni_cd) if muni_cd else Address(None, None, None, None)
    await redis.set(key, orjson.dumps(addr.__dict__), ex=CACHE_TTL)
    return addr
