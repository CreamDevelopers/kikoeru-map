from __future__ import annotations

import httpx
import numpy as np
import pytest
from sqlalchemy import func, select

from app.db import sessionmaker
from app.models import Fingerprint, SoundStatus
from app.services import fingerprint, vad

from .conftest import FakeQueue, create_sound, noise_audio
from .helpers import upload_and_process

RATE = 16000


def speechlike(seconds: float = 8, rate: int = RATE) -> np.ndarray:
    t = np.arange(int(seconds * rate)) / rate
    f0 = 120 + 25 * np.sin(2 * np.pi * 0.7 * t)
    phase = 2 * np.pi * np.cumsum(f0) / rate
    sig = sum((1 / k) * np.sin(k * phase) for k in range(1, 30))
    spec = np.fft.rfft(sig)
    freqs = np.fft.rfftfreq(len(sig), 1 / rate)
    env = sum(np.exp(-((freqs - f) ** 2) / (2 * bw**2)) for f, bw in ((700, 120), (1200, 150), (2600, 200)))
    sig = np.fft.irfft(spec * (0.05 + env), n=len(sig))
    sig = sig * (np.sin(2 * np.pi * 4 * t) > -0.2)
    return (sig / np.max(np.abs(sig)) * 12000).astype(np.int16)


def noise(kind: str, seconds: float = 8) -> np.ndarray:
    rng = np.random.default_rng(3)
    white = rng.standard_normal(int(seconds * RATE))
    if kind == "brown":
        white = np.cumsum(white)
        white -= np.convolve(white, np.ones(400) / 400, mode="same")
    return (white / np.max(np.abs(white)) * 10000).astype(np.int16)


def test_speech_ratio_distinguishes_voice_from_noise_and_tones() -> None:
    assert vad.speech_ratio(np.zeros(RATE * 5, dtype=np.int16)) == 0.0
    t = np.arange(RATE * 8) / RATE
    for name, pcm in [
        ("white", noise("white")),
        ("brown", noise("brown")),
        ("tone", (6000 * np.sin(2 * np.pi * 440 * t)).astype(np.int16)),
        ("hum", (6000 * np.sin(2 * np.pi * 100 * t)).astype(np.int16)),
    ]:
        assert vad.speech_ratio(pcm) < 0.1, name
    assert vad.speech_ratio(speechlike()) > 0.35


async def test_voice_like_upload_is_held_without_mocking(
    client: httpx.AsyncClient, fake_queue: FakeQueue
) -> None:
    import wave

    from .conftest import AUDIO_DIR

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    path = AUDIO_DIR / "speechlike.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(speechlike(10).tobytes())
    sound = await upload_and_process(client, fake_queue, path)
    assert sound.status == SoundStatus.PENDING
    assert sound.hidden_reason == "voice"
    assert (sound.voice_ratio or 0) > 0.35


def test_threshold() -> None:
    assert vad.is_voice_suspected(0.5, 0.35)
    assert not vad.is_voice_suspected(0.2, 0.35)


async def test_voice_suspected_upload_is_held_for_review(
    client: httpx.AsyncClient, fake_queue: FakeQueue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(vad, "speech_ratio", lambda pcm, rate=16000, mode=3: 0.8)
    sound = await upload_and_process(client, fake_queue, noise_audio("voice", 7, seed=20))
    assert sound.status == SoundStatus.PENDING
    assert sound.hidden_reason == "voice"
    assert sound.voice_flag is True
    assert sound.voice_ratio == pytest.approx(0.8)
    r = await client.get(f"/api/sounds/{sound.id}")
    assert r.status_code == 404
    r = await client.get(f"/media/{sound.id}/{sound.webm_hash}.webm")
    assert r.status_code == 404


async def test_low_voice_ratio_is_published(
    client: httpx.AsyncClient, fake_queue: FakeQueue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(vad, "speech_ratio", lambda pcm, rate=16000, mode=3: 0.05)
    sound = await upload_and_process(client, fake_queue, noise_audio("novoice", 7, seed=21))
    assert sound.status == SoundStatus.PUBLISHED
    assert sound.voice_flag is False


def test_bit_error_rate() -> None:
    rng = np.random.default_rng(1)
    a = [int(x) for x in rng.integers(-(2**31), 2**31 - 1, size=200)]
    assert fingerprint.bit_error_rate(a, a) == 0.0
    b = [int(x) for x in rng.integers(-(2**31), 2**31 - 1, size=200)]
    assert fingerprint.bit_error_rate(a, b) > 0.3
    assert fingerprint.bit_error_rate(a, a[5:]) == 0.0


async def test_duplicate_upload_is_rejected(client: httpx.AsyncClient, fake_queue: FakeQueue) -> None:
    src = noise_audio("dup", 12, seed=30)
    first = await upload_and_process(client, fake_queue, src)
    assert first.status == SoundStatus.PUBLISHED
    second = await upload_and_process(client, fake_queue, src)
    assert second.status == SoundStatus.REJECTED
    assert second.reject_reason == "duplicate"

    from .conftest import make_audio

    mp3 = make_audio("dup-mp3", f"amovie={src}", 12, ext="mp3", extra=["-c:a", "libmp3lame", "-b:a", "128k"])
    third = await upload_and_process(client, fake_queue, mp3, filename="x.mp3", content_type="audio/mpeg")
    assert third.status == SoundStatus.REJECTED
    assert third.reject_reason == "duplicate"

    other = await upload_and_process(client, fake_queue, noise_audio("dup", 12, seed=31))
    assert other.status == SoundStatus.PUBLISHED


async def test_duplicate_of_deleted_sound_is_rejected(
    client: httpx.AsyncClient, fake_queue: FakeQueue
) -> None:
    src = noise_audio("dupdel", 10, seed=32)
    first = await upload_and_process(client, fake_queue, src)
    assert first.status == SoundStatus.PUBLISHED
    r = await client.delete(f"/api/sounds/{first.id}", headers={"X-Delete-Token": "wrong"})
    assert r.status_code == 403

    from app import worker
    from app.models import Sound, utcnow

    async with sessionmaker()() as session:
        s = await session.get(Sound, first.id)
        assert s is not None
        s.status = SoundStatus.DELETED
        s.deleted_at = utcnow().replace(year=utcnow().year - 1)
        await session.commit()
    purged = await worker.purge_deleted({})
    assert purged == 1
    async with sessionmaker()() as session:
        assert await session.get(Sound, first.id) is None
        n = (await session.execute(select(func.count()).select_from(Fingerprint))).scalar_one()
        assert n == 1

    again = await upload_and_process(client, fake_queue, src)
    assert again.status == SoundStatus.REJECTED
    assert again.reject_reason == "duplicate"


async def test_rejected_upload_does_not_keep_fingerprint(
    client: httpx.AsyncClient, fake_queue: FakeQueue
) -> None:
    await create_sound()
    await upload_and_process(client, fake_queue, noise_audio("short-fp", 3, seed=33))
    async with sessionmaker()() as session:
        n = (await session.execute(select(func.count()).select_from(Fingerprint))).scalar_one()
    assert n == 0
