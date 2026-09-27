from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Fingerprint
from app.services.audio import AudioToolError, run

KEY_SHIFT = 12
MAX_KEYS = 256
MATCH_BER = 0.15
MIN_OVERLAP = 24
MAX_OFFSET = 40


@dataclass
class FP:
    duration: float
    raw: list[int]


def _to_signed(v: int) -> int:
    v &= 0xFFFFFFFF
    return v - (1 << 32) if v >= (1 << 31) else v


async def compute(path: Path) -> FP:
    code, out, err = await run(["fpcalc", "-raw", "-json", "-length", "120", str(path)], timeout=60)
    # fpcalc 1.5.1 は新しい FFmpeg だと正常でも終了コードが 0 以外になるので、出力の有無で判定する
    try:
        data = json.loads(out)
        if not data.get("fingerprint"):
            raise ValueError("empty fingerprint")
    except ValueError as exc:
        raise AudioToolError(f"fpcalc failed ({code}): " + err.decode(errors="replace")[-200:]) from exc
    return FP(
        duration=float(data.get("duration") or 0), raw=[_to_signed(int(v)) for v in data["fingerprint"]]
    )


def keys_of(raw: list[int]) -> list[int]:
    seen: dict[int, None] = {}
    for v in raw:
        seen.setdefault((v & 0xFFFFFFFF) >> KEY_SHIFT, None)
        if len(seen) >= MAX_KEYS:
            break
    return list(seen)


_POP8 = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint8)


def bit_error_rate(a: list[int], b: list[int]) -> float:
    if not a or not b:
        return 1.0
    aa = np.array(a, dtype=np.int64).astype(np.uint32)
    bb = np.array(b, dtype=np.int64).astype(np.uint32)
    best = 1.0
    for off in range(-MAX_OFFSET, MAX_OFFSET + 1):
        if off >= 0:
            x, y = aa[off:], bb
        else:
            x, y = aa, bb[-off:]
        n = min(len(x), len(y))
        if n < MIN_OVERLAP:
            continue
        xor = np.bitwise_xor(x[:n], y[:n])
        bits = int(_POP8[xor.view(np.uint8)].sum())
        ber = bits / (n * 32)
        if ber < best:
            best = ber
    return best


async def find_duplicate(session: AsyncSession, fp: FP) -> str | None:
    keys = keys_of(fp.raw)
    if not keys:
        return None
    stmt = select(Fingerprint.sound_id, Fingerprint.raw).where(Fingerprint.keys.overlap(keys)).limit(200)
    rows = (await session.execute(stmt)).all()
    keyset = set(keys)
    ranked = sorted(rows, key=lambda r: -len(keyset.intersection(keys_of(list(r.raw)))))[:30]
    for row in ranked:
        if bit_error_rate(fp.raw, list(row.raw)) < MATCH_BER:
            return str(row.sound_id)
    return None


def to_model(sound_id: str, fp: FP) -> Fingerprint:
    return Fingerprint(sound_id=sound_id, duration_sec=fp.duration, raw=fp.raw, keys=keys_of(fp.raw))
