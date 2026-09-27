from __future__ import annotations

import datetime as dt
import logging
import math
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

import orjson
from arq import Retry, cron, func
from sqlalchemy import delete, select, text

from app.config import get_settings
from app.db import dispose_engine, init_engine, sessionmaker
from app.models import Fingerprint, ProcessingJob, Sound, SoundStatus, utcnow
from app.services import audio, events, fingerprint, geocode, media, metrics, ogp, runtime_settings, vad
from app.services import sounds as sound_svc
from app.services.audio import AudioRejected
from app.services.logging import setup_logging
from app.services.queue import redis_settings, set_pool
from app.services.redis import close_redis, get_redis

log = logging.getLogger("kikoeru.worker")

MAX_TRIES = 3
FP_LOCK_ID = 7_441_001


async def _fail_status(sound_id: str, state: str, reason: str) -> None:
    await events.set_status(sound_id, {"state": state, "reason": reason})


async def process_sound(ctx: dict[str, Any], job_id: str) -> str:
    job_try = int(ctx.get("job_try", 1))
    settings = get_settings()
    async with sessionmaker()() as session:
        job = await session.get(ProcessingJob, job_id)
        if job is None:
            return "missing"
        sound = await session.get(Sound, job.sound_id)
        if sound is None or sound.status != SoundStatus.PROCESSING:
            job.status = "skipped"
            await session.commit()
            return "skipped"
        job.status = "running"
        job.attempts = job_try
        await session.commit()
        await events.set_status(sound.id, {"state": "processing", "attempt": job_try})
        src = Path(job.upload_path or "")
        # rollback 後は ORM の属性が失効するので、ID は先に取り出しておく
        sound_id = sound.id
        try:
            result = await _process(session, sound, src)
        except AudioRejected as exc:
            await session.rollback()
            sound = await session.get(Sound, sound_id, populate_existing=True)
            job = await session.get(ProcessingJob, job_id, populate_existing=True)
            assert sound is not None and job is not None
            sound.status = SoundStatus.REJECTED
            sound.reject_reason = exc.code
            job.status = "rejected"
            job.error = exc.code
            await session.execute(delete(Fingerprint).where(Fingerprint.sound_id == sound.id))
            await session.commit()
            src.unlink(missing_ok=True)
            await _fail_status(sound.id, "rejected", exc.code)
            await metrics.record_job("process_sound", "rejected")
            return f"rejected:{exc.code}"
        except Exception as exc:
            log.exception("process_sound failed", extra={"job_id": job_id, "try": job_try})
            await session.rollback()
            sound = await session.get(Sound, sound_id, populate_existing=True)
            job = await session.get(ProcessingJob, job_id, populate_existing=True)
            assert sound is not None and job is not None
            job.error = f"{type(exc).__name__}: {exc}"[:1000]
            if job_try < MAX_TRIES:
                job.status = "retrying"
                await session.commit()
                await events.set_status(
                    sound.id, {"state": "processing", "attempt": job_try, "retrying": True}
                )
                await metrics.record_job("process_sound", "retry")
                raise Retry(defer=job_try * 10) from exc
            job.status = "failed"
            sound.status = SoundStatus.FAILED
            sound.reject_reason = "processing_failed"
            await session.execute(delete(Fingerprint).where(Fingerprint.sound_id == sound.id))
            await session.commit()
            await _fail_status(sound.id, "failed", "processing_failed")
            await metrics.record_job("process_sound", "failed")
            return "failed"

        job.status = "done"
        job.error = None
        await session.commit()
        src.unlink(missing_ok=True)
        payload: dict[str, Any] = {
            "state": result,
            "warnings": ["clipping"] if sound.clipping_warning else [],
        }
        await events.set_status(sound.id, payload)
        if result == SoundStatus.PUBLISHED:
            await sound_svc.after_visibility_change(sound, announce=True)
        await metrics.record_job("process_sound", "done")
    if settings.geocoder_enabled:
        pool = ctx.get("redis")
        if pool is not None:
            await pool.enqueue_job("geocode_sound", sound.id, _job_id=f"geo:{sound.id}")
    return result


