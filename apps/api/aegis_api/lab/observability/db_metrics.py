"""Database statement metrics (and spans when tracing is configured).

``install_db_metrics()`` registers global SQLAlchemy ``Engine`` listeners, so every engine — the RLS app
engine, the owner engine, engines created later — is measured. Statement latency is recorded in
``DB_QUERY_LATENCY{operation}`` where ``operation`` is one of select/insert/update/delete/other. A CLIENT
span per statement is started only when tracing is configured; it carries ``db.system`` and
``db.operation`` and never the SQL text or bound parameters (they may contain tenant data or secrets).
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode
from sqlalchemy import event
from sqlalchemy.engine import Engine

from aegis_api.lab.observability.metrics import DB_QUERY_LATENCY
from aegis_api.lab.observability.telemetry import tracing_configured

TRACER_NAME = "aegis.db"
_T0_ATTR = "_aegis_stmt_t0"
_SPAN_ATTR = "_aegis_stmt_span"
_OP_ATTR = "_aegis_stmt_op"
_LEADING_NOISE = re.compile(r"^(?:\s+|--[^\n]*\n|/\*.*?\*/)+", re.S)
_WRITE_VERB = re.compile(r"\b(insert|update|delete)\b", re.I)
_OPERATIONS = frozenset({"select", "insert", "update", "delete"})

_install_lock = threading.Lock()
_installed = False


def classify_statement(statement: str) -> str:
    """Bounded operation label for a SQL statement."""
    text = _LEADING_NOISE.sub("", statement or "", count=1)
    head = text[:16].split(None, 1)
    verb = head[0].lower().rstrip("(") if head else ""
    if verb in _OPERATIONS:
        return verb
    if verb == "with":  # CTE: a data-modifying CTE is a write, otherwise a read
        match = _WRITE_VERB.search(text)
        return match.group(1).lower() if match else "select"
    return "other"


def _before_cursor_execute(
    conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool
) -> None:
    if context is None:
        return
    operation = classify_statement(statement)
    setattr(context, _OP_ATTR, operation)
    setattr(context, _T0_ATTR, time.perf_counter())
    if tracing_configured():
        span = trace.get_tracer(TRACER_NAME).start_span(
            f"db.{operation}",
            kind=SpanKind.CLIENT,
            attributes={"db.system": "postgresql", "db.operation": operation},
        )
        setattr(context, _SPAN_ATTR, span)


def _finish(context: Any, error: BaseException | None = None) -> None:
    if context is None:
        return
    started = getattr(context, _T0_ATTR, None)
    if started is None:
        return
    setattr(context, _T0_ATTR, None)
    operation = getattr(context, _OP_ATTR, "other")
    DB_QUERY_LATENCY.labels(operation).observe(time.perf_counter() - started)
    span = getattr(context, _SPAN_ATTR, None)
    if span is not None:
        setattr(context, _SPAN_ATTR, None)
        if error is not None:
            span.set_status(Status(StatusCode.ERROR, type(error).__name__))
        span.end()


def _after_cursor_execute(
    conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool
) -> None:
    _finish(context)


def _handle_error(exception_context: Any) -> None:
    _finish(getattr(exception_context, "execution_context", None), exception_context.original_exception)


def install_db_metrics() -> None:
    """Register the global engine listeners once per process (idempotent)."""
    global _installed
    with _install_lock:
        if _installed:
            return
        event.listen(Engine, "before_cursor_execute", _before_cursor_execute)
        event.listen(Engine, "after_cursor_execute", _after_cursor_execute)
        event.listen(Engine, "handle_error", _handle_error)
        _installed = True


def uninstall_db_metrics() -> None:
    """Remove the listeners (tests)."""
    global _installed
    with _install_lock:
        if not _installed:
            return
        for name, fn in (
            ("before_cursor_execute", _before_cursor_execute),
            ("after_cursor_execute", _after_cursor_execute),
            ("handle_error", _handle_error),
        ):
            if event.contains(Engine, name, fn):
                event.remove(Engine, name, fn)
        _installed = False
