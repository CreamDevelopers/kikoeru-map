from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Any

import orjson
from fastapi import Request
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from starlette.responses import HTMLResponse

from app.config import get_settings
from app.services.i18n import dictionary, pick_lang, translator

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.autoescape = True

CDN = {
    "leaflet_css": (
        "https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css",
        "sha384-sHL9NAb7lN7rfvG5lfHpm643Xkcjzp4jFvuavGOndn6pjVqS6ny56CAt3nsEVT4H",
    ),
    "leaflet_js": (
        "https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js",
        "sha384-cxOPjt7s7Iz04uaHJceBmS+qpjv2JkIHNVcuOrM+YHwZOmJGBXI00mdUXEq65HTH",
    ),
    "supercluster_js": (
        "https://cdn.jsdelivr.net/npm/supercluster@8.0.1/dist/supercluster.min.js",
        "sha384-HkQmq7PC2BUVUkCsRUnOyzpliMb5M4pVPnjEiyI92tdS8buRHN6ZnehP1n1Acj84",
    ),
    "leaflet_heat_js": (
        "https://cdn.jsdelivr.net/npm/leaflet.heat@0.2.0/dist/leaflet-heat.js",
        "sha384-mFKkGiGvT5vo1fEyGCD3hshDdKmW3wzXW/x+fWriYJArD0R3gawT6lMvLboM22c0",
    ),
}


@lru_cache
def asset_version() -> str:
    h = hashlib.sha256()
    for p in sorted(STATIC_DIR.rglob("*")):
        if p.is_file():
            h.update(p.name.encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:10]


def asset(path: str) -> str:
    return f"/static/{path}?v={asset_version()}"


@lru_cache
def import_map() -> Markup:
    # モジュール間の import にもバージョン付き URL を割り当て、古いモジュールがキャッシュに残らないようにする
    imports = {
        f"/static/js/{p.name}": asset(f"js/{p.name}") for p in sorted((STATIC_DIR / "js").glob("*.js"))
    }
    return to_json({"imports": imports})


def i18n_json(lang: str) -> Markup:
    data = orjson.dumps(dictionary(lang)).decode().replace("<", "\\u003c")
    return Markup(data)  # noqa: S704  # 自前でエスケープした JSON


def to_json(value: Any) -> Markup:
    return Markup(orjson.dumps(value).decode().replace("<", "\\u003c"))  # noqa: S704


templates.env.globals.update(asset=asset, cdn=CDN, to_json=to_json, import_map=import_map)


def render(
    request: Request, name: str, context: dict[str, Any] | None = None, status_code: int = 200
) -> HTMLResponse:
    lang = pick_lang(request)
    settings = get_settings()
    ctx: dict[str, Any] = {
        "request": request,
        "lang": lang,
        "t": translator(lang),
        "i18n_json": i18n_json(lang),
        "nonce": getattr(request.state, "csp_nonce", ""),
        "turnstile_site_key": settings.turnstile_site_key,
        "base_url": settings.base_url.rstrip("/"),
    }
    if context:
        ctx.update(context)
    resp = templates.TemplateResponse(request, name, ctx, status_code=status_code)
    if request.query_params.get("lang") == lang:
        resp.set_cookie(
            "lang", lang, max_age=60 * 60 * 24 * 365, samesite="lax", secure=settings.secure_cookies
        )
    return resp
