"""Pure ASGI middlewares: request IDs + access logs, secure headers, request body size limits."""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from aegis_api.config import get_settings

log = structlog.get_logger("aegis.http")
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9\-_.]{8,64}$")
# Probes and metric scrapes are not access-logged (they would drown real traffic).
_QUIET_PATHS = frozenset({"/health", "/ready", "/health/live", "/health/ready", "/metrics"})
UPLOAD_PATH_RE = re.compile(r"^/api/v1/policies(/upload|/[0-9a-f-]{36}/version)$")
# Lab multipart uploads (datasets, artifacts, knowledge documents) use MAX_LAB_UPLOAD_BYTES.
LAB_UPLOAD_PATH_RE = re.compile(
    r"^/api/v1/(datasets/[0-9a-f-]{36}/versions|artifacts(/[0-9a-f-]{36}/versions)?|knowledge/documents)$"
)


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
        scope.setdefault("state", {})["request_id"] = request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = time.perf_counter()
        status_holder: dict[str, int] = {"status": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                raw = list(message.get("headers", []))
                raw.append((b"x-request-id", request_id.encode()))
                message["headers"] = raw
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            path = scope.get("path", "")
            if path not in _QUIET_PATHS:
                log.info(
                    "request",
                    method=scope.get("method"),
                    path=path,
                    status=status_holder["status"],
                    latency_ms=round((time.perf_counter() - started) * 1000, 1),
                    **_principal_fields(scope),
                )


def _principal_fields(scope: Scope) -> dict[str, str]:
    """Tenant/user of the authenticated principal (set by ``get_current_principal``) for the access log."""
    principal: Any = scope.get("state", {}).get("principal")
    if principal is None:
        return {}
    fields = {"tenant_id": str(principal.organization_id)}
    user_id = getattr(principal, "user_id", None)
    if user_id is not None and getattr(user_id, "int", 1) != 0:
        fields["user_id"] = str(user_id)
    return fields


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


class BodySizeLimitMiddleware:
    """Rejects bodies above the configured limit (uploads have their own, larger limit)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        settings = get_settings()
        self.default_limit = settings.max_request_bytes
        self.upload_limit = settings.max_upload_bytes + 64 * 1024
        self.lab_upload_limit = settings.max_lab_upload_bytes + 64 * 1024

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") in ("GET", "HEAD", "OPTIONS"):
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if LAB_UPLOAD_PATH_RE.match(path):
            limit = self.lab_upload_limit
        elif UPLOAD_PATH_RE.match(path):
            limit = self.upload_limit
        else:
            limit = self.default_limit
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
