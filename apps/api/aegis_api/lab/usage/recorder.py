"""Usage recording (the cost engine's write path).

Every provider request and compute job emits a ledger row here. Mission budget counters are incremented
atomically in SQL (``UPDATE … SET spent = spent + :x``), so concurrent workers never lose updates; the
ledgers remain the source of truth for aggregation. Recording happens in a short transaction *after* the
external call returns — never while holding a transaction open across the call.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import update
from sqlalchemy.orm import Session

from aegis_api.lab.models import ComputeUsage, Mission, ModelUsage, StorageUsage
from aegis_api.lab.observability import metrics


def _money(value: float | Decimal | None) -> Decimal:
    if value is None:
        return Decimal("0")
    return value if isinstance(value, Decimal) else Decimal(str(round(float(value), 6)))


def increment_mission_spend(
    db: Session,
    mission_id: uuid.UUID | None,
    *,
    llm_usd: float | Decimal = 0,
    compute_usd: float | Decimal = 0,
    tool_usd: float | Decimal = 0,
    experiments: int = 0,
    research_tasks: int = 0,
) -> None:
    """Atomically add to a mission's budget counters (bypasses optimistic locking on purpose)."""
    if mission_id is None:
        return
    values: dict[str, Any] = {}
    if llm_usd:
        values["spent_llm_usd"] = Mission.spent_llm_usd + _money(llm_usd)
    if compute_usd:
        values["spent_compute_usd"] = Mission.spent_compute_usd + _money(compute_usd)
    if tool_usd:
        values["spent_tool_usd"] = Mission.spent_tool_usd + _money(tool_usd)
    if experiments:
        values["experiment_count"] = Mission.experiment_count + experiments
    if research_tasks:
        values["research_task_count"] = Mission.research_task_count + research_tasks
    if not values:
        return
    db.execute(
        update(Mission).where(Mission.id == mission_id).values(**values).execution_options(synchronize_session=False)
    )


def record_model_usage(
    db: Session,
    *,
    organization_id: uuid.UUID,
    provider: str,
    model: str,
    task_type: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_tokens: int = 0,
    thinking_tokens: int = 0,
    latency_ms: int = 0,
    cost_usd: float | Decimal | None = None,
    cost_estimated: bool = True,
    cost_basis: str | None = None,
    success: bool = True,
    error_code: str | None = None,
    retry_count: int = 0,
    model_version: str | None = None,
    request_id: str | None = None,
    trace_id: str | None = None,
    project_id: uuid.UUID | None = None,
    mission_id: uuid.UUID | None = None,
    agent_run_id: uuid.UUID | None = None,
    research_task_id: uuid.UUID | None = None,
    route_reason: str | None = None,
) -> ModelUsage:
    row = ModelUsage(
        organization_id=organization_id,
        project_id=project_id,
        mission_id=mission_id,
        agent_run_id=agent_run_id,
        research_task_id=research_task_id,
        provider=provider,
        model=model,
        model_version=model_version,
        request_id=(request_id or None) and request_id[:160],
        trace_id=trace_id,
        task_type=task_type,
        input_tokens=input_tokens or 0,
        output_tokens=output_tokens or 0,
        cached_tokens=cached_tokens or 0,
        thinking_tokens=thinking_tokens or 0,
        latency_ms=latency_ms or 0,
        cost_usd=_money(cost_usd),
        cost_estimated=cost_estimated,
        cost_basis=(cost_basis or None) and cost_basis[:160],
        success=success,
        error_code=error_code,
        retry_count=retry_count,
        route_reason=(route_reason or None) and route_reason[:300],
    )
    db.add(row)
    increment_mission_spend(db, mission_id, llm_usd=_money(cost_usd))
    metrics.LLM_REQUESTS.labels(provider, model, task_type, "success" if success else "error").inc()
    metrics.LLM_TOKENS.labels(provider, "input").inc(input_tokens or 0)
    metrics.LLM_TOKENS.labels(provider, "output").inc(output_tokens or 0)
    if cost_usd:
        metrics.LLM_COST.labels(provider).inc(float(cost_usd))
    db.flush()
    return row


def record_compute_usage(
    db: Session,
    *,
    organization_id: uuid.UUID,
    compute_job_id: uuid.UUID,
    backend: str,
    cpu_seconds: float = 0.0,
    gpu_seconds: float = 0.0,
    gpu_type: str | None = None,
    memory_mb_seconds: float = 0.0,
    wall_seconds: float = 0.0,
    cost_usd: float | Decimal = 0,
    cost_basis: str = "configured rates",
    project_id: uuid.UUID | None = None,
    mission_id: uuid.UUID | None = None,
    experiment_id: uuid.UUID | None = None,
) -> ComputeUsage:
    row = ComputeUsage(
        organization_id=organization_id,
        project_id=project_id,
        mission_id=mission_id,
        experiment_id=experiment_id,
        compute_job_id=compute_job_id,
        backend=backend,
        cpu_seconds=cpu_seconds,
        gpu_seconds=gpu_seconds,
        gpu_type=gpu_type,
        memory_mb_seconds=memory_mb_seconds,
        wall_seconds=wall_seconds,
        cost_usd=_money(cost_usd),
        cost_basis=cost_basis[:120],
    )
    db.add(row)
    increment_mission_spend(db, mission_id, compute_usd=_money(cost_usd))
    metrics.CPU_SECONDS.labels(backend).inc(max(cpu_seconds, 0.0))
    if gpu_seconds:
        metrics.GPU_SECONDS.labels(gpu_type or "unknown").inc(gpu_seconds)
    db.flush()
    return row


def record_tool_spend(db: Session, *, mission_id: uuid.UUID | None, cost_usd: float | Decimal) -> None:
    """Tool costs live on ``tool_invocations``; this only advances the mission counter."""
    increment_mission_spend(db, mission_id, tool_usd=_money(cost_usd))


def record_storage_usage(
    db: Session,
    *,
    organization_id: uuid.UUID,
    bytes_delta: int,
    reason: str,
    project_id: uuid.UUID | None = None,
    artifact_version_id: uuid.UUID | None = None,
    dataset_version_id: uuid.UUID | None = None,
) -> StorageUsage:
    row = StorageUsage(
        organization_id=organization_id,
        project_id=project_id,
        artifact_version_id=artifact_version_id,
        dataset_version_id=dataset_version_id,
        bytes_delta=bytes_delta,
        reason=reason[:32],
    )
    db.add(row)
    db.flush()
    return row
