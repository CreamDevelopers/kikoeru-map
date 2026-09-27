from __future__ import annotations

import json
import subprocess

import httpx
import numpy as np
import pytest

from app import worker
from app.config import get_settings
from app.db import sessionmaker
from app.models import Sound, SoundStatus
from app.services import audio, geocode, media

from .conftest import AUDIO_DIR, FakeQueue, make_audio, noise_audio
from .helpers import upload, upload_and_process


def ffprobe_json(path: str) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
        check=True,
        capture_output=True,
    )
    return json.loads(out.stdout)


async def test_normal_upload_is_published_with_outputs(
    client: httpx.AsyncClient, fake_queue: FakeQueue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "geocoder_enabled", True)
    src = make_audio(
        "tagged",
        "anoisesrc=d=9:c=pink:r=44100:a=0.3:seed=7",
        9,
        ext="mp3",
        extra=["-metadata", "title=secret-title", "-metadata", "artist=someone", "-c:a", "libmp3lame"],
    )
    sound = await upload_and_process(
        client, fake_queue, src, filename="my-home-recording.mp3", content_type="audio/mpeg"
    )
    assert sound.status == SoundStatus.PUBLISHED
    assert 8.5 <= (sound.duration_sec or 0) <= 9.5
    assert sound.webm_hash and sound.m4a_hash and sound.peaks_hash and sound.spectrogram_hash

    webm = media.file_path(sound.id, sound.webm_hash, "webm")
    m4a = media.file_path(sound.id, sound.m4a_hash, "m4a")
    peaks = json.loads(media.file_path(sound.id, sound.peaks_hash, "json").read_text())
    assert len(peaks["peaks"]) == 400
    assert media.file_path(sound.id, sound.spectrogram_hash, "png").exists()

    w = ffprobe_json(str(webm))
    a = w["streams"][0]
    assert a["codec_name"] == "opus" and int(a["sample_rate"]) == 48000
    m = ffprobe_json(str(m4a))
    assert m["streams"][0]["codec_name"] == "aac" and int(m["streams"][0]["sample_rate"]) == 48000
    for info in (w, m):
        tags = {k.lower(): v for k, v in (info["format"].get("tags") or {}).items()}
        assert "title" not in tags and "artist" not in tags
    assert "my-home-recording" not in str(webm)
    assert webm.name == f"{audio.content_hash(webm)}.webm"
    assert any(j[0] == "geocode_sound" and j[1] == (sound.id,) for j in fake_queue.jobs)

    calls: list[str] = []

    def gsi(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={"results": {"muniCd": "13101", "lv01Nm": "丸の内一丁目"}})

    monkeypatch.setattr(
        geocode, "client_factory", lambda: httpx.AsyncClient(transport=httpx.MockTransport(gsi))
    )
    assert await worker.geocode_sound({"job_try": 1}, sound.id) == "ok"
    async with sessionmaker()() as session:
        s = await session.get(Sound, sound.id)
        assert s is not None
    assert (s.pref_code, s.pref_name, s.city_name) == (13, "東京都", "千代田区")
    assert s.ogp_hash and media.file_path(s.id, s.ogp_hash, "png").exists()
    await geocode.reverse_geocode(s.lat, s.lng)
    assert len(calls) == 1


async def test_media_is_served_with_range_and_cache_headers(
    client: httpx.AsyncClient, fake_queue: FakeQueue
) -> None:
    sound = await upload_and_process(client, fake_queue, noise_audio("range", 7, seed=8))
    url = f"/media/{sound.id}/{sound.webm_hash}.webm"
    full = await client.get(url)
    assert full.status_code == 200
    assert full.headers["content-type"] == "audio/webm"
    assert full.headers["x-content-type-options"] == "nosniff"
    assert "immutable" in full.headers["cache-control"]
    part = await client.get(url, headers={"Range": "bytes=10-109"})
    assert part.status_code == 206
    assert part.headers["content-range"].startswith("bytes 10-109/")
    assert part.content == full.content[10:110]


