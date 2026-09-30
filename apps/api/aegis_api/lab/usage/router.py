"""HTTP API for usage, cost aggregation and billing."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from aegis_api.deps import get_db
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.deps import require_actor
from aegis_api.lab.core.features import require_feature
from aegis_api.lab.core.idempotency import Idempotency, idempotency
from aegis_api.lab.core.pagination import CursorPage, CursorParams, cursor_params
from aegis_api.lab.usage import billing, service
from aegis_api.lab.usage.schemas import (
    BillingPlanOut,
    ComputeUsageOut,
    CostBreakdownOut,
    GroupBy,
    InvoiceOut,
    ModelUsageOut,
    PeriodIn,
    RollupOut,
    SubscriptionOut,
    SubscriptionUpdate,
    ToolCostsOut,
    UsageRecordOut,
    UsageSummaryOut,
)
from aegis_api.schemas.common import Page, PageParams

router = APIRouter(prefix="/api/v1", tags=["Usage"])

E401: dict[int | str, dict[str, Any]] = {401: {"description": "Authentication required"}}
E403: dict[int | str, dict[str, Any]] = {
    403: {"description": "Missing permission, feature disabled, or the action requires a signed-in human"}
}
E404: dict[int | str, dict[str, Any]] = {404: {"description": "Not found"}}
E422: dict[int | str, dict[str, Any]] = {422: {"description": "Validation error (e.g. invalid date range or period)"}}

Since = Query(None, description="Inclusive start (ISO-8601). Default: start of the current month (UTC)")
Until = Query(None, description="Exclusive end (ISO-8601). Default: start of next month, or now when `since` is set")
ProjectFilter = Query(None, description="Restrict to one project")


def page_params(
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    page_size: int = Query(25, ge=1, le=200, description="Items per page"),
) -> PageParams:
    return PageParams(page=page, page_size=page_size)


# ---------------------------------------------------------------------------------------------
# Usage & costs
# ---------------------------------------------------------------------------------------------
@router.get(
    "/usage/summary",
    summary="Usage summary",
    description="LLM, compute, tool and storage totals (cost, tokens, calls, seconds, bytes) for a period.",
    response_model=UsageSummaryOut,
    status_code=status.HTTP_200_OK,
    responses={**E401, **E403, **E404, **E422},
)
def usage_summary(
    since: datetime | None = Since,
    until: datetime | None = Until,
    project_id: uuid.UUID | None = ProjectFilter,
    actor: Actor = Depends(require_actor("usage:read")),
    db: Session = Depends(get_db),
) -> UsageSummaryOut:
    return service.usage_summary(db, actor, since=since, until=until, project_id=project_id)


@router.get(
    "/usage/costs",
    summary="Cost breakdown",
    description=(
        "Costs grouped by mission, experiment, discovery (experiment compute + an even share of the mission's "
        "LLM/tool cost), agent role, model, project or organization."
    ),
    response_model=CostBreakdownOut,
    status_code=status.HTTP_200_OK,
    responses={**E401, **E403, **E404, **E422},
)
def usage_costs(
    group_by: GroupBy = Query("mission", description="Grouping dimension"),
    since: datetime | None = Since,
    until: datetime | None = Until,
    project_id: uuid.UUID | None = ProjectFilter,
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of groups"),
    actor: Actor = Depends(require_actor("usage:read")),
    db: Session = Depends(get_db),
) -> CostBreakdownOut:
    return service.cost_breakdown(
        db, actor, group_by=group_by, since=since, until=until, project_id=project_id, limit=limit
    )


@router.get(
    "/usage/models",
    summary="Model usage ledger",
    description="Every routed model call (success or failure), newest first, with cursor pagination.",
    response_model=CursorPage[ModelUsageOut],
    status_code=status.HTTP_200_OK,
    responses={**E401, **E403, **E404, **E422},
)
def model_usage(
    since: datetime | None = Since,
    until: datetime | None = Until,
    project_id: uuid.UUID | None = ProjectFilter,
    mission_id: uuid.UUID | None = Query(None),
    agent_run_id: uuid.UUID | None = Query(None),
    provider: str | None = Query(None, max_length=32),
    model: str | None = Query(None, max_length=160),
    task_type: str | None = Query(None, max_length=48),
    success: bool | None = Query(None),
    params: CursorParams = Depends(cursor_params),
    actor: Actor = Depends(require_actor("usage:read")),
    db: Session = Depends(get_db),
) -> CursorPage[ModelUsageOut]:
    return service.list_model_usage(
        db,
        actor,
        params,
        since=since,
        until=until,
        project_id=project_id,
        mission_id=mission_id,
        agent_run_id=agent_run_id,
        provider=provider,
        model=model,
        task_type=task_type,
        success=success,
    )


@router.get(
    "/usage/compute",
    summary="Compute usage ledger",
    description="Measured resource usage of finished compute jobs, newest first, with cursor pagination.",
    response_model=CursorPage[ComputeUsageOut],
    status_code=status.HTTP_200_OK,
    responses={**E401, **E403, **E404, **E422},
)
def compute_usage(
    since: datetime | None = Since,
    until: datetime | None = Until,
    project_id: uuid.UUID | None = ProjectFilter,
    mission_id: uuid.UUID | None = Query(None),
    experiment_id: uuid.UUID | None = Query(None),
    backend: str | None = Query(None, max_length=24),
    params: CursorParams = Depends(cursor_params),
    actor: Actor = Depends(require_actor("usage:read")),
    db: Session = Depends(get_db),
) -> CursorPage[ComputeUsageOut]:
    return service.list_compute_usage(
        db,
        actor,
        params,
        since=since,
        until=until,
        project_id=project_id,
        mission_id=mission_id,
        experiment_id=experiment_id,
        backend=backend,
    )


@router.get(
    "/usage/tools",
    summary="Tool invocation costs",
    description="Invocations, outcomes, cost and average latency per tool for a period.",
    response_model=ToolCostsOut,
    status_code=status.HTTP_200_OK,
    responses={**E401, **E403, **E404, **E422},
)
def tool_usage(
    since: datetime | None = Since,
    until: datetime | None = Until,
    project_id: uuid.UUID | None = ProjectFilter,
    actor: Actor = Depends(require_actor("usage:read")),
    db: Session = Depends(get_db),
) -> ToolCostsOut:
    return service.tool_costs(db, actor, since=since, until=until, project_id=project_id)


# ---------------------------------------------------------------------------------------------
# Billing
# ---------------------------------------------------------------------------------------------
@router.get(
    "/billing/plans",
    summary="Billing plans",
    description="The active plan catalog with quotas and list prices.",
    response_model=list[BillingPlanOut],
    status_code=status.HTTP_200_OK,
    responses={**E401, **E403},
)
def plans(
    _: Actor = Depends(require_actor("billing:view")),
    db: Session = Depends(get_db),
) -> list[BillingPlanOut]:
    return billing.list_plans(db)


@router.get(
    "/billing/subscription",
    summary="Current subscription",
    description="The organization's active subscription (the free plan applies when none exists).",
    response_model=SubscriptionOut,
    status_code=status.HTTP_200_OK,
    responses={**E401, **E403},
)
def get_subscription(
    actor: Actor = Depends(require_actor("billing:view")),
    db: Session = Depends(get_db),
) -> SubscriptionOut:
    return billing.subscription_view(db, actor)


@router.put(
    "/billing/subscription",
    summary="Change the subscription plan",
    description="Switches to another plan (requires `billing:manage`, a signed-in human and the `billing` feature).",
    response_model=SubscriptionOut,
    status_code=status.HTTP_200_OK,
    responses={**E401, **E403, **E404, **E422},
    dependencies=[Depends(require_feature("billing"))],
)
def put_subscription(
    body: SubscriptionUpdate,
    actor: Actor = Depends(require_actor("billing:manage")),
    db: Session = Depends(get_db),
) -> SubscriptionOut:
    return billing.change_subscription(db, actor, body.plan_key)


@router.get(
    "/billing/usage-records",
    summary="Billing usage records",
    description="Per-meter usage aggregates by period (rolled up from the ledgers).",
    response_model=Page[UsageRecordOut],
    status_code=status.HTTP_200_OK,
    responses={**E401, **E403, **E422},
)
def usage_records(
    meter: str | None = Query(None, max_length=48),
    period: str | None = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="Calendar month 'YYYY-MM'"),
    params: PageParams = Depends(page_params),
    actor: Actor = Depends(require_actor("billing:view")),
    db: Session = Depends(get_db),
) -> Page[UsageRecordOut]:
    return billing.list_usage_records(db, actor, params, meter=meter, period=period)


@router.post(
    "/billing/rollup",
    summary="Roll up usage records",
    description="Idempotently (re)computes the period's usage records from the ledgers; finalized records never change.",
    response_model=RollupOut,
    status_code=status.HTTP_200_OK,
    responses={**E401, **E403, **E422},
    dependencies=[Depends(require_feature("billing"))],
)
def rollup(
    body: PeriodIn,
    actor: Actor = Depends(require_actor("billing:manage")),
    db: Session = Depends(get_db),
) -> RollupOut:
    start, end, records = billing.rollup_period(db, actor, body.period)
    return RollupOut(period_start=start, period_end=end, records=[UsageRecordOut.model_validate(r) for r in records])


@router.get(
    "/billing/invoices",
    summary="Invoices",
    description="Invoice references recorded by the billing provider, newest period first.",
    response_model=Page[InvoiceOut],
    status_code=status.HTTP_200_OK,
    responses={**E401, **E403},
)
def invoices(
    params: PageParams = Depends(page_params),
    actor: Actor = Depends(require_actor("billing:view")),
    db: Session = Depends(get_db),
) -> Page[InvoiceOut]:
    return billing.list_invoices(db, actor, params)


@router.post(
    "/billing/invoices",
    summary="Create a draft invoice",
    description=(
        "Rolls up the period and records a draft invoice (idempotent per period; supports `Idempotency-Key`). "
        "Records of a finished period are finalized."
    ),
    response_model=InvoiceOut,
    status_code=status.HTTP_201_CREATED,
    responses={**E401, **E403, **E422},
    dependencies=[Depends(require_feature("billing"))],
)
def create_invoice(
    body: PeriodIn,
    idem: Idempotency = Depends(idempotency),
    actor: Actor = Depends(require_actor("billing:manage")),
    db: Session = Depends(get_db),
) -> InvoiceOut:
    if (replay := idem.replay()) is not None:
        return replay  # type: ignore[return-value]
    invoice = billing.create_invoice(db, actor, body.period)
    return idem.remember(status.HTTP_201_CREATED, InvoiceOut.model_validate(invoice))