async def _process(session: Any, sound: Sound, src: Path) -> str:
    settings = get_settings()
    rs = await runtime_settings.load()
    if not src.exists():
        raise AudioRejected("unreadable")
    if src.stat().st_size > settings.max_upload_bytes:
        raise AudioRejected("too_large")
    await audio.probe(src)
    max_dur = float(rs.max_duration_sec)

    samples = await audio.decode_pcm(src, audio.ANALYSIS_RATE, max_dur)
    analysis = audio.analyze(samples, audio.ANALYSIS_RATE)
    if analysis.duration_sec < settings.min_duration_sec:
        raise AudioRejected("too_short")
    if analysis.silence_ratio >= settings.silence_reject_ratio:
        raise AudioRejected("silent")

    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=settings.upload_dir, prefix="work-") as tmpdir:
        tmp = Path(tmpdir)
        normalized = tmp / "norm.wav"
        lufs = await audio.normalize(src, normalized, max_dur, settings.target_lufs)

        # 同時に処理された同じ音が両方通らないよう、重複チェックと登録を直列化する
        fp = await fingerprint.compute(normalized)
        await session.execute(delete(Fingerprint).where(Fingerprint.sound_id == sound.id))
        await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": FP_LOCK_ID})
        dup = await fingerprint.find_duplicate(session, fp)
        if dup:
            raise AudioRejected("duplicate")
        session.add(fingerprint.to_model(sound.id, fp))
        await session.commit()

        pcm16 = await audio.decode_pcm(src, audio.VAD_RATE, max_dur, as_int16=True)
        voice = vad.speech_ratio(pcm16)

        webm, m4a = tmp / "a.webm", tmp / "a.m4a"
        await audio.encode_outputs(normalized, webm, m4a)
        norm_samples = await audio.decode_pcm(normalized, audio.ANALYSIS_RATE, max_dur)
        peaks_file = tmp / "peaks.json"
        peaks_file.write_bytes(
            orjson.dumps({"peaks": audio.compute_peaks(norm_samples), "duration": len(norm_samples) / 48000})
        )
        spec = tmp / "spec.png"
        await audio.spectrogram(normalized, spec)

        hashes = {}
        for path, ext in ((webm, "webm"), (m4a, "m4a"), (peaks_file, "json"), (spec, "png")):
            h = audio.content_hash(path)
            media.store(path, sound.id, h, ext)
            hashes[ext] = h

    sound.webm_hash = hashes["webm"]
    sound.m4a_hash = hashes["m4a"]
    sound.peaks_hash = hashes["json"]
    sound.spectrogram_hash = hashes["png"]
    sound.duration_sec = round(min(analysis.duration_sec, max_dur), 2)
    sound.silence_ratio = round(analysis.silence_ratio, 4)
    sound.clipping_ratio = round(analysis.clipping_ratio, 5)
    sound.clipping_warning = analysis.clipping_ratio >= settings.clipping_warn_ratio
    sound.voice_ratio = round(voice, 4)
    sound.voice_flag = vad.is_voice_suspected(voice, rs.vad_threshold)
    sound.loudness_lufs = None if math.isnan(lufs) else round(lufs, 2)

    if sound.voice_flag:
        sound.status = SoundStatus.PENDING
        sound.hidden_reason = "voice"
    elif rs.preapproval:
        sound.status = SoundStatus.PENDING
        sound.hidden_reason = "preapproval"
    else:
        await sound_svc.publish(session, sound)
    await session.commit()
    return sound.status


async def geocode_sound(ctx: dict[str, Any], sound_id: str) -> str:
    job_try = int(ctx.get("job_try", 1))
    async with sessionmaker()() as session:
        sound = await session.get(Sound, sound_id)
        if sound is None:
            return "missing"
        try:
            addr = await geocode.reverse_geocode(sound.lat, sound.lng)
            sound.muni_code = addr.muni_code
            sound.pref_code = addr.pref_code
            sound.pref_name = addr.pref_name
            sound.city_name = addr.city_name
        except Exception as exc:
            log.warning("geocode failed", extra={"sound_id": sound_id, "error": str(exc), "try": job_try})
            if job_try < MAX_TRIES:
                await metrics.record_job("geocode_sound", "retry")
                raise Retry(defer=job_try * 30) from exc
            await metrics.record_job("geocode_sound", "failed")
        await _render_ogp(sound)
        await session.commit()
    await metrics.record_job("geocode_sound", "done")
    return "ok"


