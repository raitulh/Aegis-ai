"""Use cases behind the Models API: organization model configuration and model-usage queries."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, and_, case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aegis_api.errors import Conflict, NotFound, ValidationFailed
from aegis_api.lab.core.access import get_owned
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.audit import AuditAction, audit
from aegis_api.lab.core.pagination import CursorPage, CursorParams, decode_cursor, encode_cursor
from aegis_api.lab.llm.schemas import (
    ModelConfigCreate,
    ModelConfigOut,
    ModelConfigUpdate,
    ModelUsageOut,
    UsageSummaryOut,
    UsageSummaryRow,
)
from aegis_api.lab.models import ModelConfig, ModelUsage

MAX_SUMMARY_DAYS = 366
GROUPABLE = {"provider": ModelUsage.provider, "model": ModelUsage.model, "task_type": ModelUsage.task_type}


# --- model configs --------------------------------------------------------------------------------
def config_out(row: ModelConfig) -> ModelConfigOut:
    return ModelConfigOut.model_validate(
        {
            "id": row.id,
            "organization_id": row.organization_id,
            "scope": "organization" if row.organization_id is not None else "platform",
            "provider_kind": row.provider_kind,
            "model": row.model,
            "tier": row.tier,
            "task_types": list(row.task_types or []),
            "priority": row.priority,
            "enabled": row.enabled,
            "max_output_tokens": row.max_output_tokens,
            "temperature": row.temperature,
            "input_per_mtok_usd": row.input_per_mtok_usd,
            "output_per_mtok_usd": row.output_per_mtok_usd,
            "cached_input_per_mtok_usd": row.cached_input_per_mtok_usd,
            "context_window": row.context_window,
            "capabilities": dict(row.capabilities or {}),
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
    )


def _snapshot(row: ModelConfig) -> dict[str, Any]:
    return config_out(row).model_dump(mode="json", exclude={"created_at", "updated_at", "organization_id"})


def _check_prices(input_price: Decimal | None, output_price: Decimal | None) -> None:
    if (input_price is None) != (output_price is None):
        raise ValidationFailed("Set both input_per_mtok_usd and output_per_mtok_usd (or neither)")


def list_configs(
    db: Session,
    actor: Actor,
    *,
    provider_kind: str | None = None,
    tier: str | None = None,
    enabled: bool | None = None,
) -> Select[Any]:
    actor.require("model:read")
    stmt = select(ModelConfig).where(
        or_(ModelConfig.organization_id == actor.organization_id, ModelConfig.organization_id.is_(None))
    )
    if provider_kind:
        stmt = stmt.where(ModelConfig.provider_kind == provider_kind)
    if tier:
        stmt = stmt.where(ModelConfig.tier == tier)
    if enabled is not None:
        stmt = stmt.where(ModelConfig.enabled.is_(enabled))
    return stmt.order_by(ModelConfig.tier, ModelConfig.priority, ModelConfig.created_at, ModelConfig.id)


def get_config(db: Session, actor: Actor, config_id: uuid.UUID | str) -> ModelConfig:
    """Organization rows and platform-wide rows are readable; other tenants' rows are not found."""
    actor.require("model:read")
    try:
        key = config_id if isinstance(config_id, uuid.UUID) else uuid.UUID(str(config_id))
    except ValueError as exc:
        raise NotFound("Model configuration not found") from exc
    row = db.get(ModelConfig, key)
    if row is None or row.organization_id not in (None, actor.organization_id):
        raise NotFound("Model configuration not found")
    return row


