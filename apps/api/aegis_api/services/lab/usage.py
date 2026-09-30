"""Usage metering and budget enforcement.

Every model call, sandboxed job and stored object writes a usage row (tenant/project/mission attributed).
Spend is aggregated from those rows — never from estimates — and checked against mission and project budgets
plus organization spend quotas before expensive work starts. Costs are only reported when prices are
configured; unpriced usage still counts toward token/compute-second limits.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.db.base import utcnow
from aegis_api.models import Project
from aegis_api.models.lab import ComputeUsage, ExperimentRun, Mission, ModelUsage, StorageUsage
from aegis_api.services import quota_service
from engines.lab.budgets import Budget, BudgetCheck, Spend, check_budget
from engines.lab.costs import storage_cost


def record_model_usage(
    db: Session,
    *,
    organization_id: uuid.UUID,
    provider: str,
    model: str,
    task_type: str,
    input_tokens: int,
    output_tokens: int,
    latency_ms: int,
    success: bool,
    cost_usd: float | None,
    cost_basis: str | None,
    project_id: uuid.UUID | None = None,
    mission_id: uuid.UUID | None = None,
    agent_run_id: uuid.UUID | None = None,
    research_task_id: uuid.UUID | None = None,
    cached_tokens: int = 0,
    thought_tokens: int = 0,
    error_code: str | None = None,
    retry_count: int = 0,
    prompt_hash: str | None = None,
    routing_reason: str | None = None,
    request_id: str | None = None,
    provider_request_id: str | None = None,
) -> ModelUsage:
    row = ModelUsage(
        organization_id=organization_id,
        project_id=project_id,
        mission_id=mission_id,
        agent_run_id=agent_run_id,
        research_task_id=research_task_id,
        provider=provider,
        model=model[:160],
        task_type=task_type[:40],
        request_id=request_id,
        provider_request_id=(provider_request_id or "")[:255] or None,
        input_tokens=int(input_tokens),
        output_tokens=int(output_tokens),
        cached_tokens=int(cached_tokens),
        thought_tokens=int(thought_tokens),
        latency_ms=int(latency_ms),
        cost_usd=cost_usd,
        cost_basis=cost_basis,
        success=success,
        error_code=error_code,
        retry_count=retry_count,
        prompt_hash=prompt_hash,
        routing_reason=(routing_reason or "")[:2000] or None,
    )
    db.add(row)
    return row


def record_storage(
    db: Session,
    *,
    organization_id: uuid.UUID,
    object_kind: str,
    object_id: str | uuid.UUID,
    size_bytes: int,
    operation: str = "put",
    project_id: uuid.UUID | None = None,
    mission_id: uuid.UUID | None = None,
) -> StorageUsage:
    estimate = storage_cost(get_settings().compute_pricing, size_bytes=size_bytes)
    row = StorageUsage(
        organization_id=organization_id,
        project_id=project_id,
        mission_id=mission_id,
        object_kind=object_kind,
        object_id=str(object_id),
        operation=operation,
        bytes=int(size_bytes) if operation == "put" else -int(size_bytes),
        cost_usd=estimate.usd if operation == "put" else None,
    )
    db.add(row)
    return row


def spend(
    db: Session,
    organization_id: uuid.UUID,
    *,
    project_id: uuid.UUID | None = None,
    mission_id: uuid.UUID | None = None,
    since: datetime | None = None,
) -> Spend:
    def scoped(stmt: Any, model: Any) -> Any:
        stmt = stmt.where(model.organization_id == organization_id)
        if project_id is not None:
            stmt = stmt.where(model.project_id == project_id)
        if mission_id is not None:
            stmt = stmt.where(model.mission_id == mission_id)
        if since is not None:
            stmt = stmt.where(model.created_at >= since)
        return stmt

    llm = db.execute(
        scoped(
            select(
                func.coalesce(func.sum(ModelUsage.cost_usd), 0.0),
                func.coalesce(func.sum(ModelUsage.input_tokens + ModelUsage.output_tokens), 0),
            ),
            ModelUsage,
        )
    ).one()
    compute = db.execute(
        scoped(
            select(
                func.coalesce(func.sum(ComputeUsage.cost_usd), 0.0),
                func.coalesce(func.sum(ComputeUsage.runtime_seconds), 0.0),
            ),
            ComputeUsage,
        )
    ).one()
    runs = int(
        db.scalar(
            scoped(
                select(func.count(ExperimentRun.id)).where(ExperimentRun.status.notin_(["cancelled"])),
                ExperimentRun,
            )
        )
        or 0
    )
    llm_cost, tokens = float(llm[0] or 0.0), int(llm[1] or 0)
    compute_cost, seconds = float(compute[0] or 0.0), float(compute[1] or 0.0)
    return Spend(
        total_cost=round(llm_cost + compute_cost, 6),
        llm_cost=round(llm_cost, 6),
        compute_cost=round(compute_cost, 6),
        experiment_count=runs,
        llm_tokens=tokens,
        compute_seconds=round(seconds, 3),
    )


def check_mission_budget(db: Session, mission: Mission, estimate: Spend | None = None) -> BudgetCheck:
    budget_doc = {**(mission.budget or {}), **(mission.compute_budget or {})}
    deadline: datetime | None = mission.deadline
    if mission.time_budget_seconds and mission.started_at:
        by_time = mission.started_at + timedelta(seconds=mission.time_budget_seconds)
        deadline = min(by_time, deadline) if deadline else by_time
    budget = Budget.from_dict(budget_doc, deadline=deadline)
    spent = spend(db, mission.organization_id, mission_id=mission.id)
    return check_budget(budget, spent, estimate, now=utcnow())


def check_project_budget(db: Session, project: Project, estimate: Spend | None = None) -> BudgetCheck:
    budget = Budget.from_dict(project.budget or {})
    spent = spend(db, project.organization_id, project_id=project.id)
    return check_budget(budget, spent, estimate, now=utcnow())


def check_org_quota(db: Session, organization_id: uuid.UUID, estimate: Spend | None = None) -> BudgetCheck:
    quota = quota_service.get_quota(db, organization_id)
    month_start = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    budget = Budget(max_llm_cost=quota.max_llm_spend_usd, max_compute_cost=quota.max_compute_spend_usd)
    spent = spend(db, organization_id, since=month_start)
    return check_budget(budget, spent, estimate, now=utcnow())


def combined_check(
    db: Session,
    organization_id: uuid.UUID,
    *,
    mission: Mission | None = None,
    project: Project | None = None,
    estimate: Spend | None = None,
) -> dict[str, Any]:
    """Budget facts for the policy engine: ``budget.would_exceed`` plus per-scope details."""
    checks: dict[str, BudgetCheck] = {"organization": check_org_quota(db, organization_id, estimate)}
    if project is not None:
        checks["project"] = check_project_budget(db, project, estimate)
    if mission is not None:
        checks["mission"] = check_mission_budget(db, mission, estimate)
    exceeded = {scope: c.exceeded for scope, c in checks.items() if c.exceeded}
    warnings = {scope: c.warnings for scope, c in checks.items() if c.warnings}
    return {
        "would_exceed": bool(exceeded),
        "exceeded": exceeded,
        "warnings": warnings,
        "scopes": {scope: c.to_dict() for scope, c in checks.items()},
    }


def summary(
    db: Session,
    organization_id: uuid.UUID,
    *,
    project_id: uuid.UUID | None = None,
    mission_id: uuid.UUID | None = None,
    since: datetime | None = None,
) -> dict[str, Any]:
    s = spend(db, organization_id, project_id=project_id, mission_id=mission_id, since=since)
    by_model_stmt = (
        select(
            ModelUsage.provider,
            ModelUsage.model,
            func.count(ModelUsage.id),
            func.coalesce(func.sum(ModelUsage.input_tokens), 0),
            func.coalesce(func.sum(ModelUsage.output_tokens), 0),
            func.sum(ModelUsage.cost_usd),
            func.count(ModelUsage.id).filter(ModelUsage.success.is_(False)),
        )
        .where(ModelUsage.organization_id == organization_id)
        .group_by(ModelUsage.provider, ModelUsage.model)
    )
    if project_id:
        by_model_stmt = by_model_stmt.where(ModelUsage.project_id == project_id)
    if mission_id:
        by_model_stmt = by_model_stmt.where(ModelUsage.mission_id == mission_id)
    if since:
        by_model_stmt = by_model_stmt.where(ModelUsage.created_at >= since)
    models = [
        {
            "provider": r[0],
            "model": r[1],
            "requests": int(r[2]),
            "input_tokens": int(r[3]),
            "output_tokens": int(r[4]),
            "cost_usd": None if r[5] is None else round(float(r[5]), 6),
            "failures": int(r[6]),
        }
        for r in db.execute(by_model_stmt).all()
    ]
    storage_stmt = select(func.coalesce(func.sum(StorageUsage.bytes), 0)).where(
        StorageUsage.organization_id == organization_id
    )
    if project_id:
        storage_stmt = storage_stmt.where(StorageUsage.project_id == project_id)
    stored = int(db.scalar(storage_stmt) or 0)
    return {
        "spend": {
            "total_cost_usd": s.total_cost,
            "llm_cost_usd": s.llm_cost,
            "compute_cost_usd": s.compute_cost,
            "llm_tokens": s.llm_tokens,
            "compute_seconds": s.compute_seconds,
            "experiment_runs": s.experiment_count,
        },
        "models": models,
        "storage_bytes": stored,
        "pricing_note": "Costs are computed only from configured prices; unpriced usage reports cost as null.",
    }