async def test_too_short_is_rejected(client: httpx.AsyncClient, fake_queue: FakeQueue) -> None:
    sound = await upload_and_process(client, fake_queue, noise_audio("short", 3, seed=9))
    assert sound.status == SoundStatus.REJECTED
    assert sound.reject_reason == "too_short"


async def test_too_long_is_cut_to_60_seconds(client: httpx.AsyncClient, fake_queue: FakeQueue) -> None:
    sound = await upload_and_process(client, fake_queue, noise_audio("long", 75, seed=10))
    assert sound.status == SoundStatus.PUBLISHED
    assert 59.5 <= (sound.duration_sec or 0) <= 60.01
    info = ffprobe_json(str(media.file_path(sound.id, sound.m4a_hash or "", "m4a")))
    assert float(info["format"]["duration"]) <= 60.2


async def test_silent_is_rejected(client: httpx.AsyncClient, fake_queue: FakeQueue) -> None:
    src = make_audio("silent", "anullsrc=r=48000:cl=mono", 10)
    sound = await upload_and_process(client, fake_queue, src)
    assert sound.status == SoundStatus.REJECTED
    assert sound.reject_reason == "silent"


async def test_file_without_audio_stream_is_rejected(
    client: httpx.AsyncClient, fake_queue: FakeQueue
) -> None:
    video = make_audio("video-only", "testsrc=size=64x64:rate=10", 6, ext="mp4", extra=["-c:v", "mpeg4"])
    sound = await upload_and_process(client, fake_queue, video, filename="clip.m4a", content_type="audio/mp4")
    assert sound.status == SoundStatus.REJECTED
    assert sound.reject_reason == "no_audio"


async def test_disguised_extension_is_detected(client: httpx.AsyncClient, fake_queue: FakeQueue) -> None:
    png = make_audio("image", "color=c=red:s=32x32", 1, ext="png", extra=["-frames:v", "1"])
    sound = await upload_and_process(client, fake_queue, png, filename="song.mp3", content_type="audio/mpeg")
    assert sound.status == SoundStatus.REJECTED
    assert sound.reject_reason in ("no_audio", "unreadable", "unsupported_format")

    txt = AUDIO_DIR / "fake.mp3"
    txt.write_text("this is not audio" * 100)
    sound = await upload_and_process(client, fake_queue, txt, content_type="audio/mpeg")
    assert sound.status == SoundStatus.REJECTED
    assert sound.reject_reason == "unreadable"

    wav = noise_audio("disguise-ok", 6, seed=11)
    sound = await upload_and_process(client, fake_queue, wav, filename="sound.mp3", content_type="audio/mpeg")
    assert sound.status == SoundStatus.PUBLISHED


async def test_upload_size_limit(client: httpx.AsyncClient) -> None:
    big = AUDIO_DIR / "big.bin"
    big.write_bytes(b"\0" * (21 * 1024 * 1024))
    r = await upload(client, big)
    assert r.status_code == 413


def test_analysis_helpers() -> None:
    rate = 48000
    silence = np.zeros(rate * 5, dtype=np.float32)
    assert audio.silence_ratio(silence, rate) == 1.0
    tone = (0.3 * np.sin(2 * np.pi * 440 * np.arange(rate * 5) / rate)).astype(np.float32)
    assert audio.silence_ratio(tone, rate) == 0.0
    clipped = np.clip(tone * 10, -1, 1)
    assert audio.clipping_ratio(clipped) > 0.5
    assert audio.clipping_ratio(tone) == 0.0
    peaks = audio.compute_peaks(tone)
    assert len(peaks) == 400 and max(peaks) == 1.0


async def test_clipping_warning(client: httpx.AsyncClient, fake_queue: FakeQueue) -> None:
    src = make_audio("clipped", "anoisesrc=d=8:c=white:r=48000:a=1.0:seed=12,volume=8", 8)
    sound = await upload_and_process(client, fake_queue, src)
    assert sound.status == SoundStatus.PUBLISHED
    assert sound.clipping_warning is True
