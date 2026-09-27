from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from brotli_asgi import BrotliMiddleware
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, ORJSONResponse, RedirectResponse, Response
from sqlalchemy import text
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from app.config import get_settings
from app.db import dispose_engine, get_engine, init_engine
from app.middleware import BodySizeLimitMiddleware, RequestContextMiddleware
from app.routers import admin, api, game, public, sse
from app.services import metrics
from app.services.auth import LoginRequired, load_session
from app.services.events import broadcaster
from app.services.logging import setup_logging
from app.services.queue import close_pool
from app.services.redis import close_redis, get_redis
from app.web import STATIC_DIR

log = logging.getLogger("kikoeru")


class CachedStatic(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        resp = await super().get_response(path, scope)
        qs = scope.get("query_string", b"")
        if resp.status_code in (200, 304):
            if b"v=" in qs:
                resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            else:
                resp.headers["Cache-Control"] = "public, max-age=300"
        return resp


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    init_engine()
    settings = get_settings()
    for d in (settings.media_dir, settings.upload_dir):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            log.warning("cannot create directory", extra={"path": str(d)})
    await broadcaster.start()
    yield
    await broadcaster.stop()
    await close_pool()
    await close_redis()
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="きこえる地図",
        lifespan=lifespan,
        default_response_class=ORJSONResponse,
        docs_url="/api/docs" if settings.is_dev else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.is_dev else None,
    )

    @app.exception_handler(LoginRequired)
    async def _login_required(request: Request, exc: LoginRequired) -> Response:
        return RedirectResponse("/admin/login", status_code=303)

    @app.exception_handler(HTTPException)
    async def _http_exc(request: Request, exc: HTTPException) -> Response:
        detail: Any = exc.detail
        if not isinstance(detail, dict):
            detail = {"code": "error", "message": str(detail)}
        return JSONResponse({"error": detail}, status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> Response:
        errors = [
            {"loc": [str(x) for x in e.get("loc", ())], "msg": e.get("msg", ""), "type": e.get("type", "")}
            for e in exc.errors()
        ]
        return JSONResponse({"error": {"code": "invalid_input", "errors": errors}}, status_code=422)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    async def readyz() -> Response:
        checks: dict[str, str] = {}
        try:
            async with get_engine().connect() as conn:
                await conn.execute(text("SELECT 1"))
            checks["db"] = "ok"
        except Exception as exc:
            checks["db"] = f"error: {type(exc).__name__}"
        try:
            await get_redis().ping()
            checks["redis"] = "ok"
        except Exception as exc:
            checks["redis"] = f"error: {type(exc).__name__}"
        ok = all(v == "ok" for v in checks.values())
        return JSONResponse(
            {"status": "ok" if ok else "error", "checks": checks}, status_code=200 if ok else 503
        )

    @app.get("/metrics", include_in_schema=False)
    async def metrics_endpoint(request: Request) -> Response:
        auth = request.headers.get("authorization", "")
        token_ok = bool(settings.metrics_token) and auth == f"Bearer {settings.metrics_token}"
        if not token_ok:
            sess = await load_session(request)
            if sess is None or not sess.is_admin:
                return JSONResponse({"error": {"code": "forbidden"}}, status_code=403)
        body, ctype = await metrics.render()
        return Response(body, media_type=ctype, headers={"Cache-Control": "no-store"})

    app.include_router(api.router)
    app.include_router(sse.router)
    app.include_router(game.router)
    app.include_router(admin.router)
    app.include_router(public.router)
    app.mount("/static", CachedStatic(directory=str(STATIC_DIR)), name="static")

    # SSE・音声（Range 配信）は圧縮しない
    app.add_middleware(
        BrotliMiddleware,
        quality=4,
        minimum_size=512,
        gzip_fallback=True,
        excluded_handlers=[r"^/media/.*", r"^/api/.*/events$", r"^/api/stream$", r"^/api/game/.*/audio/.*"],
    )
    app.add_middleware(
        BodySizeLimitMiddleware, max_bytes=settings.max_upload_bytes + 256 * 1024, paths=("/api/sounds",)
    )
    app.add_middleware(RequestContextMiddleware)
    return app


app = create_app()
