"""Pure ASGI middlewares: request IDs + access logs, secure headers, request body size limits."""

from __future__ import annotations

import contextlib
import json
import re
import time
import uuid
from typing import Any

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from aegis_api.config import get_settings
from aegis_api.observability import metrics
from aegis_api.observability.tracing import new_span_id, new_trace_id, parse_traceparent, traceparent

log = structlog.get_logger("aegis.http")
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9\-_.]{8,64}$")
UPLOAD_PATH_RE = re.compile(r"^/api/v1/(policies(/upload|/[0-9a-f-]{36}/(upload|version))|evidence/verify-package)$")


class RequestContextMiddleware:
    """Assigns ``request_id`` (propagated via ``X-Request-ID``) and emits one structured access log."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        incoming = headers.get("x-request-id", "")
        request_id = incoming if _REQUEST_ID_RE.match(incoming) else f"req_{uuid.uuid4().hex[:20]}"
        parent = parse_traceparent(headers.get("traceparent"))
        trace_id = parent[0] if parent else new_trace_id()
        span_id = new_span_id()
        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        state["trace_id"] = trace_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id, trace_id=trace_id)
        started = time.perf_counter()
        status_holder: dict[str, int] = {"status": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                raw = list(message.get("headers", []))
                raw.append((b"x-request-id", request_id.encode()))
                raw.append((b"traceparent", traceparent(trace_id, span_id).encode()))
                message["headers"] = raw
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            path = scope.get("path", "")
            elapsed = time.perf_counter() - started
            route = getattr(scope.get("route"), "path", None) or "unmatched"
            if path not in ("/health", "/ready", "/live", "/metrics"):
                principal = state.get("principal")
                log.info(
                    "request",
                    method=scope.get("method"),
                    path=path,
                    route=route,
                    status=status_holder["status"],
                    latency_ms=round(elapsed * 1000, 1),
                    tenant=str(principal.organization_id) if principal is not None else None,
                )
                with contextlib.suppress(Exception):
                    metrics.HTTP_REQUESTS.labels(scope.get("method"), route, str(status_holder["status"])).inc()
                    if not path.endswith("/stream"):
                        metrics.HTTP_LATENCY.labels(scope.get("method"), route).observe(elapsed)


class SecureHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.production = get_settings().is_production

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")
        is_docs = path.startswith(("/docs", "/redoc"))

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                raw: list[tuple[bytes, bytes]] = list(message.get("headers", []))
                extra = {
                    b"x-content-type-options": b"nosniff",
                    b"x-frame-options": b"DENY",
                    b"referrer-policy": b"strict-origin-when-cross-origin",
                    b"permissions-policy": b"camera=(), microphone=(), geolocation=()",
                    b"cross-origin-opener-policy": b"same-origin",
                }
                if not is_docs:
                    extra[b"content-security-policy"] = b"default-src 'none'; frame-ancestors 'none'"
                if self.production:
                    extra[b"strict-transport-security"] = b"max-age=63072000; includeSubDomains"
                present = {k.lower() for k, _ in raw}
                raw.extend((k, v) for k, v in extra.items() if k not in present)
                message["headers"] = raw
            await send(message)

        await self.app(scope, receive, send_wrapper)


UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
SESSION_COOKIE_NAME = b"aegis_session"


def _origin_of(url: str) -> str | None:
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}".lower()


class OriginGuardMiddleware:
    """CSRF defence for cookie-authenticated requests (in addition to SameSite=Lax cookies and strict CORS).

    For unsafe methods:
      * a request carrying an ``Origin`` header that is not a trusted origin is rejected;
      * a request authenticated by the session cookie (no ``Authorization``/``X-API-Key``) must present a
        trusted ``Origin`` — or, failing that, a trusted ``Referer``.
    API-key and bearer-token clients (SDK, CI, MCP) are unaffected: they send no ambient credentials."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") not in UNSAFE_METHODS:
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode(errors="replace") for k, v in scope.get("headers", [])}
        trusted = get_settings().trusted_origins
        origin = headers.get("origin")
        if origin and origin != "null" and origin.rstrip("/").lower() not in {t.lower() for t in trusted}:
            await _reject_origin(scope, send)
            return
        has_bearer = bool(headers.get("authorization") or headers.get("x-api-key"))
        cookie_auth = SESSION_COOKIE_NAME.decode() + "=" in headers.get("cookie", "")
        if cookie_auth and not has_bearer:
            candidate = origin if origin and origin != "null" else _origin_of(headers.get("referer", "") or "")
            if not candidate or candidate.rstrip("/").lower() not in {t.lower() for t in trusted}:
                await _reject_origin(scope, send)
                return
        await self.app(scope, receive, send)


async def _reject_origin(scope: Scope, send: Send) -> None:
    request_id: Any = scope.get("state", {}).get("request_id")
    body = json.dumps(
        {
            "error": {
                "code": "origin_rejected",
                "message": "Cross-origin request rejected",
                "request_id": request_id,
            }
        }
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 403,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})


class BodySizeLimitMiddleware:
    """Rejects bodies above the configured limit (uploads have their own, larger limit)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        settings = get_settings()
        self.default_limit = settings.max_request_bytes
        self.upload_limit = settings.max_upload_bytes + 64 * 1024

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") in ("GET", "HEAD", "OPTIONS"):
            await self.app(scope, receive, send)
            return
        limit = self.upload_limit if UPLOAD_PATH_RE.match(scope.get("path", "")) else self.default_limit
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        declared = headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            await _reject(scope, send, limit)
            return
        received = 0
        exceeded = False

        async def receive_wrapper() -> Message:
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    exceeded = True
                    return {"type": "http.disconnect"}
            return message

        response_started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        await self.app(scope, receive_wrapper, send_wrapper)
        if exceeded and not response_started:
            await _reject(scope, send, limit)


async def _reject(scope: Scope, send: Send, limit: int) -> None:
    request_id: Any = scope.get("state", {}).get("request_id")
    body = json.dumps(
        {
            "error": {
                "code": "payload_too_large",
                "message": f"Request body exceeds {limit} bytes",
                "request_id": request_id,
            }
        }
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})
