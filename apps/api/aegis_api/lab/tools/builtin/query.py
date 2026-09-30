"""``lab_query``: structured, read-only queries over the lab's own records (never raw SQL).

The caller picks an ``entity`` from a fixed catalog, equality/``IN`` filters on whitelisted fields and a limit
(≤ 100). The query is built with SQLAlchemy, runs in an RLS-scoped unit of work as the calling actor, is always
restricted to the invocation's project and requires the entity's read permission. Only whitelisted columns are
returned; long text is truncated.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Select, or_, select
from sqlalchemy.orm import InstrumentedAttribute

from aegis_api.errors import Forbidden
from aegis_api.lab.core.access import effective_permissions, load_project
from aegis_api.lab.core.deps import tenant_uow
from aegis_api.lab.models import (
    Experiment,
    ExperimentMetric,
    ExperimentRun,
    Failure,
    Hypothesis,
    Lesson,
    ResearchSource,
    ScientificClaim,
)
from aegis_api.lab.tools.registry import (
    ToolDefinition,
    ToolExecutionContext,
    ToolInputError,
    ToolOutput,
    register_tool,
)
from engines.lab.states import RiskLevel

MAX_LIMIT = 100
MAX_TEXT_CHARS = 1000
MAX_IN_VALUES = 50

Entity = Literal[
    "experiments", "experiment_runs", "metrics", "hypotheses", "failures", "lessons", "claims", "sources"
]
FilterValue = str | int | float | bool | None | list[str | int | float | bool]


@dataclass(frozen=True)
class EntitySpec:
    model: Any
    permission: str
    filters: dict[str, Any]  # public filter name → column
    fields: tuple[str, ...]
    # How the row is tied to a project: a column on the model, or a join through another model.
    project_column: Any
    join: tuple[Any, Any] | None = None  # (model, onclause)
    nullable_project: bool = False


def _cols(model: Any, *names: str) -> dict[str, Any]:
    return {name: getattr(model, name) for name in names}


ENTITIES: dict[str, EntitySpec] = {
    "experiments": EntitySpec(
        model=Experiment,
        permission="experiment:read",
        filters=_cols(Experiment, "id", "mission_id", "hypothesis_id", "status", "kind", "baseline_experiment_id"),
        fields=(
            "id", "mission_id", "hypothesis_id", "title", "kind", "status", "status_reason", "current_version_id",
            "baseline_experiment_id", "parent_experiment_id", "retry_count", "created_at", "updated_at",
        ),
        project_column=Experiment.project_id,
    ),
    "experiment_runs": EntitySpec(
        model=ExperimentRun,
        permission="experiment:read",
        filters=_cols(ExperimentRun, "id", "experiment_id", "experiment_version_id", "mission_id", "status", "role", "seed"),
        fields=(
            "id", "experiment_id", "experiment_version_id", "run_number", "role", "seed", "status", "exit_code",
            "metrics", "duration_seconds", "cost_usd", "started_at", "completed_at", "created_at",
        ),
        project_column=ExperimentRun.project_id,
    ),
    "metrics": EntitySpec(
        model=ExperimentMetric,
        permission="experiment:read",
        filters={
            **_cols(ExperimentMetric, "experiment_run_id", "name", "split", "source"),
            "experiment_id": ExperimentRun.experiment_id,
        },
        fields=("id", "experiment_run_id", "name", "value", "step", "split", "source", "evaluation_run_id", "created_at"),
        project_column=ExperimentRun.project_id,
        join=(ExperimentRun, ExperimentRun.id == ExperimentMetric.experiment_run_id),
    ),
    "hypotheses": EntitySpec(
        model=Hypothesis,
        permission="hypothesis:read",
        filters=_cols(Hypothesis, "id", "mission_id", "status", "generation", "parent_hypothesis_id"),
        fields=(
            "id", "mission_id", "statement", "rationale", "expected_outcome", "status", "feasibility", "confidence",
            "scores", "selection_rank", "generation", "parent_hypothesis_id", "created_at",
        ),
        project_column=Hypothesis.project_id,
    ),
    "failures": EntitySpec(
        model=Failure,
        permission="failure:read",
        filters=_cols(
            Failure, "id", "mission_id", "experiment_id", "experiment_run_id", "failure_type", "status",
            "recovery_status", "signature",
        ),
        fields=(
            "id", "mission_id", "experiment_id", "experiment_run_id", "failure_type", "signature", "title",
            "root_cause", "confidence", "recovery_action", "recovery_status", "recurrence_count", "status",
            "created_at",
        ),
        project_column=Failure.project_id,
        nullable_project=True,
    ),
    "lessons": EntitySpec(
        model=Lesson,
        permission="failure:read",
        filters=_cols(Lesson, "id", "failure_type", "status", "source", "signature"),
        fields=(
            "id", "failure_type", "signature", "statement", "recommendation", "confidence", "status", "source",
            "times_applied", "times_succeeded", "created_at",
        ),
        project_column=Lesson.project_id,
        nullable_project=True,
    ),
    "claims": EntitySpec(
        model=ScientificClaim,
        permission="claim:read",
        filters=_cols(ScientificClaim, "id", "mission_id", "status", "claim_type", "metric", "source_id"),
        fields=(
            "id", "mission_id", "statement", "claim_type", "metric", "direction", "value", "ci_low", "ci_high",
            "units", "status", "confidence", "criteria_profile", "created_at",
        ),
        project_column=ScientificClaim.project_id,
    ),
    "sources": EntitySpec(
        model=ResearchSource,
        permission="research:read",
        filters=_cols(ResearchSource, "id", "research_task_id", "source_type", "status", "doi"),
        fields=(
            "id", "source_type", "title", "url", "doi", "publisher", "publication_date", "abstract", "status",
            "trust_metadata", "research_task_id", "created_at",
        ),
        project_column=ResearchSource.project_id,
    ),
}


class LabQueryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity: Entity = Field(description="Which records to read")
    filters: dict[str, FilterValue] = Field(
        default_factory=dict,
        max_length=8,
        description="Equality filters (a list means 'any of') on the entity's whitelisted fields",
    )
    order: Literal["newest", "oldest"] = "newest"
    limit: int = Field(default=20, ge=1, le=MAX_LIMIT)


def _python_type(column: Any) -> type | None:
    try:
        return column.type.python_type  # type: ignore[no-any-return]
    except (NotImplementedError, AttributeError):
        return None


def _coerce(name: str, column: Any, value: Any) -> Any:
    kind = _python_type(column)
    if value is None:
        return None
    if isinstance(value, bool) and kind is not bool:
        raise ToolInputError(f"filter '{name}' does not accept booleans")
    try:
        if kind is uuid.UUID:
            return uuid.UUID(str(value))
        if kind is int:
            if isinstance(value, float) and not value.is_integer():
                raise ValueError("not an integer")
            return int(value)
        if kind is float:
            return float(value)
        if kind is bool:
            if not isinstance(value, bool):
                raise ValueError("not a boolean")
            return value
    except (TypeError, ValueError) as exc:
        raise ToolInputError(f"invalid value for filter '{name}'") from exc
    text = str(value)
    if len(text) > 200:
        raise ToolInputError(f"filter '{name}' value is too long")
    return text


def _apply_filters(stmt: Select[Any], spec: EntitySpec, filters: dict[str, Any]) -> Select[Any]:
    for name, raw in filters.items():
        column = spec.filters.get(name)
        if column is None:
            allowed = ", ".join(sorted(spec.filters))
            raise ToolInputError(f"unknown filter '{name}' (allowed: {allowed})")
        if isinstance(raw, list):
            if not raw:
                raise ToolInputError(f"filter '{name}' needs at least one value")
            if len(raw) > MAX_IN_VALUES:
                raise ToolInputError(f"filter '{name}' accepts at most {MAX_IN_VALUES} values")
            values = [_coerce(name, column, v) for v in raw]
            stmt = stmt.where(column.in_(values))
        else:
            value = _coerce(name, column, raw)
            stmt = stmt.where(column.is_(None) if value is None else column == value)
    return stmt


def _jsonable(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, str) and len(value) > MAX_TEXT_CHARS:
        return value[:MAX_TEXT_CHARS] + "…"
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in list(value.items())[:50]}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in list(value)[:50]]
    return value


def build_query(spec: EntitySpec, project_id: uuid.UUID, filters: dict[str, Any], order: str, limit: int) -> Select[Any]:
    stmt = select(spec.model)
    if spec.join is not None:
        stmt = stmt.join(spec.join[0], spec.join[1])
    if spec.nullable_project:
        stmt = stmt.where(or_(spec.project_column == project_id, spec.project_column.is_(None)))
    else:
        stmt = stmt.where(spec.project_column == project_id)
    stmt = _apply_filters(stmt, spec, filters)
    created: InstrumentedAttribute[Any] = spec.model.created_at
    ident: InstrumentedAttribute[Any] = spec.model.id
    ordering = (created.desc(), ident.desc()) if order == "newest" else (created.asc(), ident.asc())
    return stmt.order_by(*ordering).limit(limit)


def _lab_query(ctx: ToolExecutionContext, args: LabQueryInput) -> ToolOutput:
    spec = ENTITIES[args.entity]
    ctx.check()
    with tenant_uow(ctx.actor) as db:
        project = load_project(db, ctx.actor, ctx.project_id)
        if spec.permission not in effective_permissions(db, ctx.actor, project):
            raise Forbidden(f"Querying {args.entity} requires the '{spec.permission}' permission")
        stmt = build_query(spec, project.id, dict(args.filters), args.order, args.limit)
        stmt = stmt.where(spec.model.organization_id == ctx.organization_id)
        rows = db.scalars(stmt).all()
        items = [{name: _jsonable(getattr(row, name, None)) for name in spec.fields} for row in rows]
    return ToolOutput(
        content={"entity": args.entity, "count": len(items), "rows": items},
        metadata={"entity": args.entity, "count": len(items)},
    )


LAB_QUERY = register_tool(
    ToolDefinition(
        name="lab_query",
        description=(
            "Read-only structured query over this project's lab records: experiments, experiment_runs, metrics, "
            "hypotheses, failures, lessons, claims or sources. Filters are equality (or 'any of' lists) on "
            "whitelisted fields; at most 100 rows."
        ),
        input_model=LabQueryInput,
        handler=_lab_query,
        risk_level=RiskLevel.LOW,
        permissions=frozenset(),
        rate_limit_per_min=240,
        category="data",
    )
)
