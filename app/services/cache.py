from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.services.geo import (
    JAPAN_LAT_MAX,
    JAPAN_LAT_MIN,
    JAPAN_LNG_MAX,
    JAPAN_LNG_MIN,
    lnglat_to_tile,
    tile_to_lnglat,
)
from app.services.redis import get_redis

INDEX_ZOOM = 9
MAX_INDEX_CELLS = 64
WIDE_INDEX = "pinsidx:wide"
GEN_KEY = "pins:gen"
MIN_Z = 3
MAX_Z = 18


@dataclass(frozen=True)
class TileRange:
    z: int
    x0: int
    y0: int
    x1: int
    y1: int

    def bbox(self) -> tuple[float, float, float, float]:
        west, north = tile_to_lnglat(self.x0, self.y0, self.z)
        east, south = tile_to_lnglat(self.x1 + 1, self.y1 + 1, self.z)
        return west, south, east, north

    def key(self, gen: int) -> str:
        return f"pins:{gen}:{self.z}:{self.x0}:{self.y0}:{self.x1}:{self.y1}"


def tile_range_for(z: int, west: float, south: float, east: float, north: float) -> TileRange:
    z = max(MIN_Z, min(MAX_Z, z))
    tz = max(MIN_Z - 1, z - 1)
    west = max(JAPAN_LNG_MIN - 2, min(JAPAN_LNG_MAX + 2, west))
    east = max(JAPAN_LNG_MIN - 2, min(JAPAN_LNG_MAX + 2, east))
    south = max(JAPAN_LAT_MIN - 2, min(JAPAN_LAT_MAX + 2, south))
    north = max(JAPAN_LAT_MIN - 2, min(JAPAN_LAT_MAX + 2, north))
    if east < west:
        west, east = east, west
    if north < south:
        south, north = north, south
    x0, y0 = lnglat_to_tile(west, north, tz)
    x1, y1 = lnglat_to_tile(east, south, tz)
    return TileRange(tz, x0, y0, x1, y1)


def _index_cells(tr: TileRange) -> list[tuple[int, int]]:
    west, south, east, north = tr.bbox()
    x0, y0 = lnglat_to_tile(west, north, INDEX_ZOOM)
    x1, y1 = lnglat_to_tile(east - 1e-9, south + 1e-9, INDEX_ZOOM)
    return [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]


async def current_gen() -> int:
    raw = await get_redis().get(GEN_KEY)
    return int(raw) if raw else 0


def make_etag(body: bytes) -> str:
    return '"' + hashlib.blake2b(body, digest_size=10).hexdigest() + '"'


async def get_cached(tr: TileRange) -> tuple[str, bytes] | None:
    redis = get_redis()
    gen = await current_gen()
    raw = await redis.get(tr.key(gen))
    if not raw:
        return None
    etag, _, body = raw.partition(b"\n")
    return etag.decode(), body


async def store(tr: TileRange, body: bytes, ttl: int) -> str:
    redis = get_redis()
    gen = await current_gen()
    key = tr.key(gen)
    etag = make_etag(body)
    cells = _index_cells(tr)
    idx_keys = [WIDE_INDEX] if len(cells) > MAX_INDEX_CELLS else [f"pinsidx:{cx}:{cy}" for cx, cy in cells]
    pipe = redis.pipeline(transaction=False)
    pipe.set(key, etag.encode() + b"\n" + body, ex=ttl)
    for idx in idx_keys:
        pipe.sadd(idx, key)
        pipe.expire(idx, ttl * 2)
    await pipe.execute()
    return etag


async def invalidate_point(lat: float, lng: float) -> int:
    redis = get_redis()
    cx, cy = lnglat_to_tile(lng, lat, INDEX_ZOOM)
    idx_keys = [f"pinsidx:{cx}:{cy}", WIDE_INDEX]
    pipe = redis.pipeline(transaction=False)
    for idx in idx_keys:
        pipe.smembers(idx)
    members = await pipe.execute()
    keys = set().union(*members)
    pipe = redis.pipeline(transaction=False)
    if keys:
        pipe.delete(*keys)
    pipe.delete(*idx_keys)
    res = await pipe.execute()
    return int(res[0]) if keys else 0


async def invalidate_all() -> None:
    await get_redis().incr(GEN_KEY)
