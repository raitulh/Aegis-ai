"""Liveness, readiness and health endpoints.

* ``/live``   — the process is up (no dependencies checked). Use for liveness probes.
* ``/ready``  — the instance can serve traffic: database reachable and migrated, Redis reachable when
  configured. Returns **503** otherwise so load balancers stop routing to it. Use for readiness probes.
* ``/health`` — backwards-compatible alias of ``/live`` with the service version.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import text

from aegis_api.config import get_settings
from aegis_api.db.session import get_engine

router = APIRouter(tags=["Health"])

REQUIRED_REVISION = "0004"
_draining = False


def set_draining(value: bool) -> None:
    """Called on shutdown so readiness fails while in-flight requests complete (connection draining)."""
    global _draining
    _draining = value


@router.get("/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "aegis-api", "version": get_settings().app_version}


@router.get("/ready")
def ready() -> JSONResponse:
    checks: dict[str, object] = {}
    ok = not _draining
    if _draining:
        checks["draining"] = True
    started = time.perf_counter()
    try:
        with get_engine().connect() as conn:
            conn.execute(text("select 1"))
            revision = conn.execute(text("select version_num from alembic_version")).scalar()
        checks["database"] = "ok"
        checks["database_latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
        checks["schema_revision"] = revision
        if revision is None or str(revision) < REQUIRED_REVISION:
            checks["schema"] = f"migration required (have {revision}, need >= {REQUIRED_REVISION})"
            ok = False
    except Exception as exc:
        checks["database"] = f"error: {type(exc).__name__}"
        ok = False
    settings = get_settings()
    if settings.redis_url:
        try:
            import redis

            started = time.perf_counter()
            redis.Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1).ping()
            checks["redis"] = "ok"
            checks["redis_latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
        except Exception as exc:
            checks["redis"] = f"error: {type(exc).__name__}"
            # Redis is required only when it backs the job queue.
            if settings.effective_job_backend == "celery":
                ok = False
    checks["job_backend"] = settings.effective_job_backend
    return JSONResponse({"status": "ready" if ok else "not_ready", "checks": checks}, status_code=200 if ok else 503)


@router.get("/metrics", include_in_schema=False)
def prometheus_metrics(request: Request) -> Response:
    """Prometheus exposition. Requires ``Authorization: Bearer <METRICS_TOKEN>`` when a token is configured."""
    import hmac

    from aegis_api.observability import metrics
    from aegis_api.realtime import hub

    settings = get_settings()
    if not settings.metrics_enabled:
        return Response(status_code=404)
    if settings.metrics_token:
        supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(supplied.encode(), settings.metrics_token.encode()):
            return Response(status_code=401)
    metrics.SSE_CONNECTIONS.set(hub.connections)
    _sample_queue_depth(metrics)
    return Response(metrics.render(), media_type="text/plain; version=0.0.4")


def _sample_queue_depth(metrics: Any) -> None:
    try:
        from aegis_api.db.session import get_admin_engine

        with get_admin_engine().connect() as conn:
            rows = conn.execute(
                text(
                    "select status, count(*) from job_runs where status in ('queued','running','retrying') group by status"
                )
            ).all()
        for status in ("queued", "running", "retrying"):
            metrics.QUEUE_DEPTH.labels(status).set(0)
        for status, count in rows:
            metrics.QUEUE_DEPTH.labels(status).set(count)
    except Exception:  # pragma: no cover - metrics must never fail the scrape
        return
