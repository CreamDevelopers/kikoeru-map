from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

ANALYSIS_RATE = 48_000
VAD_RATE = 16_000
PEAK_POINTS = 400

ALLOWED_FORMATS = {
    "wav",
    "mp3",
    "ogg",
    "matroska,webm",
    "mov,mp4,m4a,3gp,3g2,mj2",
    "flac",
    "aac",
    "aiff",
    "caf",
    "w64",
}


class AudioRejected(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


class AudioToolError(Exception):
    pass


@dataclass
class ProbeInfo:
    format_name: str
    duration: float | None
    codec: str
    channels: int
    sample_rate: int


@dataclass
class AnalysisResult:
    duration_sec: float
    silence_ratio: float
    clipping_ratio: float


async def run(
    cmd: list[str],
    *,
    timeout: float = 120.0,  # noqa: ASYNC109
    stdin: bytes | None = None,
) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin), timeout=timeout)
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise AudioToolError(f"{cmd[0]} timed out") from exc
    return proc.returncode or 0, out, err


async def probe(path: Path) -> ProbeInfo:
    code, out, _err = await run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        timeout=30,
    )
    if code != 0:
        raise AudioRejected("unreadable")
    try:
        data = json.loads(out or b"{}")
    except json.JSONDecodeError as exc:
        raise AudioRejected("unreadable") from exc
    fmt = (data.get("format") or {}).get("format_name", "")
    streams = [s for s in data.get("streams") or [] if s.get("codec_type") == "audio"]
    if not streams:
        raise AudioRejected("no_audio")
    if fmt not in ALLOWED_FORMATS:
        raise AudioRejected("unsupported_format")
    s = streams[0]
    dur_raw = (data.get("format") or {}).get("duration") or s.get("duration")
    try:
        duration = float(dur_raw) if dur_raw not in (None, "N/A") else None
    except ValueError:
        duration = None
    return ProbeInfo(
        format_name=fmt,
        duration=duration,
        codec=str(s.get("codec_name", "")),
        channels=int(s.get("channels") or 1),
        sample_rate=int(s.get("sample_rate") or 0),
    )


async def decode_pcm(path: Path, rate: int, max_seconds: float, *, as_int16: bool = False) -> np.ndarray:
    fmt = "s16le" if as_int16 else "f32le"
    code, out, err = await run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-t",
            f"{max_seconds:.3f}",
            "-ac",
            "1",
            "-ar",
            str(rate),
            "-f",
            fmt,
            "-",
        ],
        timeout=120,
    )
    if code != 0 or not out:
        log.info("decode failed", extra={"stderr": err.decode(errors="replace")[-500:]})
        raise AudioRejected("unreadable")
    dtype = np.int16 if as_int16 else np.float32
    usable = len(out) - (len(out) % np.dtype(dtype).itemsize)
    return np.frombuffer(out[:usable], dtype=dtype)


def silence_ratio(
    samples: np.ndarray, rate: int, *, threshold_db: float = -55.0, frame_ms: int = 50
) -> float:
    frame = int(rate * frame_ms / 1000)
    n = len(samples) // frame
    if n == 0:
        return 1.0
    frames = samples[: n * frame].astype(np.float64).reshape(n, frame)
    rms = np.sqrt(np.mean(frames**2, axis=1))
    db = 20 * np.log10(np.maximum(rms, 1e-10))
    return float(np.mean(db < threshold_db))


def clipping_ratio(samples: np.ndarray, *, threshold: float = 0.995) -> float:
    if len(samples) == 0:
        return 0.0
    return float(np.mean(np.abs(samples) >= threshold))


def compute_peaks(samples: np.ndarray, points: int = PEAK_POINTS) -> list[float]:
    if len(samples) == 0:
        return [0.0] * points
    edges = np.linspace(0, len(samples), points + 1).astype(np.int64)
    absd = np.abs(samples)
    peaks = []
    for i in range(points):
        a, b = edges[i], max(edges[i + 1], edges[i] + 1)
        peaks.append(float(absd[a:b].max()) if a < len(absd) else 0.0)
    top = max(peaks) or 1.0
    return [round(p / top, 3) for p in peaks]


