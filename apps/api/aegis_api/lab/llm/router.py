"""HTTP API for models (Tag "Models"): provider catalog, routing configuration, route preview, model usage.

``router`` also mounts the prompt registry API (``/api/v1/prompts``, Tag "Agents").
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from aegis_api.deps import get_db
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.deps import require_actor
from aegis_api.lab.core.pagination import CursorPage, CursorParams, cursor_params, paginate
from aegis_api.lab.llm import service
from aegis_api.lab.llm.gateway import get_gateway
from aegis_api.lab.llm.schemas import (
    ModelCatalogOut,
    ModelConfigCreate,
    ModelConfigOut,
    ModelConfigUpdate,
    ModelUsageOut,
    RouteDecision,
    RoutePreviewRequest,
    UsageSummaryOut,
)
from aegis_api.lab.prompts.router import router as prompts_router
from aegis_api.schemas.common import Page, PageParams

models_router = APIRouter(prefix="/api/v1", tags=["Models"])

_E403: dict[str, Any] = {"description": "Missing permission"}
_E404: dict[str, Any] = {"description": "Not found (or owned by another organization)"}
_E409: dict[str, Any] = {"description": "A configuration for this provider/model/tier already exists"}
_E422: dict[str, Any] = {"description": "Validation error"}


@models_router.get(
    "/models",
    response_model=ModelCatalogOut,
    status_code=status.HTTP_200_OK,
    summary="Model catalog",
    description="Configured providers (booleans only — never credentials), their capabilities, whether the "
    "organization's data-processing consent permits them, the tier → model mapping used for routing and the "
    "default tier of every task type.",
    responses={403: _E403},
)
def model_catalog(actor: Actor = Depends(require_actor("model:read"))) -> ModelCatalogOut:
    return get_gateway().catalog(actor.organization_id)


@models_router.get(
    "/models/configs",
    response_model=Page[ModelConfigOut],
    status_code=status.HTTP_200_OK,
    summary="List model configurations",
    description="Organization routing overrides and platform-wide rows (read-only), ordered by tier and priority.",
    responses={403: _E403},
)
def list_model_configs(
    provider_kind: str | None = Query(None, max_length=24),
    tier: str | None = Query(None, max_length=24),
    enabled: bool | None = Query(None),
    params: PageParams = Depends(),
    actor: Actor = Depends(require_actor("model:read")),
    db: Session = Depends(get_db),
) -> Page[ModelConfigOut]:
    stmt = service.list_configs(db, actor, provider_kind=provider_kind, tier=tier, enabled=enabled)
    return paginate(db, stmt, params, service.config_out)


@models_router.post(
    "/models/configs",
    response_model=ModelConfigOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a model configuration",
    description="Adds an organization routing entry (provider, model id, tier, task types, priority, prices, "
    "capability overrides). Model ids are configuration, never code. Requires `model:manage`; audited.",
    responses={403: _E403, 409: _E409, 422: _E422},
)
def create_model_config(
    payload: ModelConfigCreate,
    actor: Actor = Depends(require_actor("model:manage")),
    db: Session = Depends(get_db),
) -> ModelConfigOut:
    return service.config_out(service.create_config(db, actor, payload))


@models_router.get(
    "/models/configs/{config_id}",
    response_model=ModelConfigOut,
    status_code=status.HTTP_200_OK,
    summary="Get a model configuration",
    responses={403: _E403, 404: _E404},
)
def get_model_config(
    config_id: uuid.UUID,
    actor: Actor = Depends(require_actor("model:read")),
    db: Session = Depends(get_db),
) -> ModelConfigOut:
    """One organization or platform-wide model configuration row."""
    return service.config_out(service.get_config(db, actor, config_id))


@models_router.patch(
    "/models/configs/{config_id}",
    response_model=ModelConfigOut,
    status_code=status.HTTP_200_OK,
    summary="Update a model configuration",
    description="Partial update of an organization row (platform rows cannot be changed). Requires `model:manage`; "
    "audited with before/after values.",
    responses={403: _E403, 404: _E404, 422: _E422},
)
def update_model_config(
    config_id: uuid.UUID,
    payload: ModelConfigUpdate,
    actor: Actor = Depends(require_actor("model:manage")),
    db: Session = Depends(get_db),
) -> ModelConfigOut:
    return service.config_out(service.update_config(db, actor, config_id, payload))


@models_router.delete(
    "/models/configs/{config_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete a model configuration",
    description="Removes an organization routing entry. Requires `model:manage`; audited.",
    responses={403: _E403, 404: _E404},
)
def delete_model_config(
    config_id: uuid.UUID,
    actor: Actor = Depends(require_actor("model:manage")),
    db: Session = Depends(get_db),
) -> Response:
    service.delete_config(db, actor, config_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@models_router.post(
    "/models/route-preview",
    response_model=RouteDecision,
    status_code=status.HTTP_200_OK,
    summary="Preview model routing",
    description="Dry-runs the model router for a task type, complexity, budgets and required capabilities using "
    "this organization's configuration and consent. No model is called and nothing is recorded.",
    responses={403: _E403, 422: _E422},
)
def route_preview(
    payload: RoutePreviewRequest,
    actor: Actor = Depends(require_actor("model:read")),
) -> RouteDecision:
    return get_gateway().route_preview(actor.organization_id, payload)


@models_router.get(
    "/models/usage",
    response_model=CursorPage[ModelUsageOut],
    status_code=status.HTTP_200_OK,
    summary="List model usage",
    description="The organization's append-only model-call ledger (successes and failures), newest first, with "
    "keyset pagination. Costs are only non-zero when a price is configured (`cost_basis` explains).",
    responses={403: _E403, 422: _E422},
)
def list_model_usage(
    provider: str | None = Query(None, max_length=32),
    model: str | None = Query(None, max_length=160),
    task_type: str | None = Query(None, max_length=48),
    success: bool | None = Query(None),
    project_id: uuid.UUID | None = Query(None),
    mission_id: uuid.UUID | None = Query(None),
    agent_run_id: uuid.UUID | None = Query(None),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    params: CursorParams = Depends(cursor_params),
    actor: Actor = Depends(require_actor("usage:read")),
    db: Session = Depends(get_db),
) -> CursorPage[ModelUsageOut]:
    return service.list_usage(
        db,
        actor,
        params,
        provider=provider,
        model=model,
        task_type=task_type,
        success=success,
        project_id=project_id,
        mission_id=mission_id,
        agent_run_id=agent_run_id,
        since=since,
        until=until,
    )


@models_router.get(
    "/models/usage/summary",
    response_model=UsageSummaryOut,
    status_code=status.HTTP_200_OK,
    summary="Summarize model usage",
    description="Calls, failures, tokens (input, output, cached, thinking), cost and average latency grouped by "
    "provider / model / task type for a date range (default: the current calendar month, UTC).",
    responses={403: _E403, 422: _E422},
)
def model_usage_summary(
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    group_by: list[str] | None = Query(None, description="Any of provider, model, task_type (repeatable)"),
    actor: Actor = Depends(require_actor("usage:read")),
    db: Session = Depends(get_db),
) -> UsageSummaryOut:
    return service.usage_summary(db, actor, since=since, until=until, group_by=group_by)


# Mounted here (rather than in ``aegis_api.lab.router``) so the prompt API ships with the model gateway;
# ``router`` itself carries no tag so the prompt endpoints are documented under "Agents" only.
router = APIRouter()
router.include_router(models_router)
router.include_router(prompts_router, prefix="/api/v1")
