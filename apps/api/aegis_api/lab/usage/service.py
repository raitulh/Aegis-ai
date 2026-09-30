"""Usage and cost aggregation over the append-only ledgers.

Sources of truth: ``model_usage`` (LLM calls), ``compute_usage`` (finished compute jobs),
``tool_invocations`` (tool costs) and ``storage_usage`` (bytes written/purged). Every aggregate is a
``GROUP BY`` over an ``(organization_id, created_at)`` indexed range with labels resolved in one batched
lookup — no per-row queries. Periods are half-open ``[since, until)`` and default to the current calendar
month (UTC).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from aegis_api.config import get_settings
from aegis_api.db.base import utcnow
from aegis_api.errors import ValidationFailed
from aegis_api.lab.core.access import load_project, visible_project_ids
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.pagination import CursorPage, CursorParams, decode_cursor, encode_cursor
from aegis_api.lab.models import (
    AgentRun,
    ComputeUsage,
    Discovery,
    Experiment,
    Mission,
    ModelUsage,
    Project,
    StorageUsage,
    ToolInvocation,
)
from aegis_api.lab.usage.schemas import (
    ComputeUsageOut,
    ComputeUsageTotals,
    CostBreakdownOut,
    CostGroupOut,
    LLMUsageTotals,
    ModelUsageOut,
    StorageUsageTotals,
    ToolCostOut,
    ToolCostsOut,
    ToolUsageTotals,
    UsageSummaryOut,
)

GROUP_BY_OPTIONS = ("mission", "experiment", "discovery", "agent", "model", "project", "organization")
MAX_DISCOVERIES = 1000
ZERO = Decimal("0")


# ---------------------------------------------------------------------------------------------
# Periods & scoping
# ---------------------------------------------------------------------------------------------
def month_bounds(moment: datetime | None = None) -> tuple[datetime, datetime]:
    now = (moment or utcnow()).astimezone(UTC)
    start = datetime(now.year, now.month, 1, tzinfo=UTC)
    end = datetime(now.year + (now.month == 12), 1 if now.month == 12 else now.month + 1, 1, tzinfo=UTC)
    return start, end


def parse_period(period: str | None) -> tuple[datetime, datetime]:
    """``YYYY-MM`` → ``[first day, first day of next month)`` (UTC); ``None`` → current month."""
    if not period:
        return month_bounds()
    try:
        year, month = (int(part) for part in period.split("-", 1))
        return month_bounds(datetime(year, month, 15, tzinfo=UTC))
    except ValueError as exc:
        raise ValidationFailed("period must be a calendar month formatted 'YYYY-MM'") from exc


def resolve_range(since: datetime | None, until: datetime | None) -> tuple[datetime, datetime]:
    default_start, default_end = month_bounds()
    start = _aware(since) if since is not None else default_start
    end = _aware(until) if until is not None else (default_end if since is None else utcnow())
    if start >= end:
        raise ValidationFailed("since must be earlier than until")
    return start, end


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


@dataclass
class _Scope:
    organization_id: uuid.UUID
    start: datetime
    end: datetime
    project_id: uuid.UUID | None
    visible: list[uuid.UUID] | None  # None = every project visible

    def ledger(self, stmt: Select, model: Any) -> Select:
        stmt = stmt.where(
            model.organization_id == self.organization_id, model.created_at >= self.start, model.created_at < self.end
        )
        if self.project_id is not None:
            stmt = stmt.where(model.project_id == self.project_id)
        elif self.visible is not None:
            stmt = stmt.where(or_(model.project_id.is_(None), model.project_id.in_(self.visible)))
        return stmt


def _scope(
    db: Session,
    actor: Actor,
    since: datetime | None,
    until: datetime | None,
    project_id: uuid.UUID | str | None,
) -> _Scope:
    start, end = resolve_range(since, until)
    pid: uuid.UUID | None = None
    if project_id:
        pid = load_project(db, actor, project_id).id
    return _Scope(actor.organization_id, start, end, pid, None if pid else visible_project_ids(db, actor))


def _dec(value: Any) -> Decimal:
    return Decimal(value) if value is not None else ZERO


def _currency() -> str:
    return get_settings().billing_currency


# ---------------------------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------------------------
def _llm_totals(db: Session, scope: _Scope) -> LLMUsageTotals:
    row = db.execute(
        scope.ledger(
            select(
                func.coalesce(func.sum(ModelUsage.cost_usd), 0),
                func.count(ModelUsage.id),
                func.count(ModelUsage.id).filter(ModelUsage.success.is_(False)),
                func.coalesce(func.sum(ModelUsage.input_tokens), 0),
                func.coalesce(func.sum(ModelUsage.output_tokens), 0),
                func.coalesce(func.sum(ModelUsage.cached_tokens), 0),
                func.coalesce(func.sum(ModelUsage.thinking_tokens), 0),
            ),
            ModelUsage,
        )
    ).one()
    return LLMUsageTotals(
        cost_usd=_dec(row[0]),
        calls=int(row[1]),
        failed_calls=int(row[2]),
        input_tokens=int(row[3]),
        output_tokens=int(row[4]),
        cached_tokens=int(row[5]),
        thinking_tokens=int(row[6]),
    )


def _compute_totals(db: Session, scope: _Scope) -> ComputeUsageTotals:
    row = db.execute(
        scope.ledger(
            select(
                func.coalesce(func.sum(ComputeUsage.cost_usd), 0),
                func.count(func.distinct(ComputeUsage.compute_job_id)),
                func.coalesce(func.sum(ComputeUsage.cpu_seconds), 0.0),
                func.coalesce(func.sum(ComputeUsage.gpu_seconds), 0.0),
                func.coalesce(func.sum(ComputeUsage.wall_seconds), 0.0),
            ),
            ComputeUsage,
        )
    ).one()
    return ComputeUsageTotals(
        cost_usd=_dec(row[0]),
        jobs=int(row[1]),
        cpu_seconds=float(row[2]),
        gpu_seconds=float(row[3]),
        wall_seconds=float(row[4]),
    )


def _tool_totals(db: Session, scope: _Scope) -> ToolUsageTotals:
    row = db.execute(
        scope.ledger(
            select(func.coalesce(func.sum(ToolInvocation.cost_usd), 0), func.count(ToolInvocation.id)), ToolInvocation
        )
    ).one()
    return ToolUsageTotals(cost_usd=_dec(row[0]), invocations=int(row[1]))


def _storage_totals(db: Session, scope: _Scope) -> StorageUsageTotals:
    base = select(func.coalesce(func.sum(StorageUsage.bytes_delta), 0)).where(
        StorageUsage.organization_id == scope.organization_id
    )
    if scope.project_id is not None:
        base = base.where(StorageUsage.project_id == scope.project_id)
    elif scope.visible is not None:
        base = base.where(or_(StorageUsage.project_id.is_(None), StorageUsage.project_id.in_(scope.visible)))
    stored = db.scalar(base.where(StorageUsage.created_at < scope.end)) or 0
    delta = db.scalar(base.where(StorageUsage.created_at >= scope.start, StorageUsage.created_at < scope.end)) or 0
    return StorageUsageTotals(bytes_stored=max(int(stored), 0), bytes_delta=int(delta))


def usage_summary(
    db: Session,
    actor: Actor,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    project_id: uuid.UUID | str | None = None,
) -> UsageSummaryOut:
    scope = _scope(db, actor, since, until, project_id)
    llm = _llm_totals(db, scope)
    compute = _compute_totals(db, scope)
    tools = _tool_totals(db, scope)
    return UsageSummaryOut(
        period_start=scope.start,
        period_end=scope.end,
        project_id=str(scope.project_id) if scope.project_id else None,
        currency=_currency(),
        llm=llm,
        compute=compute,
        tools=tools,
        storage=_storage_totals(db, scope),
        total_cost_usd=llm.cost_usd + compute.cost_usd + tools.cost_usd,
    )


# ---------------------------------------------------------------------------------------------
# Cost breakdowns
# ---------------------------------------------------------------------------------------------
@dataclass
class _Acc:
    llm: Decimal = ZERO
    compute: Decimal = ZERO
    tool: Decimal = ZERO
    tokens: int = 0
    calls: int = 0
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def total(self) -> Decimal:
        return self.llm + self.compute + self.tool


def _grouped(
    db: Session, scope: _Scope, model: Any, key_col: Any, cost_col: Any, extra: Iterable[Any] = ()
) -> list[Any]:
    stmt = select(key_col, func.coalesce(func.sum(cost_col), 0), func.count(model.id), *extra).group_by(key_col)
    return list(db.execute(scope.ledger(stmt, model)).all())


def _llm_by(db: Session, scope: _Scope, key_col: Any, accs: dict[Any, _Acc]) -> None:
    tokens = func.coalesce(func.sum(ModelUsage.input_tokens + ModelUsage.output_tokens + ModelUsage.thinking_tokens), 0)
    for key, cost, calls, tok in _grouped(db, scope, ModelUsage, key_col, ModelUsage.cost_usd, (tokens,)):
        acc = accs[key]
        acc.llm += _dec(cost)
        acc.calls += int(calls)
        acc.tokens += int(tok)


def _compute_by(db: Session, scope: _Scope, key_col: Any, accs: dict[Any, _Acc]) -> None:
    for key, cost, jobs in _grouped(db, scope, ComputeUsage, key_col, ComputeUsage.cost_usd):
        acc = accs[key]
        acc.compute += _dec(cost)
        acc.details["compute_jobs"] = acc.details.get("compute_jobs", 0) + int(jobs)


def _tools_by(db: Session, scope: _Scope, key_col: Any, accs: dict[Any, _Acc]) -> None:
    for key, cost, count in _grouped(db, scope, ToolInvocation, key_col, ToolInvocation.cost_usd):
        acc = accs[key]
        acc.tool += _dec(cost)
        acc.details["tool_invocations"] = acc.details.get("tool_invocations", 0) + int(count)


def _labels(
    db: Session, column_id: InstrumentedAttribute[Any], column_label: Any, keys: Iterable[Any]
) -> dict[Any, str]:
    ids = [k for k in keys if isinstance(k, uuid.UUID)]
    if not ids:
        return {}
    return {row[0]: str(row[1]) for row in db.execute(select(column_id, column_label).where(column_id.in_(ids))).all()}


def _items(
    accs: dict[Any, _Acc], labels: dict[Any, str], *, unattributed: str = "Unattributed", fallback: str = "Unknown"
) -> list[CostGroupOut]:
    items = []
    for key, acc in accs.items():
        default = fallback if isinstance(key, uuid.UUID) else str(key)
        label = unattributed if key is None else labels.get(key, default)
        items.append(
            CostGroupOut(
                key=None if key is None else str(key),
                label=label,
                llm_usd=acc.llm,
                compute_usd=acc.compute,
                tool_usd=acc.tool,
                total_usd=acc.total,
                tokens=acc.tokens,
                calls=acc.calls,
                details=acc.details,
            )
        )
    items.sort(key=lambda item: (-item.total_usd, item.label))
    return items


def _by_mission(db: Session, scope: _Scope) -> list[CostGroupOut]:
    accs: dict[Any, _Acc] = defaultdict(_Acc)
    _llm_by(db, scope, ModelUsage.mission_id, accs)
    _compute_by(db, scope, ComputeUsage.mission_id, accs)
    _tools_by(db, scope, ToolInvocation.mission_id, accs)
    return _items(accs, _labels(db, Mission.id, Mission.title, accs), unattributed="No mission")


def _by_project(db: Session, scope: _Scope) -> list[CostGroupOut]:
    accs: dict[Any, _Acc] = defaultdict(_Acc)
    _llm_by(db, scope, ModelUsage.project_id, accs)
    _compute_by(db, scope, ComputeUsage.project_id, accs)
    _tools_by(db, scope, ToolInvocation.project_id, accs)
    return _items(accs, _labels(db, Project.id, Project.name, accs), unattributed="No project")


def _by_experiment(db: Session, scope: _Scope) -> list[CostGroupOut]:
    accs: dict[Any, _Acc] = defaultdict(_Acc)
    _compute_by(db, scope, ComputeUsage.experiment_id, accs)
    items = _items(accs, _labels(db, Experiment.id, Experiment.title, accs), unattributed="No experiment")
    for item in items:
        item.details["llm_attribution"] = "not_linked"  # model calls are attributed to missions/agents
    return items


def _by_model(db: Session, scope: _Scope) -> list[CostGroupOut]:
    tokens = func.coalesce(func.sum(ModelUsage.input_tokens + ModelUsage.output_tokens + ModelUsage.thinking_tokens), 0)
    stmt = select(
        ModelUsage.provider,
        ModelUsage.model,
        func.coalesce(func.sum(ModelUsage.cost_usd), 0),
        func.count(ModelUsage.id),
        tokens,
        func.count(ModelUsage.id).filter(ModelUsage.success.is_(False)),
        func.count(ModelUsage.id).filter(ModelUsage.cost_estimated.is_(True)),
    ).group_by(ModelUsage.provider, ModelUsage.model)
    items = [
        CostGroupOut(
            key=f"{provider}:{model}",
            label=f"{provider} / {model}",
            llm_usd=_dec(cost),
            total_usd=_dec(cost),
            tokens=int(tok),
            calls=int(calls),
            details={"provider": provider, "model": model, "failed_calls": int(failed), "estimated_calls": int(est)},
        )
        for provider, model, cost, calls, tok, failed, est in db.execute(scope.ledger(stmt, ModelUsage)).all()
    ]
    items.sort(key=lambda item: (-item.total_usd, item.label))
    return items


def _by_agent(db: Session, scope: _Scope) -> list[CostGroupOut]:
    accs: dict[Any, _Acc] = defaultdict(_Acc)
    tokens = func.coalesce(func.sum(ModelUsage.input_tokens + ModelUsage.output_tokens + ModelUsage.thinking_tokens), 0)
    llm_stmt = (
        select(AgentRun.role, func.coalesce(func.sum(ModelUsage.cost_usd), 0), func.count(ModelUsage.id), tokens)
        .select_from(ModelUsage)
        .outerjoin(AgentRun, AgentRun.id == ModelUsage.agent_run_id)
        .group_by(AgentRun.role)
    )
    for role, cost, calls, tok in db.execute(scope.ledger(llm_stmt, ModelUsage)).all():
        acc = accs[role]
        acc.llm += _dec(cost)
        acc.calls += int(calls)
        acc.tokens += int(tok)
    tool_stmt = (
        select(AgentRun.role, func.coalesce(func.sum(ToolInvocation.cost_usd), 0), func.count(ToolInvocation.id))
        .select_from(ToolInvocation)
        .outerjoin(AgentRun, AgentRun.id == ToolInvocation.agent_run_id)
        .group_by(AgentRun.role)
    )
    for role, cost, count in db.execute(scope.ledger(tool_stmt, ToolInvocation)).all():
        acc = accs[role]
        acc.tool += _dec(cost)
        acc.details["tool_invocations"] = acc.details.get("tool_invocations", 0) + int(count)
    return _items(accs, {}, unattributed="No agent")


def _by_discovery(db: Session, scope: _Scope) -> list[CostGroupOut]:
    """Compute cost of each discovery's experiments; its mission's LLM and tool costs are shared evenly
    among the mission's discoveries so that totals never double count."""
    stmt = select(Discovery.id, Discovery.title, Discovery.mission_id, Discovery.experiment_ids).where(
        Discovery.organization_id == scope.organization_id
    )
    if scope.project_id is not None:
        stmt = stmt.where(Discovery.project_id == scope.project_id)
    elif scope.visible is not None:
        stmt = stmt.where(Discovery.project_id.in_(scope.visible))
    discoveries = db.execute(stmt.order_by(Discovery.created_at.desc()).limit(MAX_DISCOVERIES)).all()
    if not discoveries:
        return []
    experiment_ids: set[uuid.UUID] = set()
    parsed: dict[uuid.UUID, list[uuid.UUID]] = {}
    per_mission: dict[uuid.UUID, int] = defaultdict(int)
    for discovery_id, _title, mission_id, exp_ids in discoveries:
        ids = []
        for raw in exp_ids or []:
            try:
                ids.append(uuid.UUID(str(raw)))
            except ValueError:
                continue
        parsed[discovery_id] = ids
        experiment_ids.update(ids)
        if mission_id is not None:
            per_mission[mission_id] += 1

    compute_by_exp: dict[uuid.UUID, Decimal] = {}
    if experiment_ids:
        rows = db.execute(
            scope.ledger(
                select(ComputeUsage.experiment_id, func.coalesce(func.sum(ComputeUsage.cost_usd), 0))
                .where(ComputeUsage.experiment_id.in_(experiment_ids))
                .group_by(ComputeUsage.experiment_id),
                ComputeUsage,
            )
        ).all()
        compute_by_exp = {row[0]: _dec(row[1]) for row in rows}
    mission_ids = list(per_mission)
    llm_by_mission: dict[uuid.UUID, tuple[Decimal, int, int]] = {}
    tool_by_mission: dict[uuid.UUID, Decimal] = {}
    if mission_ids:
        tokens = func.coalesce(
            func.sum(ModelUsage.input_tokens + ModelUsage.output_tokens + ModelUsage.thinking_tokens), 0
        )
        for mission_id, cost, calls, tok in db.execute(
            scope.ledger(
                select(ModelUsage.mission_id, func.coalesce(func.sum(ModelUsage.cost_usd), 0), func.count(), tokens)
                .where(ModelUsage.mission_id.in_(mission_ids))
                .group_by(ModelUsage.mission_id),
                ModelUsage,
            )
        ).all():
            llm_by_mission[mission_id] = (_dec(cost), int(calls), int(tok))
        for mission_id, cost in db.execute(
            scope.ledger(
                select(ToolInvocation.mission_id, func.coalesce(func.sum(ToolInvocation.cost_usd), 0))
                .where(ToolInvocation.mission_id.in_(mission_ids))
                .group_by(ToolInvocation.mission_id),
                ToolInvocation,
            )
        ).all():
            tool_by_mission[mission_id] = _dec(cost)

    items: list[CostGroupOut] = []
    for discovery_id, title, mission_id, _exp in discoveries:
        compute = sum((compute_by_exp.get(e, ZERO) for e in parsed[discovery_id]), ZERO)
        llm = tool = ZERO
        calls = tokens_total = 0
        share = per_mission.get(mission_id, 0) if mission_id is not None else 0
        if share and mission_id is not None:
            cost, mission_calls, mission_tokens = llm_by_mission.get(mission_id, (ZERO, 0, 0))
            llm = (cost / share).quantize(Decimal("0.000001"))
            tool = (tool_by_mission.get(mission_id, ZERO) / share).quantize(Decimal("0.000001"))
            calls = mission_calls // share
            tokens_total = mission_tokens // share
        items.append(
            CostGroupOut(
                key=str(discovery_id),
                label=title,
                llm_usd=llm,
                compute_usd=compute,
                tool_usd=tool,
                total_usd=llm + compute + tool,
                tokens=tokens_total,
                calls=calls,
                details={
                    "mission_id": str(mission_id) if mission_id else None,
                    "experiments": len(parsed[discovery_id]),
                    "mission_cost_share": f"1/{share}" if share else None,
                },
            )
        )
    items.sort(key=lambda item: (-item.total_usd, item.label))
    return items


