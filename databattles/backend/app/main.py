"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.errors import AppError
from app.core.logging import configure_logging, request_id_var
from app.core.metrics import metrics
from app.core.middleware import BodySizeLimitMiddleware, CSRFMiddleware, RequestContextMiddleware
from app.jobs.registry import load_handlers

logger = logging.getLogger("databattles.api")

API_PREFIX = "/api/v1"


def _error_body(code: str, message: str, details: object | None = None) -> dict[str, object]:
    return {"error": {"code": code, "message": message, "details": details, "request_id": request_id_var.get()}}


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error(_: Request, exc: AppError) -> JSONResponse:
        headers = {}
        if exc.status_code == 429 and exc.details and "retry_after_seconds" in exc.details:
            headers["Retry-After"] = str(exc.details["retry_after_seconds"])
        return JSONResponse(_error_body(exc.code, exc.message, exc.details), status_code=exc.status_code, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields: dict[str, str] = {}
        for err in exc.errors():
            loc = [str(p) for p in err.get("loc", []) if p not in ("body", "query", "path")]
            key = ".".join(loc) or "request"
            fields.setdefault(key, str(err.get("msg", "Invalid value")).removeprefix("Value error, "))
        return JSONResponse(_error_body("validation_error", "Some fields are invalid.", {"fields": fields}), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed", 401: "unauthenticated", 403: "forbidden"}.get(exc.status_code, "http_error")
        message = exc.detail if isinstance(exc.detail, str) else "Request failed."
        if exc.status_code == 404:
            message = "We could not find what you were looking for."
        return JSONResponse(_error_body(code, message), status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_error", extra={"path": request.url.path})
        metrics.incr("unhandled_errors")
        try:
            from app.models.system import AppErrorLog

            with SessionLocal() as db:
                db.add(AppErrorLog(request_id=request_id_var.get(), method=request.method, path=request.url.path[:300],
                                   error_type=type(exc).__name__[:120], message=str(exc)[:500],
                                   user_id=getattr(request.state, "actor_id", None), source="api"))
                db.commit()
        except Exception:  # noqa: BLE001 — never let error logging mask the original error
            logger.warning("error_log_write_failed")
        return JSONResponse(_error_body("internal_error", "Something went wrong on our side. Please try again."), status_code=500)


async def _embedded_worker(stop: asyncio.Event) -> None:
    """Optional in-process worker for tiny single-node deployments (EMBEDDED_WORKER=true)."""
    from app.worker import run_once

    while not stop.is_set():
        processed = await asyncio.to_thread(run_once)
        if not processed:
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=settings.WORKER_POLL_SECONDS)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    load_handlers()
    stop = asyncio.Event()
    task = asyncio.create_task(_embedded_worker(stop)) if settings.EMBEDDED_WORKER else None
    logger.info("startup", extra={"env": settings.ENV})
    try:
        yield
    finally:
        stop.set()
        if task:
            with suppress(Exception):
                await asyncio.wait_for(task, timeout=10)


def create_app() -> FastAPI:
    app = FastAPI(
        title=f"{settings.APP_NAME} API",
        version="1.0.0",
        description="University AI competition, learning, open-source and reputation platform.",
        lifespan=lifespan,
        docs_url="/docs" if not settings.is_production else None,
        redoc_url=None,
        openapi_url="/openapi.json" if not settings.is_production else None,
    )
    _register_exception_handlers(app)

    from app.modules.admin.router import router as admin
    from app.modules.analytics.router import router as analytics
    from app.modules.auth.router import router as auth
    from app.modules.billing.router import router as billing
    from app.modules.competitions.router import router as competitions
    from app.modules.credentials.router import router as credentials
    from app.modules.datasets.router import router as datasets
    from app.modules.discussions.router import router as discussions
    from app.modules.files.router import router as files
    from app.modules.judging.router import router as judging
    from app.modules.leaderboards.router import router as leaderboards
    from app.modules.learning.router import router as learning
    from app.modules.meta.router import health_router
    from app.modules.meta.router import router as meta
    from app.modules.moderation.router import router as moderation
    from app.modules.notifications.router import router as notifications
    from app.modules.opensource.router import router as opensource
    from app.modules.orgs.router import router as orgs
    from app.modules.projects.router import router as projects
    from app.modules.search.router import router as search
    from app.modules.submissions.router import router as submissions
    from app.modules.teams.router import router as teams
    from app.modules.users.router import router as users

    # Order matters where paths overlap: more specific competition sub-routers before generic ones.
    for r in (meta, auth, users, orgs, teams, submissions, leaderboards, judging, credentials, analytics, competitions, datasets,
              files, projects, opensource, learning, discussions, moderation, notifications, search, admin, billing):
        app.include_router(r, prefix=API_PREFIX)
    app.include_router(health_router)

    # Middleware: last added runs first. Order of execution: RequestContext -> CORS -> BodySize -> CSRF -> app.
    app.add_middleware(CSRFMiddleware)
    app.add_middleware(BodySizeLimitMiddleware)
    app.add_middleware(CORSMiddleware, allow_origins=sorted(settings.allowed_origins), allow_credentials=True,
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                       allow_headers=["Content-Type", "X-CSRF-Token", "Idempotency-Key", "Authorization", "X-Request-ID"],
                       expose_headers=["X-Request-ID", "Retry-After"], max_age=600)
    app.add_middleware(RequestContextMiddleware)
    return app


app = create_app()
