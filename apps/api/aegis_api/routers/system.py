"""Operational endpoints: liveness, dependency-aware readiness, Prometheus metrics and system info."""

from __future__ import annotations

import hmac
import time
from typing import Any

from fastapi import APIRouter, Header, Response
from fastapi.responses import JSONResponse
from sqlalchemy import text

from aegis_api.config import get_settings
from aegis_api.db.session import get_engine
from aegis_api.errors import Unauthorized
from aegis_api.infrastructure.observability import metrics

router = APIRouter(tags=["Health"])


@router.get("/health/live")
def live() -> dict[str, str]:
    """Liveness: the process is up and serving (no dependency checks — never restart-loop on a DB outage)."""
    return {"status": "alive", "version": get_settings().app_version}


def _check(name: str, fn: Any) -> tuple[str, dict[str, Any]]:
    started = time.perf_counter()
    try:
        ok, detail = fn()
    except Exception as exc:
        ok, detail = False, f"{type(exc).__name__}"
    return name, {
        "ok": bool(ok),
        "detail": str(detail)[:200],
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
    }


def _postgres() -> tuple[bool, str]:
    with get_engine().connect() as conn:
        conn.execute(text("select 1"))
        conn.execute(text("select 1 from lab.workflow_runs limit 1"))
    return True, "reachable; lab schema present"


def _redis() -> tuple[bool, str]:
    from aegis_api.infrastructure.eventbus import get_event_bus

    return get_event_bus().health()


def _temporal() -> tuple[bool, str]:
    from aegis_api.workflows import temporal

    return temporal.health()


def _storage() -> tuple[bool, str]:
    from aegis_api.infrastructure.storage import get_storage

    return get_storage().health()


def _execution() -> tuple[bool, str]:
    from aegis_api.infrastructure.execution.registry import get_backend

    return get_backend().health()


def _providers() -> tuple[bool, str]:
    from aegis_api.infrastructure.llm.router import get_registry

    registry = get_registry()
    configured = sorted(registry.providers)
    return (bool(configured), "configured: " + (", ".join(configured) or "none"))


@router.get("/health/ready")
def ready_detailed() -> JSONResponse:
    """Readiness: required dependencies (PostgreSQL, object storage, Temporal when it is the engine, Redis when
    configured) must be healthy; optional ones (execution backend, model providers) are reported only."""
    settings = get_settings()
    required = [("postgres", _postgres), ("object_storage", _storage)]
    optional = [("model_providers", _providers)]
    if settings.effective_workflow_engine == "temporal":
        required.append(("temporal", _temporal))
    if settings.redis_url:
        required.append(("redis", _redis))
    if settings.execution_backend != "disabled":
        optional.append(("execution_backend", _execution))
    checks = dict(_check(n, f) for n, f in required + optional)
    ok = all(checks[n]["ok"] for n, _ in required)
    body = {
        "status": "ready" if ok else "not_ready",
        "workflow_engine": settings.effective_workflow_engine,
        "checks": checks,
        "required": [n for n, _ in required],
    }
    return JSONResponse(body, status_code=200 if ok else 503)


@router.get("/metrics", include_in_schema=False)
def prometheus_metrics(authorization: str | None = Header(default=None)) -> Response:
    settings = get_settings()
    if not settings.metrics_enabled:
        return Response(status_code=404)
    if settings.metrics_token:
        expected = f"Bearer {settings.metrics_token}"
        if not authorization or not hmac.compare_digest(authorization, expected):
            raise Unauthorized("Metrics require the configured bearer token")
    body, content_type = metrics.render()
    return Response(content=body, media_type=content_type)


@router.get("/api/v1/system/info")
def system_info() -> dict[str, Any]:
    s = get_settings()
    return {
        "service": s.app_name,
        "version": s.app_version,
        "environment": s.environment,
        "workflow_engine": s.effective_workflow_engine,
        "execution_backend": s.execution_backend,
        "object_storage": s.object_storage_backend,
        "features": s.feature_defaults(),
        "disclaimer": "Aegis provides evidence-backed research automation and compliance *assessments*; it does "
        "not certify results or guarantee regulatory compliance.",
    }
