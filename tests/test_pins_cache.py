from __future__ import annotations

import httpx

from app.services import cache

from .conftest import FakeQueue, create_sound, ip_headers, noise_audio
from .helpers import upload_and_process

TOKYO_BBOX = "139.6,35.6,139.9,35.8"


async def get_pins(
    client: httpx.AsyncClient, bbox: str = TOKYO_BBOX, z: int = 12, **headers: str
) -> httpx.Response:
    return await client.get(f"/api/pins?z={z}&bbox={bbox}", headers={**ip_headers("203.0.113.77"), **headers})


def ids(r: httpx.Response) -> set[str]:
    return {p[0] for p in r.json()["p"]}


async def test_pins_minimal_payload_and_cache_hit(client: httpx.AsyncClient) -> None:
    s = await create_sound(lat=35.68, lng=139.76, tags=["water"], nearby_count=2)
    r1 = await get_pins(client)
    assert r1.status_code == 200
    assert r1.headers["x-cache"] == "MISS"
    body = r1.json()
    assert body["f"] == ["id", "lat", "lng", "tag", "flags"]
    assert body["p"] == [[s.id, 35.68, 139.76, 4, 1]]
    r2 = await get_pins(client)
    assert r2.headers["x-cache"] == "HIT"
    assert r2.content == r1.content


async def test_etag_304(client: httpx.AsyncClient) -> None:
    await create_sound(lat=35.68, lng=139.76)
    r1 = await get_pins(client)
    etag = r1.headers["etag"]
    r2 = await get_pins(client, **{"If-None-Match": etag})
    assert r2.status_code == 304
    assert r2.content == b""


async def test_compression(client: httpx.AsyncClient) -> None:
    for i in range(60):
        await create_sound(lat=35.65 + i * 0.001, lng=139.7 + i * 0.001)
    r = await get_pins(client, **{"Accept-Encoding": "br"})
    assert r.headers.get("content-encoding") == "br"
    r = await get_pins(client, **{"Accept-Encoding": "gzip"})
    assert r.headers.get("content-encoding") == "gzip"


async def test_bbox_filters_by_location(client: httpx.AsyncClient) -> None:
    tokyo = await create_sound(lat=35.68, lng=139.76)
    osaka = await create_sound(lat=34.70, lng=135.50)
    hidden = await create_sound(lat=35.681, lng=139.761, status="hidden")
    got = ids(await get_pins(client))
    assert tokyo.id in got
    assert osaka.id not in got
    assert hidden.id not in got


async def test_new_post_invalidates_only_relevant_keys(
    client: httpx.AsyncClient, fake_queue: FakeQueue
) -> None:
    await create_sound(lat=35.68, lng=139.76)
    osaka_bbox = "135.4,34.6,135.6,34.8"
    await get_pins(client)
    await get_pins(client, bbox=osaka_bbox)
    assert (await get_pins(client)).headers["x-cache"] == "HIT"
    assert (await get_pins(client, bbox=osaka_bbox)).headers["x-cache"] == "HIT"

    sound = await upload_and_process(
        client, fake_queue, noise_audio("cache", 7, seed=50), lat="35.70", lng="139.80"
    )
    assert sound.status == "published"
    r = await get_pins(client)
    assert r.headers["x-cache"] == "MISS"
    assert sound.id in ids(r)
    assert (await get_pins(client, bbox=osaka_bbox)).headers["x-cache"] == "HIT"


async def test_delete_invalidates_cache(client: httpx.AsyncClient, fake_queue: FakeQueue) -> None:
    sound = await upload_and_process(
        client, fake_queue, noise_audio("cache-del", 7, seed=51), lat="35.70", lng="139.80"
    )
    r = await get_pins(client)
    assert sound.id in ids(r)
    assert (await get_pins(client)).headers["x-cache"] == "HIT"

    from app.db import sessionmaker
    from app.models import Sound
    from app.services.security import sha256_hex

    async with sessionmaker()() as session:
        s = await session.get(Sound, sound.id)
        assert s is not None
        s.delete_token_hash = sha256_hex("my-token")
        await session.commit()
    r = await client.delete(f"/api/sounds/{sound.id}", headers={"X-Delete-Token": "my-token"})
    assert r.status_code == 200
    r = await get_pins(client)
    assert r.headers["x-cache"] == "MISS"
    assert sound.id not in ids(r)


async def test_tile_range_rounding() -> None:
    a = cache.tile_range_for(12, 139.70, 35.60, 139.80, 35.70)
    b = cache.tile_range_for(12, 139.701, 35.601, 139.799, 35.699)
    assert a == b
    w, s, e, n = a.bbox()
    assert w <= 139.70 and s <= 35.60 and e >= 139.80 and n >= 35.70
