"""Liveness and readiness endpoints."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from aegis_api.config import get_settings
from aegis_api.db.session import get_engine

router = APIRouter(tags=["Health"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "aegis-api", "version": get_settings().app_version}


@router.get("/ready")
def ready() -> dict[str, object]:
    checks: dict[str, str] = {}
    ok = True
    try:
        with get_engine().connect() as conn:
            conn.execute(text("select 1"))
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {type(exc).__name__}"
        ok = False
    settings = get_settings()
    if settings.redis_url:
        try:
            import redis

            redis.Redis.from_url(settings.redis_url, socket_connect_timeout=1).ping()
            checks["redis"] = "ok"
        except Exception as exc:
            checks["redis"] = f"error: {type(exc).__name__}"
    checks["job_backend"] = settings.effective_job_backend
    return {"status": "ready" if ok else "degraded", "checks": checks}
