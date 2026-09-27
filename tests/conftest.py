from __future__ import annotations

import asyncio
import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

TEST_DB = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://kikoeru:kikoeru@localhost:5432/kikoeru_test"
)
TEST_REDIS = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")
os.environ["DATABASE_URL"] = TEST_DB
os.environ["REDIS_URL"] = TEST_REDIS
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("MEDIA_DIR", "/tmp/kikoeru-test/media")
os.environ.setdefault("UPLOAD_DIR", "/tmp/kikoeru-test/uploads")
os.environ["GEOCODER_ENABLED"] = "false"
os.environ["BASE_URL"] = "https://testserver"
os.environ.pop("PROMETHEUS_MULTIPROC_DIR", None)

from asgi_lifespan import LifespanManager  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app import db as app_db  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.services import queue, turnstile  # noqa: E402
from app.services.redis import get_redis  # noqa: E402

TABLES = [
    "audit_logs",
    "reports",
    "reporter_trust",
    "bans",
    "ng_words",
    "course_items",
    "courses",
    "processing_jobs",
    "fingerprints",
    "leaderboard",
    "daily_challenges",
    "daily_stats",
    "settings",
    "sounds",
    "admin_users",
]


async def _ensure_database() -> None:
    import asyncpg

    url = TEST_DB.replace("postgresql+asyncpg://", "postgresql://")
    base, dbname = url.rsplit("/", 1)
    conn = await asyncpg.connect(base + "/postgres")
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", dbname)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{dbname}"')
    finally:
        await conn.close()


def _migrate() -> None:
    from alembic.config import Config

    from alembic import command

    cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    cfg.attributes["url"] = TEST_DB
    cfg.attributes["skip_logging"] = True
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session", autouse=True)
async def database() -> AsyncIterator[None]:
    await _ensure_database()
    # env.py が asyncio.run を使うので別スレッドで実行する
    await asyncio.to_thread(_migrate)
    app_db.init_engine(TEST_DB)
    yield
    await app_db.dispose_engine()


class FakeQueue:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    async def enqueue_job(self, function: str, *args: Any, **kwargs: Any) -> None:
        self.jobs.append((function, args, kwargs))


def _turnstile_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"success": True, "hostname": "testserver", "action": "x"})


@pytest.fixture(autouse=True)
async def clean_state(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[FakeQueue]:
    async with app_db.get_engine().begin() as conn:
        await conn.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))
    await get_redis().flushdb()
    fq = FakeQueue()
    queue.set_pool(fq)
    monkeypatch.setattr(
        turnstile, "client_factory", lambda: httpx.AsyncClient(transport=httpx.MockTransport(_turnstile_ok))
    )
    for d in (get_settings().media_dir, get_settings().upload_dir):
        d.mkdir(parents=True, exist_ok=True)
    yield fq
    queue.set_pool(None)


@pytest.fixture
def fake_queue(clean_state: FakeQueue) -> FakeQueue:
    return clean_state


@pytest.fixture(scope="session")
async def app() -> AsyncIterator[Any]:
    from app.main import create_app

    application = create_app()
    async with LifespanManager(application):
        yield application


@pytest.fixture
async def client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as c:
        yield c


def ip_headers(ip: str = "203.0.113.10") -> dict[str, str]:
    return {"CF-Connecting-IP": ip}


AUDIO_DIR = Path("/tmp/kikoeru-test/fixtures")


def make_audio(
    name: str, lavfi: str, seconds: float, *, ext: str = "wav", extra: list[str] | None = None
) -> Path:
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    path = AUDIO_DIR / f"{name}.{ext}"
    if path.exists():
        return path
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", lavfi, "-t", str(seconds)]
    cmd += extra or []
    cmd.append(str(path))
    subprocess.run(cmd, check=True)
    return path


def noise_audio(name: str, seconds: float = 8, seed: int = 1, *, ext: str = "wav") -> Path:
    lavfi = (
        f"anoisesrc=d={seconds}:c=pink:r=48000:a=0.25:seed={seed},"
        f"volume='0.6+0.4*sin(2*PI*{0.3 + seed * 0.07}*t)':eval=frame"
    )
    return make_audio(f"{name}-{seed}-{seconds}", lavfi, seconds, ext=ext)


async def create_sound(**overrides: Any) -> Any:
    from app.models import Sound, SoundStatus, utcnow
    from app.services import sounds as sound_svc
    from app.services.security import new_sound_id, sha256_hex

    lat = overrides.pop("lat", 35.681)
    lng = overrides.pop("lng", 139.767)
    data: dict[str, Any] = {
        "id": new_sound_id(),
        "status": SoundStatus.PUBLISHED,
        "title": "テストの音",
        "comment": "",
        "tags": ["city"],
        "license": "cc-by",
        "location": sound_svc.point(lat, lng),
        "lat": lat,
        "lng": lng,
        "precision": "exact",
        "delete_token_hash": sha256_hex("token"),
        "ip_hash": "0" * 64,
        "duration_sec": 10.0,
        "webm_hash": "a" * 16,
        "m4a_hash": "b" * 16,
        "peaks_hash": "c" * 16,
        "published_at": utcnow(),
        "time_of_day": "noon",
        "season": "spring",
    }
    data.update(overrides)
    async with app_db.sessionmaker()() as session:
        sound = Sound(**data)
        session.add(sound)
        await session.commit()
        return sound


def unique_ip() -> str:
    n = uuid.uuid4().int
    return f"198.51.{(n >> 8) % 250}.{n % 250 + 1}"
