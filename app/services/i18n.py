from __future__ import annotations

import json
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

from starlette.requests import HTTPConnection

LANGS = ("ja", "en")
DEFAULT_LANG = "ja"
_DIR = Path(__file__).resolve().parent.parent / "i18n"


@lru_cache
def dictionary(lang: str) -> dict[str, str]:
    data: dict[str, str] = json.loads((_DIR / f"{lang}.json").read_text(encoding="utf-8"))
    return data


def pick_lang(conn: HTTPConnection) -> str:
    q = conn.query_params.get("lang")
    if q in LANGS:
        return q
    c = conn.cookies.get("lang")
    if c in LANGS:
        return c
    accept = conn.headers.get("accept-language", "")
    for part in accept.split(","):
        code = part.split(";")[0].strip().lower()[:2]
        if code in LANGS:
            return code
    return DEFAULT_LANG


def translator(lang: str) -> Callable[..., str]:
    d = dictionary(lang)
    fallback = dictionary(DEFAULT_LANG)

    def t(key: str, **kwargs: object) -> str:
        text = d.get(key) or fallback.get(key) or key
        return text.format(**kwargs) if kwargs else text

    return t
