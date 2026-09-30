"""Shared helpers of the verification context (batched loading, metrics provenance, anchoring, lazy
cross-context calls, claim state paths).

Nothing here makes a scientific decision: decisions live in ``engines.lab`` (verification criteria, lineage,
review, reports, reproduction evaluator). These helpers only load and normalise the stored facts those
engines consume.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import uuid
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from types import ModuleType
from typing import Any, Literal

import structlog
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis_api.errors import ValidationFailed
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.evidence import append_evidence
from aegis_api.lab.models import ExperimentComparison, ExperimentMetric, ExperimentRun
from aegis_api.models import Evidence
from engines.lab.comparison import ComparisonResult, ComparisonStatistics
from engines.lab.evaluators.base import RunRecord
from engines.lab.experiment_spec import ExperimentSpec
from engines.lab.states import CLAIM_TRANSITIONS, ExecutionStatus, assert_transition

log = structlog.get_logger("aegis.lab.verification")

SUCCESS_RUN_STATUSES: frozenset[str] = frozenset(
    {ExecutionStatus.SUCCEEDED, ExecutionStatus.VERIFICATION_PENDING, ExecutionStatus.VERIFIED}
)
TERMINAL_RUN_STATUSES: frozenset[str] = SUCCESS_RUN_STATUSES | {
    ExecutionStatus.FAILED,
    ExecutionStatus.TIMED_OUT,
    ExecutionStatus.CANCELLED,
}
#: Lower rank wins when a metric was measured by several sources.
METRIC_SOURCE_RANK: dict[str, int] = {"evaluator": 0, "platform": 1, "self_reported": 2}
MetricSource = Literal["platform", "evaluator", "self_reported"]


# ---------------------------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------------------------
def to_uuid(value: uuid.UUID | str | None, label: str) -> uuid.UUID:
    """Parse an id from a payload (422 when malformed)."""
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise ValidationFailed(f"Invalid {label} id") from exc


def uuid_set(values: Iterable[Any]) -> set[uuid.UUID]:
    """Best-effort conversion of stored id strings to UUIDs (malformed entries are skipped)."""
    out: set[uuid.UUID] = set()
    for value in values:
        if value is None:
            continue
        if isinstance(value, uuid.UUID):
            out.add(value)
            continue
        try:
            out.add(uuid.UUID(str(value)))
        except ValueError:
            continue
    return out


def jsonable(value: Any) -> Any:
    """JSON-safe copy (UUID/Decimal/datetime → str/float/iso; non-finite floats → None)."""
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [jsonable(v) for v in value]
    return value


def canonical_hash(value: Any) -> str:
    text = json.dumps(jsonable(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def clean_text(text: str | None, limit: int) -> str:
    cleaned = " ".join((text or "").split())
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1] + "…"


def optional_module(name: str) -> ModuleType | None:
    """Import a sibling context module that may not be deployed yet (``None`` when it does not exist)."""
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name is not None and (exc.name == name or name.startswith(exc.name + ".")):
            return None
        raise


def get_many[T](db: Session, model: type[T], ids: Iterable[Any], organization_id: uuid.UUID) -> dict[uuid.UUID, T]:
    """Load rows by id in one query (RLS-scoped session; organization re-checked defensively)."""
    keys = uuid_set(ids)
    if not keys:
        return {}
    column = model.id  # type: ignore[attr-defined]
    rows = db.scalars(select(model).where(column.in_(sorted(keys)))).all()
    return {row.id: row for row in rows if getattr(row, "organization_id", organization_id) == organization_id}  # type: ignore[attr-defined]


def anchor(db: Session, organization_id: uuid.UUID, kind: str, title: str, content: dict[str, Any]) -> Evidence:
    """Append an immutable, hash-chained evidence record (kind ≤ 28 characters)."""
    if len(kind) > 28:
        raise ValueError("evidence kind must be at most 28 characters")
    return append_evidence(
        db, organization_id=organization_id, kind=kind, title=clean_text(title, 300), content=jsonable(content)
    )


# ---------------------------------------------------------------------------------------------
# Claim status paths
# ---------------------------------------------------------------------------------------------
def claim_status_path(current: str, target: str) -> list[str]:
    """Shortest legal path of claim states from ``current`` to ``target`` (excluding ``current``)."""
    if current == target:
        return []
    queue: deque[tuple[str, list[str]]] = deque([(current, [])])
    seen = {current}
    while queue:
        state, path = queue.popleft()
        for nxt in sorted(CLAIM_TRANSITIONS.get(state, frozenset())):
            if nxt in seen:
                continue
            step = [*path, nxt]
            if nxt == target:
                return step
            seen.add(nxt)
            queue.append((nxt, step))
    assert_transition("claim", current, target)  # raises InvalidTransitionError with the allowed set
    return [target]  # pragma: no cover - assert_transition always raises here


def walk_claim_status(current: str, target: str) -> list[str]:
    """Validate every hop with ``assert_transition`` and return the visited states."""
    path = claim_status_path(current, target)
    state = current
    for nxt in path:
        assert_transition("claim", state, nxt)
        state = nxt
    return path


# ---------------------------------------------------------------------------------------------
# Metrics with provenance
# ---------------------------------------------------------------------------------------------
@dataclass
class RunMetrics:
    values: dict[str, float] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)


def load_run_metrics(db: Session, runs: Iterable[ExperimentRun]) -> dict[uuid.UUID, RunMetrics]:
    """Final metric values per run, preferring evaluator- over platform- over self-reported measurements.

    ``experiment_metrics`` rows are the source of truth; the run's ``metrics`` JSON (as reported by the
    experiment code) is only used for names without a row, and is labelled ``self_reported``.
    """
    run_list = list(runs)
    out: dict[uuid.UUID, RunMetrics] = {run.id: RunMetrics() for run in run_list}
    if not run_list:
        return out
    rows = db.scalars(
        select(ExperimentMetric)
        .where(ExperimentMetric.experiment_run_id.in_([r.id for r in run_list]))
        .order_by(ExperimentMetric.created_at, ExperimentMetric.id)
    ).all()
    best: dict[tuple[uuid.UUID, str], tuple[int, int, float, str]] = {}
    for row in rows:
        if row.value is None or not math.isfinite(float(row.value)):
            continue
        rank = METRIC_SOURCE_RANK.get(row.source, 3)
        step = row.step if row.step is not None else 1 << 30  # a final value (no step) beats any step
        key = (row.experiment_run_id, row.name)
        current = best.get(key)
        # lower source rank wins; within a source the highest step wins; later rows win ties
        if current is None or (rank, -step) <= (current[0], -current[1]):
            best[key] = (rank, step, float(row.value), row.source)
    for (run_id, name), (_rank, _step, value, source) in best.items():
        out[run_id].values[name] = value
        out[run_id].sources[name] = source
    for run in run_list:
        target = out[run.id]
        for name, value in (run.metrics or {}).items():
            if name in target.values or isinstance(value, bool) or not isinstance(value, int | float):
                continue
            if math.isfinite(float(value)):
                target.values[str(name)] = float(value)
                target.sources[str(name)] = "self_reported"
    return out


def run_record(run: ExperimentRun, metrics: RunMetrics | None) -> RunRecord:
    return RunRecord(
        run_id=str(run.id),
        seed=run.seed,
        status=run.status,
        metrics=dict(metrics.values) if metrics else {},
    )


def metric_sources(metrics: Iterable[RunMetrics]) -> dict[str, MetricSource]:
    """Weakest source per metric across runs (a metric is only as trustworthy as its weakest measurement)."""
    worst: dict[str, str] = {}
    for item in metrics:
        for name, source in item.sources.items():
            if name not in worst or METRIC_SOURCE_RANK.get(source, 3) > METRIC_SOURCE_RANK.get(worst[name], 3):
                worst[name] = source
    return {k: v for k, v in worst.items() if v in ("platform", "evaluator", "self_reported")}  # type: ignore[misc]


# ---------------------------------------------------------------------------------------------
# Stored specs / comparisons → engine models
# ---------------------------------------------------------------------------------------------
def parse_spec(spec: Mapping[str, Any] | None) -> ExperimentSpec | None:
    if not spec:
        return None
    try:
        return ExperimentSpec.model_validate(dict(spec))
    except ValidationError:
        return None


def comparison_statistics(stats: Mapping[str, Any] | None) -> ComparisonStatistics | None:
    """Stored statistics → the engine model (extra keys such as ``metric_source`` are ignored)."""
    if not stats:
        return None
    allowed = set(ComparisonStatistics.model_fields)
    try:
        return ComparisonStatistics.model_validate({k: v for k, v in stats.items() if k in allowed})
    except ValidationError:
        return None


def comparison_result(comparison: ExperimentComparison) -> ComparisonResult | None:
    stats = comparison_statistics(comparison.statistics)
    if stats is None or comparison.direction not in ("maximize", "minimize"):
        return None
    verdict = comparison.verdict
    if verdict not in ("improved", "regressed", "no_significant_difference", "insufficient_data"):
        return None
    rationale = str((comparison.statistics or {}).get("rationale") or "")
    return ComparisonResult(
        metric=comparison.metric,
        direction=comparison.direction,  # type: ignore[arg-type]
        statistics=stats,
        verdict=verdict,  # type: ignore[arg-type]
        rationale=rationale,
        engine_version=comparison.evaluator_version or "unknown",
    )


def statistic(stats: Mapping[str, Any] | None, name: str) -> float | None:
    value = (stats or {}).get(name)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(float(value)) else None


# ---------------------------------------------------------------------------------------------
# Lazy workflow launch
# ---------------------------------------------------------------------------------------------
def launch_flow(
    db: Session,
    actor: Actor,
    kind: str,
    *,
    subject_type: str,
    subject_id: uuid.UUID | str,
    flow_input: dict[str, Any],
    project_id: uuid.UUID | None,
    mission_id: uuid.UUID | None,
    workflow_key: str = "default",
) -> uuid.UUID | None:
    """Launch ``kind`` after commit when the workflow engine and the flow are deployed; otherwise ``None``.

    The caller keeps its record in a resumable state (activities and API endpoints can drive it).
    """
    launcher = optional_module("aegis_api.lab.workflows.launcher")
    definitions = optional_module("aegis_api.lab.workflows.definitions")
    if launcher is None or definitions is None:
        log.info("workflow_launch_skipped", kind=kind, reason="workflow engine not installed")
        return None
    if not definitions.has_flow(kind):
        log.info("workflow_launch_skipped", kind=kind, reason="flow not registered")
        return None
    run = launcher.launch_workflow(
        db,
        actor,
        kind,
        subject_type=subject_type,
        subject_id=str(subject_id),
        input=jsonable(flow_input),
        project_id=project_id,
        mission_id=mission_id,
        workflow_key=workflow_key,
    )
    run_id: uuid.UUID = run.id
    return run_id