async def _render_ogp(sound: Sound) -> None:
    spec = media.file_path(sound.id, sound.spectrogram_hash, "png") if sound.spectrogram_hash else None
    place = " ".join(p for p in (sound.pref_name, sound.city_name) if p)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False, dir=get_settings().upload_dir) as tmpf:
        tmp = Path(tmpf.name)
    try:
        ogp.render(spec, sound.title, place, tmp)
        h = audio.content_hash(tmp)
        media.store(tmp, sound.id, "ogp-" + h[:11], "png")
        sound.ogp_hash = "ogp-" + h[:11]
    finally:
        tmp.unlink(missing_ok=True)


async def render_ogp(ctx: dict[str, Any], sound_id: str) -> str:
    async with sessionmaker()() as session:
        sound = await session.get(Sound, sound_id)
        if sound is None:
            return "missing"
        await _render_ogp(sound)
        await session.commit()
    return "ok"


async def flush_plays(ctx: dict[str, Any]) -> int:
    redis = get_redis()
    tmp_key = f"plays:flushing:{int(time.time() * 1000)}"
    try:
        await redis.rename("plays:pending", tmp_key)
    except Exception:
        return 0
    raw = await redis.hgetall(tmp_key)
    counts = {k.decode(): int(v) for k, v in raw.items()}
    async with sessionmaker()() as session:
        total = await sound_svc.flush_play_counts(session, counts)
        await session.commit()
    await redis.delete(tmp_key)
    await metrics.record_job("flush_plays", "done")
    return total


async def purge_deleted(ctx: dict[str, Any]) -> int:
    # フィンガープリントは重複検出のため残す
    settings = get_settings()
    cutoff = utcnow() - dt.timedelta(days=settings.purge_after_days)
    async with sessionmaker()() as session:
        stmt = select(Sound.id).where(
            ((Sound.status == SoundStatus.DELETED) & (Sound.deleted_at < cutoff))
            | (Sound.status.in_([SoundStatus.REJECTED, SoundStatus.FAILED]) & (Sound.updated_at < cutoff))
        )
        ids = list((await session.execute(stmt)).scalars().all())
        for sid in ids:
            media.remove_all(sid)
        if ids:
            await session.execute(delete(Sound).where(Sound.id.in_(ids)))
        await session.commit()
    await metrics.record_job("purge_deleted", "done")
    log.info("purged sounds", extra={"count": len(ids)})
    return len(ids)


async def cleanup_uploads(ctx: dict[str, Any]) -> int:
    # 失敗ジョブを再実行できるよう 7 日間は残す
    settings = get_settings()
    cutoff = time.time() - 7 * 86400
    removed = 0
    if not settings.upload_dir.exists():
        return 0
    for p in settings.upload_dir.iterdir():
        try:
            if p.stat().st_mtime < cutoff:
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    p.unlink(missing_ok=True)
                removed += 1
        except FileNotFoundError:
            continue
    return removed


async def startup(ctx: dict[str, Any]) -> None:
    setup_logging()
    init_engine()
    set_pool(ctx["redis"])
    get_settings().media_dir.mkdir(parents=True, exist_ok=True)
    get_settings().upload_dir.mkdir(parents=True, exist_ok=True)


async def shutdown(ctx: dict[str, Any]) -> None:
    await dispose_engine()
    await close_redis()


class WorkerSettings:
    functions = [  # noqa: RUF012  # arq の設定クラスの規約
        func(process_sound, max_tries=MAX_TRIES, timeout=600),
        func(geocode_sound, max_tries=MAX_TRIES, timeout=60),
        func(render_ogp, max_tries=MAX_TRIES, timeout=60),
    ]
    cron_jobs = [  # noqa: RUF012
        cron(flush_plays, second={0, 30}, run_at_startup=False),
        cron(purge_deleted, hour={18}, minute={0}),  # 03:00 JST
        cron(cleanup_uploads, minute={17}),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = redis_settings()
    max_jobs = 4
    health_check_interval = 30
    job_timeout = 600
