from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import HTTPException, Request

from app.config import get_settings
from app.services.security import client_ip

log = logging.getLogger(__name__)

TOKEN_FIELD = "cf-turnstile-response"  # noqa: S105


@dataclass
class TurnstileResult:
    success: bool
    error_codes: list[str] = field(default_factory=list)
    hostname: str | None = None
    action: str | None = None


def _default_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(8.0))


client_factory: Callable[[], httpx.AsyncClient] = _default_client


async def verify_token(token: str, remote_ip: str | None, expected_action: str) -> TurnstileResult:
    settings = get_settings()
    if not token or len(token) > 2048:
        return TurnstileResult(False, ["missing-input-response"])
    data: dict[str, Any] = {"secret": settings.turnstile_secret_key, "response": token}
    if remote_ip:
        data["remoteip"] = remote_ip
    try:
        async with client_factory() as client:
            resp = await client.post(settings.turnstile_verify_url, data=data)
            payload = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("turnstile verify request failed", extra={"error": str(exc)})
        return TurnstileResult(False, ["internal-error"])

    result = TurnstileResult(
        success=bool(payload.get("success")),
        error_codes=list(payload.get("error-codes") or []),
        hostname=payload.get("hostname"),
        action=payload.get("action"),
    )
    if not result.success:
        return result
    # テスト用キーは hostname / action をダミー値で返すため検証を省略する
    if settings.turnstile_is_test_key:
        return result
    if result.hostname != settings.hostname:
        return TurnstileResult(False, ["hostname-mismatch"], result.hostname, result.action)
    if result.action != expected_action:
        return TurnstileResult(False, ["action-mismatch"], result.hostname, result.action)
    return result


def require_turnstile(action: str) -> Callable[[Request], Awaitable[TurnstileResult]]:
    async def dependency(request: Request) -> TurnstileResult:
        token = request.headers.get("x-turnstile-token", "")
        if not token:
            ctype = request.headers.get("content-type", "")
            if ctype.startswith(("multipart/form-data", "application/x-www-form-urlencoded")):
                form = await request.form()
                value = form.get(TOKEN_FIELD)
                token = value if isinstance(value, str) else ""
            elif ctype.startswith("application/json"):
                try:
                    body = await request.json()
                except ValueError:
                    body = {}
                if isinstance(body, dict):
                    token = str(body.get(TOKEN_FIELD) or body.get("turnstile_token") or "")
        result = await verify_token(token, client_ip(request), action)
        if not result.success:
            raise HTTPException(
                status_code=403,
                detail={"code": "turnstile_failed", "errors": result.error_codes},
            )
        return result

    return dependency
