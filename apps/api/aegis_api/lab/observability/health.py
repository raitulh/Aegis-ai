"""Liveness and readiness checks.

* Liveness never touches dependencies: it only proves the process can serve a request.
* Readiness checks each dependency with its own timeout, concurrently, and caches the result for a
  few seconds so frequent probes cannot overload the database. Results carry a status and, on failure,
  only the exception *class name* — never hosts, credentials, messages or stack traces.

Required checks: database, object storage, Redis (when configured) and Temporal (when it is the
workflow engine). Model providers are informational ("configured"/"not_configured", no network).
"""

from __future__ import annotations

import importlib
import socket
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from sqlalchemy import text

from aegis_api.config import get_settings
from aegis_api.lab.observability.metrics import DEPENDENCY_UP

CheckStatus = Literal["ok", "error", "timeout", "unknown", "not_configured"]
CACHE_SECONDS = 5.0
DEFAULT_TIMEOUT_SECONDS = 2.0
TEMPORAL_CONNECT_TIMEOUT_SECONDS = 1.0

_STARTED_MONOTONIC = time.monotonic()
_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="aegis-health")
_cache_lock = threading.Lock()
_cache: tuple[float, Readiness] | None = None


@dataclass(frozen=True)
class CheckResult:
    status: CheckStatus
    required: bool
    latency_ms: float | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"status": self.status, "required": self.required}
        if self.latency_ms is not None:
            out["latency_ms"] = self.latency_ms
        if self.error:
            out["error"] = self.error
        return out


@dataclass(frozen=True)
class Readiness:
    ready: bool
    checks: dict[str, CheckResult]
    providers: dict[str, str]
    checked_at: datetime
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        settings = get_settings()
        return {
            "status": "ready" if self.ready else "not_ready",
            "service": settings.otel_service_name,
            "version": settings.app_version,
            "checks": {name: result.as_dict() for name, result in self.checks.items()},
            "providers": dict(self.providers),
            "checked_at": self.checked_at.isoformat(),
        }


class CheckUnknown(Exception):
    """The dependency's health cannot be determined (e.g. the adapter is not installed)."""


def uptime_seconds() -> float:
    return round(time.monotonic() - _STARTED_MONOTONIC, 3)


def liveness() -> dict[str, Any]:
    settings = get_settings()
    return {
        "status": "ok",
        "service": settings.otel_service_name,
        "version": settings.app_version,
        "uptime_seconds": uptime_seconds(),
    }


# --- individual checks (raise on failure; return normally when healthy) ------------------------------
def _check_database() -> None:
    from aegis_api.db.session import get_engine

    with get_engine().connect() as conn:
        conn.execute(text("select 1"))


def _check_redis() -> None:
    import redis

    client = redis.Redis.from_url(
        get_settings().redis_url or "", socket_connect_timeout=1, socket_timeout=1, health_check_interval=0
    )
    try:
        if not client.ping():
            raise ConnectionError("ping failed")
    finally:
        client.close()


def parse_host_port(address: str, default_port: int) -> tuple[str, int]:
    """``host:port`` / ``[v6]:port`` / ``scheme://host:port`` → (host, port)."""
    raw = address.strip()
    parts = urlsplit(raw if "://" in raw else f"//{raw}")
    host = parts.hostname
    if not host:
        raise ValueError("invalid address")
    return host, parts.port or default_port


def _check_temporal() -> None:
    address = get_settings().temporal_address
    if not address:
        raise LookupError("temporal address is not configured")
    host, port = parse_host_port(address, 7233)
    with socket.create_connection((host, port), timeout=TEMPORAL_CONNECT_TIMEOUT_SECONDS):
        pass


def _check_storage() -> None:
    try:
        module = importlib.import_module("aegis_api.lab.storage")
    except ImportError as exc:
        raise CheckUnknown("storage adapter unavailable") from exc
    get_storage = getattr(module, "get_storage", None)
    if get_storage is None:
        raise CheckUnknown("storage adapter unavailable")
    if not get_storage().health():
        raise ConnectionError("object storage health check failed")


def provider_status() -> dict[str, str]:
    settings = get_settings()
    configured = {
        "gemini": bool(settings.gemini_api_key),
        "openai": bool(settings.openai_api_key),
        "anthropic": bool(settings.anthropic_api_key),
        "ollama": bool(settings.ollama_base_url),
    }
    return {name: "configured" if ok else "not_configured" for name, ok in configured.items()}


def _planned_checks() -> list[tuple[str, Callable[[], None] | None, bool, float]]:
    """(name, check or None when not configured, required, timeout). Resolved at call time."""
    settings = get_settings()
    redis_configured = bool(settings.redis_url)
    temporal_configured = settings.effective_workflow_engine == "temporal"
    return [
        ("database", _check_database, True, DEFAULT_TIMEOUT_SECONDS),
        ("storage", _check_storage, True, DEFAULT_TIMEOUT_SECONDS),
        ("redis", _check_redis if redis_configured else None, redis_configured, DEFAULT_TIMEOUT_SECONDS),
        (
            "temporal",
            _check_temporal if temporal_configured else None,
            temporal_configured,
            TEMPORAL_CONNECT_TIMEOUT_SECONDS + 0.5,
        ),
    ]


def _timed(check: Callable[[], None]) -> float:
    started = time.perf_counter()
    check()
    return round((time.perf_counter() - started) * 1000, 2)


def run_checks() -> Readiness:
    """Run all checks concurrently (each with its own timeout); no caching."""
    planned = _planned_checks()
    futures = {name: _executor.submit(_timed, check) for name, check, _, _ in planned if check is not None}
    deadline_base = time.monotonic()
    results: dict[str, CheckResult] = {}
    for name, check, required, timeout in planned:
        if check is None:
            results[name] = CheckResult(status="not_configured", required=False)
            continue
        remaining = max(0.0, timeout - (time.monotonic() - deadline_base))
        try:
            latency = futures[name].result(timeout=remaining)
            results[name] = CheckResult(status="ok", required=required, latency_ms=latency)
        except FutureTimeout:
            results[name] = CheckResult(status="timeout", required=required, error="Timeout")
        except CheckUnknown:
            results[name] = CheckResult(status="unknown", required=required)
        except Exception as exc:
            results[name] = CheckResult(status="error", required=required, error=type(exc).__name__)
    for name, result in results.items():
        if result.status != "not_configured":
            DEPENDENCY_UP.labels(name).set(1 if result.status == "ok" else 0)
    # "unknown" means the component is not part of this deployment; it does not fail readiness.
    ready = all(r.status in ("ok", "unknown") for r in results.values() if r.required)
    return Readiness(ready=ready, checks=results, providers=provider_status(), checked_at=datetime.now(UTC))


def readiness(*, use_cache: bool = True) -> Readiness:
    """Readiness with a short cache; concurrent callers share one evaluation."""
    global _cache
    with _cache_lock:
        now = time.monotonic()
        if use_cache and _cache is not None and now - _cache[0] < CACHE_SECONDS:
            return _cache[1]
        result = run_checks()
        _cache = (time.monotonic(), result)
        return result


def reset_readiness_cache() -> None:
    global _cache
    with _cache_lock:
        _cache = None