def analyze(samples: np.ndarray, rate: int) -> AnalysisResult:
    return AnalysisResult(
        duration_sec=len(samples) / rate,
        silence_ratio=silence_ratio(samples, rate),
        clipping_ratio=clipping_ratio(samples),
    )


_LOUDNORM_JSON = re.compile(r"\{[^{}]*\"input_i\"[^{}]*\}", re.S)


async def loudnorm_measure(path: Path, max_seconds: float, target: float) -> dict[str, str]:
    code, _out, err = await run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-t",
            f"{max_seconds:.3f}",
            "-af",
            f"loudnorm=I={target}:TP=-1.5:LRA=11:print_format=json",
            "-f",
            "null",
            "-",
        ],
        timeout=180,
    )
    text = err.decode(errors="replace")
    m = _LOUDNORM_JSON.search(text)
    if code != 0 or not m:
        raise AudioToolError("loudnorm measure failed")
    data: dict[str, str] = json.loads(m.group(0))
    return data


async def normalize(src: Path, dst: Path, max_seconds: float, target: float) -> float:
    m = await loudnorm_measure(src, max_seconds, target)

    def val(key: str, default: float, lo: float, hi: float) -> str:
        # 極端に大きい音では測定値が loudnorm の受け付ける範囲を超えるので丸める
        try:
            v = float(m.get(key, default))
        except ValueError:
            v = default
        if v != v or v in (float("inf"), float("-inf")):
            v = default
        return f"{min(hi, max(lo, v)):.2f}"

    filt = (
        f"loudnorm=I={target}:TP=-1.5:LRA=11"
        f":measured_I={val('input_i', -70, -99, 0)}:measured_TP={val('input_tp', -70, -99, 99)}"
        f":measured_LRA={val('input_lra', 0, 0, 99)}:measured_thresh={val('input_thresh', -80, -99, 0)}"
        f":offset={val('target_offset', 0, -99, 99)}:linear=true,aresample=48000"
    )
    code, _o, err = await run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-i",
            str(src),
            "-map",
            "0:a:0",
            "-t",
            f"{max_seconds:.3f}",
            "-af",
            filt,
            "-ac",
            "2" if await _channels(src) >= 2 else "1",
            "-ar",
            "48000",
            "-map_metadata",
            "-1",
            "-c:a",
            "pcm_s16le",
            str(dst),
        ],
        timeout=180,
    )
    if code != 0:
        raise AudioToolError("normalize failed: " + err.decode(errors="replace")[-300:])
    try:
        return float(m.get("input_i", "nan"))
    except ValueError:
        return float("nan")


async def _channels(path: Path) -> int:
    try:
        return (await probe(path)).channels
    except AudioRejected:
        return 1


async def encode_outputs(normalized: Path, webm: Path, m4a: Path) -> None:
    jobs = [
        [
            "ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(normalized),
            "-map_metadata", "-1", "-ar", "48000",
            "-c:a", "libopus", "-b:a", "96k", "-vbr", "on", "-f", "webm", str(webm),
        ],
        [
            "ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(normalized),
            "-map_metadata", "-1", "-ar", "48000",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", "-f", "mp4", str(m4a),
        ],
    ]  # fmt: skip
    results = await asyncio.gather(*(run(cmd, timeout=180) for cmd in jobs))
    for code, _o, err in results:
        if code != 0:
            raise AudioToolError("encode failed: " + err.decode(errors="replace")[-300:])


async def spectrogram(normalized: Path, dst: Path, size: str = "1200x300") -> None:
    code, _o, err = await run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-i",
            str(normalized),
            "-lavfi",
            f"showspectrumpic=s={size}:legend=0:mode=combined:color=intensity:scale=log:fscale=log",
            "-frames:v",
            "1",
            str(dst),
        ],
        timeout=120,
    )
    if code != 0:
        raise AudioToolError("spectrogram failed: " + err.decode(errors="replace")[-300:])


def content_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def bytes_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]
