"""ASGI middleware: request context, security headers, CSRF/origin checks, body size limits."""

from __future__ import annotations

import logging
import re
import secrets
import time
import uuid
from http.cookies import SimpleCookie

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.audit import ip_hash_var
from app.core.config import settings
from app.core.deps import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE
from app.core.logging import request_id_var, user_id_var
from app.core.metrics import metrics
from app.core.security import constant_time_eq, hash_identifier

logger = logging.getLogger("databattles.http")

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_WEBHOOK_PATH = "/api/v1/opensource/github/webhook"
_CSRF_EXEMPT_PREFIXES = (_WEBHOOK_PATH, "/api/v1/billing/webhook")
_DOCS_PREFIXES = ("/docs", "/redoc", "/openapi.json")


def _error(status: int, code: str, message: str, request_id: str | None) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message, "details": None, "request_id": request_id}},
        status_code=status,
    )


class RequestContextMiddleware:
    """Assigns a correlation id, records latency metrics, adds security headers."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        incoming = headers.get("x-request-id", "")
        request_id = incoming if _REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex
        token_rid = request_id_var.set(request_id)
        token_uid = user_id_var.set(None)
        client = scope.get("client")
        token_ip = ip_hash_var.set(hash_identifier(client[0]) if client else None)
        started = time.perf_counter()
        status_holder = {"status": 500}
        path: str = scope.get("path", "")

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                h = MutableHeaders(scope=message)
                h["X-Request-ID"] = request_id
                h.setdefault("X-Content-Type-Options", "nosniff")
                h.setdefault("X-Frame-Options", "DENY")
                h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
                h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
                h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
                if not path.startswith(_DOCS_PREFIXES):
                    h.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
                if settings.COOKIE_SECURE:
                    h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
                if path.startswith("/api/") and "cache-control" not in h:
                    h["Cache-Control"] = "no-store"
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed = (time.perf_counter() - started) * 1000
            metrics.record_request(status_holder["status"], elapsed)
            if elapsed > 1500:
                logger.warning("slow_request", extra={"path": path, "elapsed_ms": round(elapsed, 1)})
            request_id_var.reset(token_rid)
            user_id_var.reset(token_uid)
            ip_hash_var.reset(token_ip)


class CSRFMiddleware:
    """Defends cookie-authenticated requests.

    1. Unsafe requests carrying an Origin header must come from an allowed origin.
    2. Unsafe requests authenticated by the session cookie must echo the CSRF
       cookie in the X-CSRF-Token header (double-submit). Bearer-token clients
       are exempt because browsers never attach bearer tokens automatically.
    A CSRF cookie is issued on any response when the client does not have one.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        method = scope["method"]
        path: str = scope.get("path", "")
        cookies = SimpleCookie()
        try:
            cookies.load(headers.get("cookie", ""))
        except Exception:  # noqa: BLE001 — malformed cookie header
            cookies = SimpleCookie()
        csrf_cookie = cookies[CSRF_COOKIE].value if CSRF_COOKIE in cookies else None
        rid = request_id_var.get()

        if method in _UNSAFE_METHODS and path.startswith("/api/") and not path.startswith(_CSRF_EXEMPT_PREFIXES):
            origin = headers.get("origin")
            if origin and origin.rstrip("/") not in settings.allowed_origins:
                await _error(403, "csrf_failed", "Cross-origin request blocked.", rid)(scope, receive, send)
                return
            has_bearer = headers.get("authorization", "").lower().startswith("bearer ")
            if SESSION_COOKIE in cookies and not has_bearer:
                supplied = headers.get(CSRF_HEADER, "")
                if not csrf_cookie or not supplied or not constant_time_eq(csrf_cookie, supplied):
                    await _error(403, "csrf_failed", "Security token missing or invalid. Refresh the page and try again.",
                                 rid)(scope, receive, send)
                    return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start" and not csrf_cookie:
                h = MutableHeaders(scope=message)
                parts = [f"{CSRF_COOKIE}={secrets.token_urlsafe(24)}", "Path=/", "SameSite=Lax", "Max-Age=31536000"]
                if settings.COOKIE_SECURE:
                    parts.append("Secure")
                if settings.COOKIE_DOMAIN:
                    parts.append(f"Domain={settings.COOKIE_DOMAIN}")
                h.append("set-cookie", "; ".join(parts))
            await send(message)

        await self.app(scope, receive, send_wrapper)


class BodySizeLimitMiddleware:
    """Rejects bodies larger than the limit for their content type.

    Checks the declared Content-Length up front and also counts streamed bytes, so chunked
    requests without a Content-Length cannot bypass the limit.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    @staticmethod
    def _limit(scope: Scope, headers: Headers) -> int:
        ctype = headers.get("content-type", "")
        if ctype.startswith("multipart/form-data"):
            return (max(settings.MAX_DATASET_FILE_MB, settings.MAX_SUBMISSION_MB) + 2) * 1024 * 1024
        if scope.get("path", "").startswith(_WEBHOOK_PATH):
            return 25 * 1024 * 1024  # GitHub caps payloads at 25 MB
        return settings.MAX_JSON_BODY_KB * 1024

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        limit = self._limit(scope, headers)
        length = headers.get("content-length")
        if length and length.isdigit() and int(length) > limit:
            await _error(413, "payload_too_large", "The request body is too large.", request_id_var.get())(scope, receive, send)
            return
        received = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _BodyTooLarge()
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLarge:
            if not response_started:
                await _error(413, "payload_too_large", "The request body is too large.", request_id_var.get())(scope, receive, send)


class _BodyTooLarge(Exception):
    pass