def _by_organization(db: Session, scope: _Scope) -> list[CostGroupOut]:
    llm = _llm_totals(db, scope)
    compute = _compute_totals(db, scope)
    tools = _tool_totals(db, scope)
    return [
        CostGroupOut(
            key=str(scope.organization_id),
            label="Organization",
            llm_usd=llm.cost_usd,
            compute_usd=compute.cost_usd,
            tool_usd=tools.cost_usd,
            total_usd=llm.cost_usd + compute.cost_usd + tools.cost_usd,
            tokens=llm.input_tokens + llm.output_tokens + llm.thinking_tokens,
            calls=llm.calls,
            details={"compute_jobs": compute.jobs, "tool_invocations": tools.invocations},
        )
    ]


_GROUPERS: dict[str, Callable[[Session, _Scope], list[CostGroupOut]]] = {
    "mission": _by_mission,
    "experiment": _by_experiment,
    "discovery": _by_discovery,
    "agent": _by_agent,
    "model": _by_model,
    "project": _by_project,
    "organization": _by_organization,
}


def cost_breakdown(
    db: Session,
    actor: Actor,
    *,
    group_by: str = "mission",
    since: datetime | None = None,
    until: datetime | None = None,
    project_id: uuid.UUID | str | None = None,
    limit: int = 100,
) -> CostBreakdownOut:
    if group_by not in _GROUPERS:
        raise ValidationFailed(f"group_by must be one of {', '.join(GROUP_BY_OPTIONS)}")
    if not 1 <= limit <= 1000:
        raise ValidationFailed("limit must be between 1 and 1000")
    scope = _scope(db, actor, since, until, project_id)
    items = _GROUPERS[group_by](db, scope)
    total = sum((item.total_usd for item in items), ZERO)
    return CostBreakdownOut(
        group_by=group_by,  # type: ignore[arg-type]
        period_start=scope.start,
        period_end=scope.end,
        currency=_currency(),
        total_usd=total,
        items=items[:limit],
    )


