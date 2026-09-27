from __future__ import annotations

import logging
import re
import secrets
import time
import uuid

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.services.logging import request_id_var
from app.services.metrics import LATENCY, REQUESTS

log = logging.getLogger("kikoeru.access")

_RID_OK = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

GSI = "https://cyberjapandata.gsi.go.jp"
TURNSTILE = "https://challenges.cloudflare.com"
CDN = "https://cdn.jsdelivr.net"


def build_csp(nonce: str) -> str:
    return "; ".join(
        [
            "default-src 'self'",
            f"script-src 'self' 'nonce-{nonce}' {TURNSTILE} {CDN}",
            f"style-src 'self' 'nonce-{nonce}' {CDN} https://fonts.googleapis.com",
            # Leaflet・Turnstile が要素に直接付ける style 属性のみ許可
            "style-src-attr 'unsafe-inline'",
            "font-src 'self' https://fonts.gstatic.com",
            f"img-src 'self' data: blob: {GSI} {CDN}",
            f"connect-src 'self' {GSI} https://msearch.gsi.go.jp",
            "media-src 'self' blob:",
            f"frame-src {TURNSTILE}",
            "worker-src 'self'",
            "manifest-src 'self'",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        ]
    )


def _route_label(scope: Scope) -> str:
    route = scope.get("route")
    path = getattr(route, "path", None)
    if path:
        return str(path)
    p = scope.get("path", "")
    if p.startswith("/static/"):
        return "/static"
    if p.startswith("/media/"):
        return "/media"
    return "unmatched"


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode(errors="replace") for k, v in scope.get("headers", [])}
        rid = headers.get("x-request-id", "")
        if not _RID_OK.match(rid):
            rid = headers.get("cf-ray", "") or uuid.uuid4().hex
            rid = rid if _RID_OK.match(rid) else uuid.uuid4().hex
        token = request_id_var.set(rid)
        nonce = secrets.token_urlsafe(16)
        scope.setdefault("state", {})
        scope["state"]["csp_nonce"] = nonce
        scope["state"]["request_id"] = rid
        start = time.perf_counter()
        status_holder = {"status": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                h = MutableHeaders(scope=message)
                h["X-Request-ID"] = rid
                h.setdefault("X-Content-Type-Options", "nosniff")
                h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
                h.setdefault(
                    "Permissions-Policy",
                    "microphone=(self), geolocation=(self), camera=(), payment=(), usb=()",
                )
                h.setdefault("X-Frame-Options", "DENY")
                h.setdefault("Content-Security-Policy", build_csp(nonce))
                h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed = time.perf_counter() - start
            route = _route_label(scope)
            method = scope.get("method", "GET")
            status = status_holder["status"]
            REQUESTS.labels(method, route, str(status)).inc()
            LATENCY.labels(method, route).observe(elapsed)
            if route not in ("/healthz", "/static", "/media"):
                log.info(
                    "request",
                    extra={
                        "method": method,
                        "path": scope.get("path"),
                        "route": route,
                        "status": status,
                        "duration_ms": round(elapsed * 1000, 2),
                    },
                )
            request_id_var.reset(token)


class BodySizeLimitMiddleware:
    # multipart の解析前に Content-Length で弾き、巨大なファイルがディスクに書かれるのを防ぐ
    def __init__(self, app: ASGIApp, max_bytes: int, paths: tuple[str, ...]) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.paths = paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "POST" or scope.get("path") not in self.paths:
            await self.app(scope, receive, send)
            return
        length: int | None = None
        for k, v in scope.get("headers", []):
            if k == b"content-length":
                try:
                    length = int(v)
                except ValueError:
                    length = None
        if length is None:
            await _reject(send, 411, b'{"error":{"code":"length_required"}}')
            return
        if length > self.max_bytes:
            await _reject(send, 413, b'{"error":{"code":"too_large"}}')
            return
        await self.app(scope, receive, send)


async def _reject(send: Send, status: int, body: bytes) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})
