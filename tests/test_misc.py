from __future__ import annotations

import json
import re
from pathlib import Path

import httpx

from .conftest import create_sound

APP = Path(__file__).resolve().parent.parent / "app"


async def test_health(client: httpx.AsyncClient) -> None:
    assert (await client.get("/healthz")).json() == {"status": "ok"}
    r = await client.get("/readyz")
    assert r.status_code == 200
    assert r.json()["checks"] == {"db": "ok", "redis": "ok"}


async def test_security_headers_and_csp_nonce(client: httpx.AsyncClient) -> None:
    r = await client.get("/")
    assert r.status_code == 200
    csp = r.headers["content-security-policy"]
    nonce = re.search(r"'nonce-([^']+)'", csp)
    assert nonce
    assert f'nonce="{nonce.group(1)}"' in r.text
    assert "https://cyberjapandata.gsi.go.jp" in csp and "https://challenges.cloudflare.com" in csp
    assert "unsafe-inline" not in csp.split("style-src-attr")[0]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"]
    assert "microphone=(self)" in r.headers["permissions-policy"]
    assert "geolocation=(self)" in r.headers["permissions-policy"]
    assert r.headers["x-request-id"]
    for m in re.finditer(r'<script src="https://cdn\.jsdelivr\.net[^>]+>', r.text):
        assert 'integrity="sha384-' in m.group(0)


async def test_user_input_is_escaped(client: httpx.AsyncClient) -> None:
    s = await create_sound(title="<script>alert(1)</script>", comment='"><img src=x onerror=alert(1)>')
    r = await client.get(f"/s/{s.id}")
    assert r.status_code == 200
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;" in r.text
    assert "<img src=x" not in r.text


async def test_sound_page_has_ogp(client: httpx.AsyncClient) -> None:
    s = await create_sound(ogp_hash="ogp-abcdef12345", pref_name="東京都", city_name="千代田区")
    r = await client.get(f"/s/{s.id}")
    assert 'property="og:image"' in r.text
    assert f"/media/{s.id}/ogp-abcdef12345.png" in r.text
    assert (await client.get("/s/doesnotexist1")).status_code == 404


async def test_static_pages_and_lists(client: httpx.AsyncClient) -> None:
    await create_sound(pref_code=13, pref_name="東京都")
    for path in ["/about/guidelines", "/about/terms", "/about/privacy", "/about/attribution", "/about/help"]:
        for lang in ("ja", "en"):
            assert (await client.get(f"{path}?lang={lang}")).status_code == 200
    for path in ["/list/new", "/list/popular", "/list/prefs", "/list/pref/13", "/game", "/walk"]:
        assert (await client.get(path)).status_code == 200
    for path in ["/", "/about/terms", "/list/new"]:
        html = (await client.get(path)).text
        assert 'href="https://www.kikoeru.org"' in html
        assert "img/kikoeru-logo.png" in html
    r = await client.get("/manifest.webmanifest")
    assert r.json()["display"] == "standalone"
    assert all(i["type"] == "image/png" for i in r.json()["icons"])
    r = await client.get("/sw.js")
    assert "__VERSION__" not in r.text and "__SHELL__" not in r.text


def _used_keys() -> set[str]:
    keys: set[str] = set()
    pat = re.compile(r"""\bt\(\s*["']([a-z0-9_.-]+)["']""")
    for p in list((APP / "templates").rglob("*.html")) + list((APP / "static" / "js").glob("*.js")):
        keys |= set(pat.findall(p.read_text(encoding="utf-8")))
    return {k for k in keys if "." in k and not k.endswith(".")}


def test_i18n_dictionaries_are_complete() -> None:
    ja = json.loads((APP / "i18n" / "ja.json").read_text(encoding="utf-8"))
    en = json.loads((APP / "i18n" / "en.json").read_text(encoding="utf-8"))
    assert set(ja) == set(en)
    missing = _used_keys() - set(ja)
    assert not missing, missing
