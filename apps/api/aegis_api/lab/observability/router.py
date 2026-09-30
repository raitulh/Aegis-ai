"""System endpoints: liveness, readiness, Prometheus metrics and platform info.

``/health/live``, ``/health/ready`` and ``/metrics`` live at the root (probe/scrape conventions); the
authenticated platform info endpoint lives under ``/api/v1``. The core ``/health`` and ``/ready`` routes
are unchanged.
"""

from __future__ import annotations

import asyncio
import hmac
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_PLAIN_0_0_4, generate_latest
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.deps import get_db
from aegis_api.errors import NotFound, Unauthorized
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.deps import get_actor
from aegis_api.lab.core.features import all_features
from aegis_api.lab.observability import health
from aegis_api.lab.observability.metrics import REGISTRY, register_runtime_collectors
from aegis_api.lab.observability.telemetry import tracing_configured

router = APIRouter(tags=["System"])
register_runtime_collectors()

_ERROR = {"description": "Error envelope", "content": {"application/json": {}}}


class LivenessOut(BaseModel):
    status: str = Field(description="Always `ok` when the process can serve requests")
    service: str
    version: str
    uptime_seconds: float


class CheckOut(BaseModel):
    status: str = Field(description="ok | error | timeout | unknown | not_configured")
    required: bool
    latency_ms: float | None = None
    error: str | None = Field(default=None, description="Exception class name only")


class ReadinessOut(BaseModel):
    status: str = Field(description="`ready` or `not_ready`")
    service: str
    version: str
    checks: dict[str, CheckOut]
    providers: dict[str, str] = Field(description="Model provider configuration (no network check)")
    checked_at: datetime


class SystemInfoOut(BaseModel):
    version: str
    environment: str
    workflow_engine: str
    execution_backend: str
    event_bus: str
    storage_backend: str
    tracing_enabled: bool
    features: dict[str, bool] = Field(description="Effective feature flags for the caller's organization")


@router.get(
    "/health/live",
    response_model=LivenessOut,
    status_code=200,
    summary="Liveness probe",
    description="Process liveness. Performs no dependency checks.",
)
def live() -> dict[str, Any]:
    return health.liveness()


@router.get(
    "/health/ready",
    response_model=ReadinessOut,
    status_code=200,
    summary="Readiness probe",
    description=(
        "Checks the database, object storage, Redis (when configured) and Temporal (when it is the workflow "
        "engine) with individual timeouts; results are cached for 5 seconds. Returns 503 with the same body "
        "when a required dependency is unhealthy. Errors expose only the exception class name."
    ),
    responses={503: {"model": ReadinessOut, "description": "A required dependency is unhealthy"}},
)
async def ready() -> JSONResponse:
    result = await asyncio.to_thread(health.readiness)
    body = ReadinessOut.model_validate(result.as_dict()).model_dump(mode="json")
    return JSONResponse(body, status_code=200 if result.ready else 503, headers={"Cache-Control": "no-store"})


def _authorize_scrape(request: Request) -> None:
    settings = get_settings()
    if not settings.metrics_enabled:
        raise NotFound("Not found")
    token = settings.metrics_token
    if not token:
        return
    header = request.headers.get("authorization", "")
    scheme, _, supplied = header.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(supplied.strip().encode(), token.encode()):
        raise Unauthorized("A valid metrics bearer token is required", code="invalid_metrics_token")


@router.get(
    "/metrics",
    status_code=200,
    summary="Prometheus metrics",
    description=(
        "Prometheus text exposition (version 0.0.4). Disabled (404) when METRICS_ENABLED=false; requires "
        "`Authorization: Bearer <METRICS_TOKEN>` when a token is configured."
    ),
    response_class=Response,
    responses={
        200: {"content": {CONTENT_TYPE_PLAIN_0_0_4: {}}, "description": "Metrics"},
        401: _ERROR,
        404: _ERROR,
    },
)
def metrics(request: Request) -> Response:
    _authorize_scrape(request)
    return Response(
        content=generate_latest(REGISTRY),
        media_type=CONTENT_TYPE_PLAIN_0_0_4,
        headers={"Cache-Control": "no-store"},
    )


@router.get(
    "/api/v1/system/info",
    response_model=SystemInfoOut,
    status_code=200,
    summary="Platform info",
    description="Version, environment, configured backends and the effective feature flags for your organization.",
    responses={401: _ERROR},
)
def system_info(actor: Actor = Depends(get_actor), db: Session = Depends(get_db)) -> SystemInfoOut:
    settings = get_settings()
    return SystemInfoOut(
        version=settings.app_version,
        environment=settings.environment,
        workflow_engine=settings.effective_workflow_engine,
        execution_backend=settings.execution_backend,
        event_bus=settings.effective_event_bus,
        storage_backend=settings.object_storage_backend,
        tracing_enabled=tracing_configured(),
        features=all_features(db, actor.organization_id),
    )
