"""Structured logging (structlog). Secrets are redacted before any log line is emitted."""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

import structlog

_SENSITIVE_KEY = re.compile(
    r"(pass(word)?|secret|token|api[_-]?key|authorization|cookie|credential|private[_-]?key)", re.I
)
_BEARER = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-~+/=]+", re.I)
_KEY_LIKE = re.compile(r"\b(aeg_(?:live|test)_|aegs_|sk-|AIza|ghp_|xox[bp]-)[A-Za-z0-9_\-]{6,}")


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        value = _BEARER.sub(r"\1[REDACTED]", value)
        return _KEY_LIKE.sub(lambda m: m.group(1) + "[REDACTED]", value)
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if _SENSITIVE_KEY.search(str(k)) else _redact_value(v)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_redact_value(v) for v in value]
    return value


def redact_processor(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    return {
        k: ("[REDACTED]" if _SENSITIVE_KEY.search(k) and k != "event" else _redact_value(v))
        for k, v in event_dict.items()
    }


def configure_logging(level: str = "INFO", json_logs: bool = False) -> None:
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        timestamper,
        redact_processor,
    ]
    renderer: Any = structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer(colors=False)
    structlog.configure(
        processors=[*shared, structlog.processors.format_exc_info, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=False,
    )
    logging.basicConfig(level=level.upper(), stream=sys.stdout, format="%(levelname)s %(name)s %(message)s")
    for noisy in ("uvicorn.access", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def redact_data(value: Any) -> Any:
    """Redact secrets from a structure before it is persisted (tool arguments, request snapshots)."""
    return _redact_value(value)
