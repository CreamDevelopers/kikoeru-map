from __future__ import annotations

import shutil
from pathlib import Path

from app.config import get_settings

EXT_TYPES = {
    "webm": "audio/webm",
    "m4a": "audio/mp4",
    "json": "application/json",
    "png": "image/png",
}


def sound_dir(sound_id: str) -> Path:
    return get_settings().media_dir / sound_id[:2] / sound_id


def file_path(sound_id: str, content_hash: str, ext: str) -> Path:
    return sound_dir(sound_id) / f"{content_hash}.{ext}"


def url(sound_id: str, content_hash: str | None, ext: str) -> str | None:
    if not content_hash:
        return None
    return f"/media/{sound_id}/{content_hash}.{ext}"


def store(src: Path, sound_id: str, content_hash: str, ext: str) -> Path:
    dst = file_path(sound_id, content_hash, ext)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), dst)
    return dst


def remove_all(sound_id: str) -> None:
    shutil.rmtree(sound_dir(sound_id), ignore_errors=True)