def create_config(db: Session, actor: Actor, data: ModelConfigCreate) -> ModelConfig:
    actor.require("model:manage")
    _check_prices(data.input_per_mtok_usd, data.output_per_mtok_usd)
    duplicate = db.scalar(
        select(ModelConfig.id).where(
            ModelConfig.organization_id == actor.organization_id,
            ModelConfig.provider_kind == data.provider_kind,
            ModelConfig.model == data.model,
            ModelConfig.tier == data.tier,
        )
    )
    if duplicate is not None:
        raise Conflict(f"A configuration for {data.provider_kind}:{data.model} ({data.tier}) already exists")
    row = ModelConfig(
        organization_id=actor.organization_id,
        provider_kind=data.provider_kind,
        model=data.model,
        tier=data.tier,
        task_types=[t.value for t in data.task_types],
        priority=data.priority,
        enabled=data.enabled,
        max_output_tokens=data.max_output_tokens,
        temperature=data.temperature,
        input_per_mtok_usd=data.input_per_mtok_usd,
        output_per_mtok_usd=data.output_per_mtok_usd,
        cached_input_per_mtok_usd=data.cached_input_per_mtok_usd,
        context_window=data.context_window,
        capabilities={str(k): bool(v) for k, v in data.capabilities.items()},
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError as exc:
        raise Conflict("A matching model configuration already exists") from exc
    audit(db, actor, AuditAction.MODEL_CONFIG_CHANGED, "model_config", row.id, after=_snapshot(row))
    return row


def update_config(db: Session, actor: Actor, config_id: uuid.UUID | str, data: ModelConfigUpdate) -> ModelConfig:
    actor.require("model:manage")
    row = get_owned(db, ModelConfig, config_id, actor, label="Model configuration")
    changes = data.model_dump(exclude_unset=True)
    if not changes:
        return row
    before = _snapshot(row)
    if "task_types" in changes and changes["task_types"] is not None:
        changes["task_types"] = [str(t) for t in changes["task_types"]]
    if "capabilities" in changes and changes["capabilities"] is not None:
        changes["capabilities"] = {str(k): bool(v) for k, v in changes["capabilities"].items()}
    for field_name in ("task_types", "priority", "enabled", "capabilities"):
        if field_name in changes and changes[field_name] is None:
            raise ValidationFailed(f"{field_name} cannot be null")
    for key, value in changes.items():
        setattr(row, key, value)
    _check_prices(row.input_per_mtok_usd, row.output_per_mtok_usd)
    db.flush()
    audit(db, actor, AuditAction.MODEL_CONFIG_CHANGED, "model_config", row.id, before=before, after=_snapshot(row))
    return row


def delete_config(db: Session, actor: Actor, config_id: uuid.UUID | str) -> None:
    actor.require("model:manage")
    row = get_owned(db, ModelConfig, config_id, actor, label="Model configuration")
    before = _snapshot(row)
    db.delete(row)
    db.flush()
    audit(db, actor, AuditAction.MODEL_CONFIG_CHANGED, "model_config", before["id"], before=before, after=None)


# --- usage ----------------------------------------------------------------------------------------
def _range(since: datetime | None, until: datetime | None) -> tuple[datetime, datetime]:
    now = datetime.now(UTC)
    end = until or now
    start = since or end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    if start >= end:
        raise ValidationFailed("'since' must be before 'until'")
    if end - start > timedelta(days=MAX_SUMMARY_DAYS):
        raise ValidationFailed(f"The date range may span at most {MAX_SUMMARY_DAYS} days")
    return start, end


def list_usage(
    db: Session,
    actor: Actor,
    params: CursorParams,
    *,
    provider: str | None = None,
    model: str | None = None,
    task_type: str | None = None,
    success: bool | None = None,
    project_id: uuid.UUID | None = None,
    mission_id: uuid.UUID | None = None,
    agent_run_id: uuid.UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> CursorPage[ModelUsageOut]:
    """Newest-first keyset pagination over the organization's ``model_usage`` ledger."""
    actor.require("usage:read")
    stmt = select(ModelUsage).where(ModelUsage.organization_id == actor.organization_id)
    for column, value in (
        (ModelUsage.provider, provider),
        (ModelUsage.model, model),
        (ModelUsage.task_type, task_type),
        (ModelUsage.project_id, project_id),
        (ModelUsage.mission_id, mission_id),
        (ModelUsage.agent_run_id, agent_run_id),
    ):
        if value is not None:
            stmt = stmt.where(column == value)
    if success is not None:
        stmt = stmt.where(ModelUsage.success.is_(success))
    if since is not None:
        stmt = stmt.where(ModelUsage.created_at >= since)
    if until is not None:
        stmt = stmt.where(ModelUsage.created_at < until)
    if params.cursor:
        cursor = decode_cursor(params.cursor)
        try:
            after_time = datetime.fromisoformat(str(cursor["t"]))
            after_id = uuid.UUID(str(cursor["i"]))
        except (KeyError, ValueError) as exc:
            raise ValidationFailed("Invalid pagination cursor") from exc
        stmt = stmt.where(
            or_(
                ModelUsage.created_at < after_time,
                and_(ModelUsage.created_at == after_time, ModelUsage.id < after_id),
            )
        )
    rows = list(db.scalars(stmt.order_by(ModelUsage.created_at.desc(), ModelUsage.id.desc()).limit(params.limit + 1)))
    has_more = len(rows) > params.limit
    rows = rows[: params.limit]
    next_cursor = encode_cursor({"t": rows[-1].created_at, "i": rows[-1].id}) if has_more and rows else None
    return CursorPage[ModelUsageOut](
        items=[ModelUsageOut.model_validate(r) for r in rows], next_cursor=next_cursor, limit=params.limit
    )


def usage_summary(
    db: Session,
    actor: Actor,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    group_by: list[str] | None = None,
) -> UsageSummaryOut:
    """Token, cost, failure and latency aggregates grouped by provider/model/task type in a date range."""
    actor.require("usage:read")
    start, end = _range(since, until)
    groups = group_by or ["provider", "model", "task_type"]
    unknown = [g for g in groups if g not in GROUPABLE]
    if unknown:
        raise ValidationFailed(f"Unsupported group_by value(s): {', '.join(unknown)}. Allowed: {', '.join(GROUPABLE)}")
    groups = list(dict.fromkeys(groups))
    columns = [GROUPABLE[g] for g in groups]
    metrics_cols = (
        func.count(ModelUsage.id),
        func.coalesce(func.sum(case((ModelUsage.success.is_(False), 1), else_=0)), 0),
        func.coalesce(func.sum(ModelUsage.input_tokens), 0),
        func.coalesce(func.sum(ModelUsage.output_tokens), 0),
        func.coalesce(func.sum(ModelUsage.cached_tokens), 0),
        func.coalesce(func.sum(ModelUsage.thinking_tokens), 0),
        func.coalesce(func.sum(ModelUsage.cost_usd), 0),
        func.coalesce(func.avg(ModelUsage.latency_ms), 0),
    )
    where = (
        ModelUsage.organization_id == actor.organization_id,
        ModelUsage.created_at >= start,
        ModelUsage.created_at < end,
    )

    def row_of(keys: dict[str, Any], values: Any) -> UsageSummaryRow:
        calls, failures, inp, out, cached, thinking, cost, latency = values
        return UsageSummaryRow(
            **keys,
            calls=int(calls),
            failures=int(failures),
            input_tokens=int(inp),
            output_tokens=int(out),
            cached_tokens=int(cached),
            thinking_tokens=int(thinking),
            cost_usd=Decimal(cost),
            avg_latency_ms=round(float(latency), 1),
        )

    grouped = db.execute(select(*columns, *metrics_cols).where(*where).group_by(*columns).order_by(*columns)).all()
    rows = [row_of(dict(zip(groups, r[: len(groups)], strict=True)), r[len(groups) :]) for r in grouped]
    total = db.execute(select(*metrics_cols).where(*where)).one()
    return UsageSummaryOut(since=start, until=end, group_by=groups, groups=rows, totals=row_of({}, total))
