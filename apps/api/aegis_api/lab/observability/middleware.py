"""HTTP telemetry: one SERVER span + RED metrics per request (pure ASGI, streaming-safe).

* The incoming W3C ``traceparent`` is honoured (``opentelemetry.propagate.extract``).
* The span name and the ``route`` metric label use the matched route *template*
  (``/api/v1/missions/{mission_id}``), never the raw path, so cardinality stays bounded; requests that
  match no route are labelled ``unmatched``.
* The trace id is exposed as ``request.state.trace_id``, bound into structlog contextvars and returned in
  the ``X-Trace-ID`` response header.
* Messages are forwarded as they are produced — responses (SSE in particular) are never buffered.
"""

from __future__ import annotations

import time
from typing import Any

import structlog
from opentelemetry import propagate, trace
from opentelemetry.trace import SpanKind, Status, StatusCode
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from aegis_api.lab.observability.metrics import HTTP_LATENCY, HTTP_REQUESTS

TRACER_NAME = "aegis.http"
UNMATCHED_ROUTE = "unmatched"
# Probes and scrapes are measured but not traced (they would dominate trace volume).
UNTRACED_PATHS = frozenset({"/health", "/ready", "/health/live", "/health/ready", "/metrics"})
# Methods are client-controlled: anything non-standard is folded into one label value.
KNOWN_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"})


def route_template(scope: Scope) -> str:
    """The matched route template, or ``unmatched``."""
    route = scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str) and path:
        return path
    return UNMATCHED_ROUTE


def _carrier(scope: Scope) -> dict[str, str]:
    carrier: dict[str, str] = {}
    for key, value in scope.get("headers", []):
        name = key.decode("latin-1").lower()
        if name in ("traceparent", "tracestate"):
            carrier[name] = value.decode("latin-1")
    return carrier


def _principal_org(scope: Scope) -> str | None:
    principal: Any = scope.get("state", {}).get("principal")
    org = getattr(principal, "organization_id", None)
    return str(org) if org is not None else None


class TelemetryMiddleware:
    """Creates the request SERVER span, records ``HTTP_REQUESTS``/``HTTP_LATENCY`` and sets ``X-Trace-ID``."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        raw_method = str(scope.get("method", "GET")).upper()
        method = raw_method if raw_method in KNOWN_METHODS else "OTHER"
        path: str = scope.get("path", "")
        started = time.perf_counter()
        status_holder = {"status": 500}

        if path in UNTRACED_PATHS:

            async def send_status(message: Message) -> None:
                if message["type"] == "http.response.start":
                    status_holder["status"] = message["status"]
                await send(message)

            try:
                await self.app(scope, receive, send_status)
            finally:
                self._record(method, route_template(scope), status_holder["status"], started)
            return

        parent = propagate.extract(_carrier(scope))
        tracer = trace.get_tracer(TRACER_NAME)
        with tracer.start_as_current_span(
            f"HTTP {method}",
            context=parent,
            kind=SpanKind.SERVER,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            span_context = span.get_span_context()
            trace_id = format(span_context.trace_id, "032x") if span_context.is_valid else None
            if trace_id:
                scope.setdefault("state", {})["trace_id"] = trace_id
                structlog.contextvars.bind_contextvars(trace_id=trace_id)
            if span.is_recording():
                span.set_attribute("http.request.method", method)
                request_id = scope.get("state", {}).get("request_id")
                if request_id:
                    span.set_attribute("aegis.request_id", str(request_id))

            async def send_wrapper(message: Message) -> None:
                if message["type"] == "http.response.start":
                    status_holder["status"] = message["status"]
                    if trace_id:
                        headers = list(message.get("headers", []))
                        headers.append((b"x-trace-id", trace_id.encode()))
                        message["headers"] = headers
                await send(message)

            try:
                await self.app(scope, receive, send_wrapper)
            except Exception as exc:
                if span.is_recording():
                    span.record_exception(exc)
                    span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                raise
            finally:
                template = route_template(scope)
                status = status_holder["status"]
                span.update_name(f"HTTP {method} {template}")
                if span.is_recording():
                    span.set_attribute("http.route", template)
                    span.set_attribute("http.response.status_code", status)
                    org = _principal_org(scope)
                    if org:
                        span.set_attribute("aegis.organization_id", org)
                    if status >= 500:
                        span.set_status(Status(StatusCode.ERROR))
                self._record(method, template, status, started)

    @staticmethod
    def _record(method: str, route: str, status: int, started: float) -> None:
        elapsed = time.perf_counter() - started
        HTTP_REQUESTS.labels(method, route, str(status)).inc()
        HTTP_LATENCY.labels(method, route).observe(elapsed)