def tool_costs(
    db: Session,
    actor: Actor,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    project_id: uuid.UUID | str | None = None,
) -> ToolCostsOut:
    scope = _scope(db, actor, since, until, project_id)
    stmt = select(
        ToolInvocation.tool_name,
        ToolInvocation.tool_source,
        func.count(ToolInvocation.id),
        func.count(ToolInvocation.id).filter(ToolInvocation.status == "succeeded"),
        func.count(ToolInvocation.id).filter(ToolInvocation.status.in_(("failed", "timeout"))),
        func.count(ToolInvocation.id).filter(ToolInvocation.status == "denied"),
        func.coalesce(func.sum(ToolInvocation.cost_usd), 0),
        func.avg(ToolInvocation.latency_ms),
    ).group_by(ToolInvocation.tool_name, ToolInvocation.tool_source)
    items = [
        ToolCostOut(
            tool_name=name,
            tool_source=source,
            invocations=int(total),
            succeeded=int(ok),
            failed=int(failed),
            denied=int(denied),
            cost_usd=_dec(cost),
            avg_latency_ms=float(latency) if latency is not None else None,
        )
        for name, source, total, ok, failed, denied, cost, latency in db.execute(
            scope.ledger(stmt, ToolInvocation)
        ).all()
    ]
    items.sort(key=lambda item: (-item.cost_usd, -item.invocations, item.tool_name))
    return ToolCostsOut(
        period_start=scope.start,
        period_end=scope.end,
        currency=_currency(),
        total_usd=sum((i.cost_usd for i in items), ZERO),
        items=items,
    )


