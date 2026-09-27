from __future__ import annotations

import httpx
import pytest

from app.config import get_settings
from app.services import turnstile

from .conftest import ip_headers


def _mock(payload: dict, seen: list[dict] | None = None):  # type: ignore[no-untyped-def]
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            from urllib.parse import parse_qs

            seen.append({k: v[0] for k, v in parse_qs(request.content.decode()).items()})
        return httpx.Response(200, json=payload)

    return lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture
def real_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "turnstile_secret_key", "0x4AAAAAAA-real-secret")
    monkeypatch.setattr(s, "base_url", "https://kikoeru.example.jp")


async def test_success_sends_secret_token_and_ip(monkeypatch: pytest.MonkeyPatch, real_keys: None) -> None:
    seen: list[dict] = []
    monkeypatch.setattr(
        turnstile,
        "client_factory",
        _mock({"success": True, "hostname": "kikoeru.example.jp", "action": "post"}, seen),
    )
    res = await turnstile.verify_token("tok-123", "203.0.113.5", "post")
    assert res.success
    assert seen[0] == {"secret": "0x4AAAAAAA-real-secret", "response": "tok-123", "remoteip": "203.0.113.5"}


async def test_failure(monkeypatch: pytest.MonkeyPatch, real_keys: None) -> None:
    monkeypatch.setattr(
        turnstile, "client_factory", _mock({"success": False, "error-codes": ["invalid-input-response"]})
    )
    res = await turnstile.verify_token("bad", "203.0.113.5", "post")
    assert not res.success
    assert res.error_codes == ["invalid-input-response"]


async def test_hostname_mismatch(monkeypatch: pytest.MonkeyPatch, real_keys: None) -> None:
    monkeypatch.setattr(
        turnstile,
        "client_factory",
        _mock({"success": True, "hostname": "evil.example.com", "action": "post"}),
    )
    res = await turnstile.verify_token("tok", "203.0.113.5", "post")
    assert not res.success
    assert res.error_codes == ["hostname-mismatch"]


async def test_action_mismatch(monkeypatch: pytest.MonkeyPatch, real_keys: None) -> None:
    monkeypatch.setattr(
        turnstile,
        "client_factory",
        _mock({"success": True, "hostname": "kikoeru.example.jp", "action": "login"}),
    )
    res = await turnstile.verify_token("tok", "203.0.113.5", "post")
    assert not res.success
    assert res.error_codes == ["action-mismatch"]


async def test_missing_token_is_rejected_without_request() -> None:
    res = await turnstile.verify_token("", None, "post")
    assert not res.success
    assert res.error_codes == ["missing-input-response"]


async def test_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    monkeypatch.setattr(
        turnstile, "client_factory", lambda: httpx.AsyncClient(transport=httpx.MockTransport(boom))
    )
    res = await turnstile.verify_token("tok", None, "post")
    assert not res.success


async def test_dependency_blocks_report_api(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from .conftest import create_sound

    sound = await create_sound()
    monkeypatch.setattr(
        turnstile, "client_factory", _mock({"success": False, "error-codes": ["timeout-or-duplicate"]})
    )
    r = await client.post(
        f"/api/sounds/{sound.id}/reports",
        json={"reason": "spam", "cf-turnstile-response": "used-token"},
        headers=ip_headers(),
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "turnstile_failed"
