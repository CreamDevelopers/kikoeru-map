# webrtcvad だけだと雨・風・雑踏もほぼ全部「声」になるため、声の高さの周期性とスペクトル平坦度でも絞り、
# 音節くらいの長さ（90〜750ms）で途切れる区間だけを数える
from __future__ import annotations

import numpy as np
import webrtcvad

FRAME_MS = 30
PITCH_MIN_HZ = 50
PITCH_MAX_HZ = 400
HARMONIC_MIN = 0.45
FLATNESS_MAX = 0.3
RUN_MIN_FRAMES = 3
RUN_MAX_FRAMES = 25


def _frames(pcm16: np.ndarray, rate: int) -> np.ndarray:
    n = rate * FRAME_MS // 1000
    count = len(pcm16) // n
    return pcm16[: count * n].reshape(count, n)


def _harmonicity(frames: np.ndarray, rate: int) -> np.ndarray:
    x = frames.astype(np.float64)
    nxt = np.vstack([x[1:], x[-1:]])
    win = np.hstack([x, nxt])
    # プリエンファシスで、低域に偏ったノイズが周期的に見えるのを防ぐ
    win = np.hstack([win[:, :1], win[:, 1:] - 0.97 * win[:, :-1]])
    win -= win.mean(axis=1, keepdims=True)
    n = win.shape[1]
    size = 1 << (2 * n - 1).bit_length()
    spec = np.fft.rfft(win, size, axis=1)
    ac = np.fft.irfft(np.abs(spec) ** 2, size, axis=1)[:, :n]
    lo = rate // PITCH_MAX_HZ
    hi = min(n - 1, rate // PITCH_MIN_HZ)
    lags = np.arange(lo, hi + 1)
    norm = ac[:, lo : hi + 1] / (ac[:, :1] + 1e-9) * (n / (n - lags))
    return np.asarray(norm.max(axis=1))


def _flatness(frames: np.ndarray, rate: int) -> np.ndarray:
    x = frames.astype(np.float64) * np.hanning(frames.shape[1])
    p = np.abs(np.fft.rfft(x, axis=1)) ** 2 + 1e-12
    freqs = np.fft.rfftfreq(frames.shape[1], 1 / rate)
    band = p[:, (freqs >= 300) & (freqs <= 3400)]
    return np.asarray(np.exp(np.mean(np.log(band), axis=1)) / np.mean(band, axis=1))


def speech_ratio(pcm16: np.ndarray, rate: int = 16_000, mode: int = 3) -> float:
    frames = _frames(pcm16, rate)
    if len(frames) == 0:
        return 0.0
    vad = webrtcvad.Vad(mode)
    is_speech = np.array([vad.is_speech(f.astype("<i2").tobytes(), rate) for f in frames])
    voiced = (
        is_speech & (_harmonicity(frames, rate) >= HARMONIC_MIN) & (_flatness(frames, rate) <= FLATNESS_MAX)
    )
    counted = 0
    run = 0
    for flag in [*voiced.tolist(), False]:
        if flag:
            run += 1
        else:
            if RUN_MIN_FRAMES <= run <= RUN_MAX_FRAMES:
                counted += run
            run = 0
    return counted / len(frames)


def is_voice_suspected(ratio: float, threshold: float) -> bool:
    return ratio >= threshold