# ---------------------------------------------------------------------------------------------
# Ledger listings (keyset pagination, newest first)
# ---------------------------------------------------------------------------------------------
def _keyset[T](
    db: Session, stmt: Select, params: CursorParams, model: Any, mapper: Callable[[Any], T]
) -> CursorPage[T]:
    if params.cursor:
        data = decode_cursor(params.cursor)
        try:
            at = datetime.fromisoformat(str(data["t"]))
            last_id = uuid.UUID(str(data["i"]))
        except (KeyError, ValueError) as exc:
            raise ValidationFailed("Invalid pagination cursor") from exc
        stmt = stmt.where(or_(model.created_at < at, and_(model.created_at == at, model.id < last_id)))
    rows = db.scalars(stmt.order_by(model.created_at.desc(), model.id.desc()).limit(params.limit + 1)).all()
    has_more = len(rows) > params.limit
    rows = rows[: params.limit]
    cursor = encode_cursor({"t": rows[-1].created_at.isoformat(), "i": str(rows[-1].id)}) if has_more and rows else None
    return CursorPage[T](items=[mapper(r) for r in rows], next_cursor=cursor, limit=params.limit)


def list_model_usage(
    db: Session,
    actor: Actor,
    params: CursorParams,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    project_id: uuid.UUID | str | None = None,
    mission_id: uuid.UUID | str | None = None,
    agent_run_id: uuid.UUID | str | None = None,
    provider: str | None = None,
    model: str | None = None,
    task_type: str | None = None,
    success: bool | None = None,
) -> CursorPage[ModelUsageOut]:
    scope = _scope(db, actor, since, until, project_id)
    stmt = scope.ledger(select(ModelUsage), ModelUsage)
    if mission_id:
        stmt = stmt.where(ModelUsage.mission_id == _as_uuid(mission_id, "mission_id"))
    if agent_run_id:
        stmt = stmt.where(ModelUsage.agent_run_id == _as_uuid(agent_run_id, "agent_run_id"))
    if provider:
        stmt = stmt.where(ModelUsage.provider == provider)
    if model:
        stmt = stmt.where(ModelUsage.model == model)
    if task_type:
        stmt = stmt.where(ModelUsage.task_type == task_type)
    if success is not None:
        stmt = stmt.where(ModelUsage.success.is_(success))
    return _keyset(db, stmt, params, ModelUsage, ModelUsageOut.model_validate)


def list_compute_usage(
    db: Session,
    actor: Actor,
    params: CursorParams,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    project_id: uuid.UUID | str | None = None,
    mission_id: uuid.UUID | str | None = None,
    experiment_id: uuid.UUID | str | None = None,
    backend: str | None = None,
) -> CursorPage[ComputeUsageOut]:
    scope = _scope(db, actor, since, until, project_id)
    stmt = scope.ledger(select(ComputeUsage), ComputeUsage)
    if mission_id:
        stmt = stmt.where(ComputeUsage.mission_id == _as_uuid(mission_id, "mission_id"))
    if experiment_id:
        stmt = stmt.where(ComputeUsage.experiment_id == _as_uuid(experiment_id, "experiment_id"))
    if backend:
        stmt = stmt.where(ComputeUsage.backend == backend)
    return _keyset(db, stmt, params, ComputeUsage, ComputeUsageOut.model_validate)


def _as_uuid(value: uuid.UUID | str, name: str) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise ValidationFailed(f"{name} must be a UUID") from exc
