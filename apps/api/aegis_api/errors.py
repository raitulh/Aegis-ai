"""Application errors and the consistent error envelope.

Every error response has the shape::

    {"error": {"code": "...", "message": "...", "request_id": "...", "details": {...}?}}

Raw exceptions never reach clients; unexpected failures are logged with the request ID and returned as
``internal_error``.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = structlog.get_logger("aegis.errors")


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str | None = None, *, code: str | None = None, details: Any = None) -> None:
        self.message = message or self.__class__.__doc__ or "Request failed"
        if code:
            self.code = code
        self.details = details
        super().__init__(self.message)


class NotFound(AppError):
    """The requested resource was not found."""

    status_code = 404
    code = "not_found"


class Unauthorized(AppError):
    """Authentication is required."""

    status_code = 401
    code = "unauthenticated"


class Forbidden(AppError):
    """You do not have permission to perform this action."""

    status_code = 403
    code = "forbidden"


class Conflict(AppError):
    """The request conflicts with the current state of the resource."""

    status_code = 409
    code = "conflict"


class ValidationFailed(AppError):
    """The request is invalid."""

    status_code = 422
    code = "validation_error"


class PayloadTooLarge(AppError):
    """The request payload is too large."""

    status_code = 413
    code = "payload_too_large"


class RateLimited(AppError):
    """Too many requests. Please retry later."""

    status_code = 429
    code = "rate_limited"

    def __init__(self, message: str | None = None, *, retry_after: int = 60) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class PlanLimitExceeded(AppError):
    """Your plan limit has been reached."""

    status_code = 403
    code = "plan_limit_exceeded"


class ServiceUnavailable(AppError):
    """A dependent service is unavailable."""

    status_code = 503
    code = "service_unavailable"


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def error_body(code: str, message: str, request_id: str | None, details: Any = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"code": code, "message": message, "request_id": request_id}
    if details is not None:
        payload["details"] = details
    return {"error": payload}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        headers = {}
        if isinstance(exc, RateLimited):
            headers["Retry-After"] = str(exc.retry_after)
        if exc.status_code >= 500:
            log.error("app_error", code=exc.code, message=exc.message)
        return JSONResponse(
            error_body(exc.code, exc.message, _request_id(request), exc.details),
            status_code=exc.status_code,
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {
                "field": ".".join(str(p) for p in err.get("loc", ()) if p not in ("body", "query", "path")),
                "message": err.get("msg", "Invalid value"),
                "type": err.get("type"),
            }
            for err in exc.errors()
        ]
        return JSONResponse(
            error_body("validation_error", "Request validation failed", _request_id(request), details),
            status_code=422,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {
            400: "bad_request",
            401: "unauthenticated",
            403: "forbidden",
            404: "not_found",
            405: "method_not_allowed",
            413: "payload_too_large",
            415: "unsupported_media_type",
            429: "rate_limited",
        }.get(exc.status_code, "http_error")
        message = exc.detail if isinstance(exc.detail, str) else "Request failed"
        return JSONResponse(
            error_body(code, message, _request_id(request)),
            status_code=exc.status_code,
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_exception", path=request.url.path, error_type=type(exc).__name__)
        return JSONResponse(
            error_body(
                "internal_error",
                "An unexpected error occurred. The incident has been logged.",
                _request_id(request),
            ),
            status_code=500,
        )
