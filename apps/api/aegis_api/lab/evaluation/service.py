"""Evaluation service: the evaluator registry and platform-side, independent evaluation runs.

Evaluator registry
------------------
* Built-in evaluators (``organization_id`` NULL) are seeded at start-up from
  :func:`engines.lab.evaluators.evaluator_manifest` by :func:`seed_evaluators`. Rows are immutable: an existing
  ``(key, version)`` whose configuration hash differs from the engine's is a conflict (logged and skipped — the
  engine must bump the version instead). Built-in versions the engine no longer executes are marked
  ``deprecated`` (``status`` is the only mutable column).
* Organizations add their own immutable evaluator versions (:func:`create_evaluator`, humans only): a validated
  configuration of one built-in kind — e.g. a ``custom`` rule whose expression is compiled by the safe AST walker
  of :mod:`engines.lab.evaluators.expressions` (unsafe expressions are rejected before anything is stored).
* Resolution (:func:`resolve_evaluator`): an organization evaluator overrides the built-in with the same key;
  without an explicit version the latest active (semantic-version order) is used.

Evaluation runs
---------------
An evaluation is planned inside the tenant session (:func:`prepare_evaluation`: subject ownership, permissions,
evaluator/config resolution, the :class:`~engines.lab.evaluators.ExperimentView` of the runs, and the authorised
input objects), then the input bytes are read from object storage (:func:`load_bundle`), the pure evaluator runs
**platform-side** (never inside the experiment sandbox, so candidate code never sees held-out labels read from
``evaluator_only`` dataset splits), and the outcome is persisted (:func:`persist_evaluation`): the
``evaluation_runs`` row (metrics, verdict, confidence, warnings, evidence with the SHA-256 of every input), evaluator-
sourced ``experiment_metrics`` rows for metrics the evaluator *recomputed from raw data*, an immutable evidence
record and an ``EVALUATION_COMPLETED`` event.

``independent`` on a run is true only when (a) the evaluator is not controlled by the agent that generated the
result (built-in or human-configured evaluator, no agent-supplied configuration) **and** (b) the verdict does not
rest on numbers self-reported by the experiment code.

Metric provenance: for each run and metric the view uses the most trusted measurement available
(``evaluator`` > ``platform`` > ``self_reported``); the view's ``metric_sources`` records the weakest source used
across the runs, so a verdict relying on any self-reported number is never labelled independent.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import tarfile
import time
import uuid
import zlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

import structlog
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.errors import AppError, Conflict, NotFound, PayloadTooLarge, ServiceUnavailable, ValidationFailed
from aegis_api.lab.core.access import get_owned, load_project, visible_project_ids
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.audit import audit
from aegis_api.lab.core.deps import tenant_uow
from aegis_api.lab.core.events import EventType, emit
from aegis_api.lab.core.evidence import append_evidence
from aegis_api.lab.core.locks import advisory_xact_lock
from aegis_api.lab.core.pagination import CursorPage, CursorParams, paginate, paginate_keyset
from aegis_api.lab.evaluation.schemas import EvaluatorCreate
from aegis_api.lab.models import (
    Artifact,
    CodeSnapshot,
    ComputeJob,
    DatasetVersion,
    EvaluationRun,
    Experiment,
    ExperimentComparison,
    ExperimentMetric,
    ExperimentRun,
    ExperimentVersion,
    LabEvaluator,
    Project,
)
from aegis_api.lab.observability.tracing import span
from aegis_api.lab.storage import get_storage
from aegis_api.schemas.common import Page, PageParams
from engines.lab.evaluators import (
    BUILTIN_EVALUATORS,
    ArtifactBundle,
    EvalContext,
    EvaluationResult,
    EvaluatorConfigError,
    ExperimentView,
    ResourceUsage,
    RunRecord,
    evaluator_manifest,
    get_evaluator,
)
from engines.lab.evaluators.base import INDEPENDENT_SOURCES
from engines.lab.experiment_spec import ExperimentSpec, canonical_json
from engines.lab.states import ExecutionStatus, RunState, assert_transition

log = structlog.get_logger("aegis.lab.evaluation")

#: Evaluations whose inputs total at most this many bytes run inline in the request; larger ones are queued.
INLINE_INPUT_LIMIT_BYTES = 5 * 1024 * 1024
#: Hard upper bound on the bytes one evaluation may read (predictions + held-out labels + code).
MAX_EVALUATION_INPUT_BYTES = 128 * 1024 * 1024
MAX_CODE_ARCHIVE_BYTES = 16 * 1024 * 1024
MAX_CODE_FILES = 500
MAX_CODE_FILE_BYTES = 1024 * 1024
MAX_CODE_TOTAL_BYTES = 8 * 1024 * 1024
MAX_WARNINGS = 100
MAX_WARNING_CHARS = 1000
EVALUATION_WORKFLOW = "EvaluationWorkflow"
EVALUATOR_CREATED = "EVALUATOR_CREATED"
HELDOUT_DATA_READ = "EVALUATOR_HELDOUT_READ"

BUILTIN_KINDS: frozenset[str] = frozenset(BUILTIN_EVALUATORS)
#: Evaluators that recompute metrics from raw data (their metrics become evaluator-sourced metric rows).
RECOMPUTING_KINDS: frozenset[str] = frozenset({"classification", "regression"})
#: Per-run evaluators that read files: kind → config fields naming (predictions, reference) files.
FILE_EVALUATORS: dict[str, tuple[str, str]] = {
    "classification": ("predictions_file", "labels_file"),
    "regression": ("predictions_file", "targets_file"),
}
#: Evaluators comparing a candidate against a baseline sample.
BASELINE_KINDS: frozenset[str] = frozenset({"benchmark", "statistical"})
_BOOKKEEPING_METRICS = frozenset({"n", "mape_excluded_rows"})
_SOURCE_RANK = {"evaluator": 0, "platform": 1, "self_reported": 2}
_HELDOUT_ROLE_ORDER = {"test": 0, "eval": 1, "validation": 2, "train": 3}
_TERMINAL = frozenset({RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED})

Mode = Literal["inline", "workflow", "activity"]


# =============================================================================================
# Helpers
# =============================================================================================
def _uuid(value: uuid.UUID | str | None) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def semver_key(version: str) -> tuple[int, ...]:
    """Sort key of an ``x.y.z`` version (non-numeric parts sort first)."""
    parts: list[int] = []
    for part in str(version).split("."):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(-1)
    return tuple(parts)


def _deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = dict(base)
    for key, value in override.items():
        current = out.get(key)
        if isinstance(value, Mapping) and isinstance(current, Mapping):
            out[key] = _deep_merge(current, value)
        else:
            out[key] = value
    return out


def config_digest(key: str, config: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical ``{"key", "config"}`` document (equals the engine registry's ``config_hash``)."""
    return hashlib.sha256(canonical_json({"key": key, "config": dict(config)}).encode("utf-8")).hexdigest()


def _clip_warnings(items: Iterable[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        text = str(item)
        if text and text not in out:
            out.append(text[:MAX_WARNING_CHARS])
        if len(out) >= MAX_WARNINGS:
            break
    return out


def _finite_nonneg(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) and number >= 0 else None


def _finite_pos(value: Any) -> float | None:
    number = _finite_nonneg(value)
    return number if number is not None and number > 0 else None


def _engine_version(kind: str) -> str:
    return BUILTIN_EVALUATORS[kind][1]


def evaluator_info(kind: str) -> dict[str, Any]:
    """Presentation facts of an evaluator kind (name, independence) from the engine registry."""
    entry = BUILTIN_EVALUATORS.get(kind)
    if entry is None:
        return {"name": kind, "independent": False}
    cls = entry[0]
    return {"name": cls.name, "independent": bool(cls.independent)}


# =============================================================================================
# Registry: seeding, creation, resolution
# =============================================================================================
def seed_evaluators(session: Session) -> dict[str, int]:
    """Upsert the built-in evaluators (organization NULL). Idempotent; runs on the owner/admin connection.

    Existing ``(key, version)`` rows are never modified except ``status``: a differing configuration hash is a
    conflict (logged and skipped). Built-in versions the engine no longer executes become ``deprecated``.
    """
    inserted = unchanged = conflicts = deprecated = 0
    current: dict[str, str] = {}
    for entry in evaluator_manifest():
        key, version, kind = str(entry["key"]), str(entry["version"]), str(entry["kind"])
        config: dict[str, Any] = dict(entry["default_config"] or {})
        digest = config_digest(key, config)
        current[key] = version
        existing = session.scalar(
            select(LabEvaluator).where(
                LabEvaluator.organization_id.is_(None), LabEvaluator.key == key, LabEvaluator.version == version
            )
        )
        if existing is None:
            session.execute(
                insert(LabEvaluator)
                .values(
                    id=uuid.uuid4(),
                    organization_id=None,
                    key=key,
                    version=version,
                    kind=kind,
                    description=str(entry.get("description") or "")[:4000] or None,
                    config=config,
                    config_hash=digest,
                    status="active",
                    created_at=utcnow(),
                )
                .on_conflict_do_nothing(constraint="uq_lab_evaluators_key_version")
            )
            inserted += 1
        elif existing.config_hash != digest or existing.kind != kind:
            conflicts += 1
            log.error(
                "evaluator_seed_conflict",
                key=key,
                version=version,
                stored_hash=existing.config_hash,
                engine_hash=digest,
                hint="bump the evaluator version instead of changing a released configuration",
            )
        else:
            unchanged += 1
            if existing.status != "active":
                existing.status = "active"
    for row in session.scalars(
        select(LabEvaluator).where(LabEvaluator.organization_id.is_(None), LabEvaluator.status == "active")
    ):
        if current.get(row.key) != row.version:
            row.status = "deprecated"
            deprecated += 1
    session.flush()
    result = {"inserted": inserted, "unchanged": unchanged, "conflicts": conflicts, "deprecated": deprecated}
    log.info("evaluators_seeded", **result)
    return result


def create_evaluator(db: Session, actor: Actor, data: Any) -> LabEvaluator:
    """Create an immutable organization evaluator version (humans with ``evaluation:run``; 409 on duplicates)."""
    actor.require_human("creating an evaluator")
    actor.require("evaluation:run")
    try:
        body = data if isinstance(data, EvaluatorCreate) else EvaluatorCreate.model_validate(data)
    except PydanticValidationError as exc:
        raise ValidationFailed(
            "Invalid evaluator definition",
            details=[{"field": ".".join(map(str, e["loc"])), "message": e["msg"]} for e in exc.errors()],
        ) from exc
    if body.key in BUILTIN_KINDS and body.kind != body.key:
        raise ValidationFailed(
            f"An organization evaluator named '{body.key}' overrides the built-in and must have kind '{body.key}'"
        )
    engine = get_evaluator(body.kind)
    try:
        normalized = engine.parse_config(body.config).model_dump(mode="json")
    except EvaluatorConfigError as exc:
        raise ValidationFailed(str(exc), code="invalid_evaluator_config") from exc
    advisory_xact_lock(db, f"lab-evaluator:{actor.organization_id}:{body.key}")
    duplicate = db.scalar(
        select(LabEvaluator.id).where(
            LabEvaluator.organization_id == actor.organization_id,
            LabEvaluator.key == body.key,
            LabEvaluator.version == body.version,
        )
    )
    if duplicate is not None:
        raise Conflict(
            f"Evaluator '{body.key}' version {body.version} already exists; versions are immutable — create a new one",
            code="evaluator_version_exists",
        )
    row = LabEvaluator(
        id=uuid.uuid4(),
        organization_id=actor.organization_id,
        key=body.key,
        version=body.version,
        kind=body.kind,
        description=body.description,
        config=normalized,
        config_hash=config_digest(body.kind, normalized),
        status="active",
    )
    db.add(row)
    db.flush()
    audit(
        db,
        actor,
        EVALUATOR_CREATED,
        "lab_evaluator",
        row.id,
        after={"key": row.key, "version": row.version, "kind": row.kind, "config_hash": row.config_hash},
    )
    return row


def _visible_evaluators(db: Session, actor: Actor) -> Any:
    return select(LabEvaluator).where(
        (LabEvaluator.organization_id.is_(None)) | (LabEvaluator.organization_id == actor.organization_id)
    )


def list_evaluators(
    db: Session,
    actor: Actor,
    params: PageParams,
    *,
    kind: str | None = None,
    key: str | None = None,
    include_deprecated: bool = False,
    mapper: Any = None,
) -> Page[Any]:
    """Built-in and organization evaluators (``evaluation:read``), ordered by key then newest first."""
    actor.require("evaluation:read")
    stmt = _visible_evaluators(db, actor)
    if kind is not None:
        stmt = stmt.where(LabEvaluator.kind == kind)
    if key is not None:
        stmt = stmt.where(LabEvaluator.key == key)
    if not include_deprecated:
        stmt = stmt.where(LabEvaluator.status == "active")
    stmt = stmt.order_by(LabEvaluator.key.asc(), LabEvaluator.created_at.desc(), LabEvaluator.id.asc())
    return paginate(db, stmt, params, mapper or (lambda row: row))


def evaluator_versions(db: Session, actor: Actor, key: str) -> list[LabEvaluator]:
    """Every version of ``key`` visible to the organization (organization rows first, newest version first)."""
    actor.require("evaluation:read")
    rows = list(db.scalars(_visible_evaluators(db, actor).where(LabEvaluator.key == key)))
    if not rows:
        raise NotFound("Evaluator not found")
    rows.sort(key=lambda r: (r.organization_id is not None, semver_key(r.version)), reverse=True)
    return rows


def resolve_evaluator(db: Session, actor: Actor, key: str, version: str | None = None) -> LabEvaluator:
    """Organization evaluator > built-in; latest active version unless ``version`` pins one."""
    rows = [
        r
        for r in db.scalars(_visible_evaluators(db, actor).where(LabEvaluator.key == key))
        if r.organization_id in (None, actor.organization_id)
    ]
    if version is not None:
        pinned = [r for r in rows if r.version == version]
        if not pinned:
            raise NotFound(f"Evaluator '{key}' version {version} not found")
        pinned.sort(key=lambda r: r.organization_id is not None, reverse=True)
        return pinned[0]
    active = [r for r in rows if r.status == "active"]
    org_rows = [r for r in active if r.organization_id is not None]
    candidates = org_rows or active
    if not candidates:
        raise NotFound(f"Evaluator '{key}' not found")
    return max(candidates, key=lambda r: semver_key(r.version))


# =============================================================================================
# Planning
# =============================================================================================
@dataclass(frozen=True)
class InputRef:
    """One authorised input object for an evaluation (resolved inside the tenant session)."""

    name: str
    source: Literal["artifact_version", "dataset_split", "code_snapshot"]
    ref_id: str
    storage_key: str
    size_bytes: int
    sha256: str | None
    split: str | None = None
    visibility: str | None = None
    manifest: dict[str, Any] = field(default_factory=dict)

    def describe(self) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "name": self.name,
            "source": self.source,
            "id": self.ref_id,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }
        if self.split is not None:
            entry["split"] = self.split
        if self.visibility is not None:
            entry["visibility"] = self.visibility
        return entry


@dataclass
class EvaluationPlan:
    """Everything needed to evaluate, as plain data (safe to carry across short transactions)."""

    organization_id: uuid.UUID
    workspace_id: uuid.UUID
    project_id: uuid.UUID
    mission_id: uuid.UUID | None
    experiment_id: uuid.UUID | None
    experiment_run_id: uuid.UUID | None
    comparison_id: uuid.UUID | None
    experiment_version_id: uuid.UUID | None
    evaluator_id: uuid.UUID
    evaluator_key: str
    evaluator_version: str
    evaluator_kind: str
    evaluator_org_id: uuid.UUID | None
    evaluator_config_hash: str
    config: dict[str, Any]
    config_hash: str
    request: dict[str, Any]
    view: ExperimentView
    context: EvalContext
    inputs: list[InputRef]
    warnings: list[str]
    evaluator_independent: bool
    independence_notes: list[str]

    @property
    def total_bytes(self) -> int:
        return sum(ref.size_bytes for ref in self.inputs)

    @property
    def subject(self) -> dict[str, str | None]:
        return {
            "experiment_id": str(self.experiment_id) if self.experiment_id else None,
            "experiment_run_id": str(self.experiment_run_id) if self.experiment_run_id else None,
            "comparison_id": str(self.comparison_id) if self.comparison_id else None,
            "experiment_version_id": str(self.experiment_version_id) if self.experiment_version_id else None,
        }

    @property
    def heldout_inputs(self) -> list[InputRef]:
        return [ref for ref in self.inputs if ref.visibility == "evaluator_only"]


@dataclass
class _Subject:
    project: Project
    experiment: Experiment | None
    version: ExperimentVersion | None
    runs: list[ExperimentRun]
    baseline_runs: list[ExperimentRun]
    run: ExperimentRun | None = None
    comparison: ExperimentComparison | None = None
    warnings: list[str] = field(default_factory=list)


def _parse_spec(version: ExperimentVersion | None, warnings: list[str]) -> ExperimentSpec | None:
    if version is None or not version.spec:
        return None
    try:
        return ExperimentSpec.model_validate(version.spec)
    except PydanticValidationError:
        warnings.append("the experiment version's stored spec does not parse; evaluating without it")
        return None


def _version_runs(db: Session, version_id: uuid.UUID | None) -> list[ExperimentRun]:
    if version_id is None:
        return []
    return list(
        db.scalars(
            select(ExperimentRun)
            .where(ExperimentRun.experiment_version_id == version_id)
            .order_by(ExperimentRun.run_number.asc(), ExperimentRun.created_at.asc(), ExperimentRun.id.asc())
        )
    )


def _baseline_runs(
    db: Session, actor: Actor, experiment: Experiment, version: ExperimentVersion, spec: ExperimentSpec | None
) -> list[ExperimentRun]:
    """Runs of the baseline experiment's current version, else runs of this version with role ``baseline``."""
    baseline_id = experiment.baseline_experiment_id
    if baseline_id is None and spec is not None and spec.baseline.kind == "experiment":
        baseline_id = _uuid(spec.baseline.experiment_id)
    if baseline_id is not None and baseline_id != experiment.id:
        baseline = db.get(Experiment, baseline_id)
        if (
            baseline is not None
            and baseline.organization_id == actor.organization_id
            and baseline.project_id == experiment.project_id
        ):
            return [r for r in _version_runs(db, baseline.current_version_id) if r.role != "reproduction"]
    return [r for r in _version_runs(db, version.id) if r.role == "baseline"]


def _load_subject(
    db: Session,
    actor: Actor,
    kind: str,
    *,
    experiment_run_id: uuid.UUID | str | None,
    experiment_id: uuid.UUID | str | None,
    comparison_id: uuid.UUID | str | None,
    permission: str,
) -> _Subject:
    given = [s for s in (experiment_run_id, experiment_id, comparison_id) if s is not None]
    if len(given) != 1:
        raise ValidationFailed("Provide exactly one of experiment_run_id, experiment_id or comparison_id")
    warnings: list[str] = []
    if experiment_run_id is not None:
        run = get_owned(db, ExperimentRun, experiment_run_id, actor, label="Experiment run")
        project = load_project(db, actor, run.project_id, permission)
        experiment = db.get(Experiment, run.experiment_id)
        version = db.get(ExperimentVersion, run.experiment_version_id)
        if experiment is None or version is None:
            raise NotFound("Experiment run not found")
        spec = _parse_spec(version, warnings)
        baseline = _baseline_runs(db, actor, experiment, version, spec)
        return _Subject(project, experiment, version, [run], baseline, run=run, warnings=warnings)
    if experiment_id is not None:
        experiment = get_owned(db, Experiment, experiment_id, actor, label="Experiment")
        project = load_project(db, actor, experiment.project_id, permission)
        if experiment.current_version_id is None:
            raise Conflict("The experiment has no version to evaluate", code="experiment_has_no_version")
        version = db.get(ExperimentVersion, experiment.current_version_id)
        if version is None:
            raise NotFound("Experiment version not found")
        spec = _parse_spec(version, warnings)
        runs = _version_runs(db, version.id)
        if kind == "reproduction":
            reproduced = [r for r in runs if r.role == "reproduction"]
            originals = [r for r in runs if r.role not in ("reproduction", "baseline")]
            return _Subject(project, experiment, version, reproduced, originals, warnings=warnings)
        candidates = [r for r in runs if r.role not in ("baseline", "reproduction")]
        if experiment.kind == "baseline" and not candidates:
            candidates = [r for r in runs if r.role == "baseline"]
        baseline = [] if experiment.kind == "baseline" else _baseline_runs(db, actor, experiment, version, spec)
        return _Subject(project, experiment, version, candidates, baseline, warnings=warnings)
    comparison = get_owned(db, ExperimentComparison, comparison_id, actor, label="Comparison")
    candidate = get_owned(db, Experiment, comparison.candidate_experiment_id, actor, label="Experiment")
    project = load_project(db, actor, candidate.project_id, permission)
    version = db.get(ExperimentVersion, comparison.candidate_version_id)
    ids = [_uuid(i) for i in [*comparison.candidate_run_ids, *comparison.baseline_run_ids]]
    by_id = {
        r.id: r
        for r in db.scalars(select(ExperimentRun).where(ExperimentRun.id.in_([i for i in ids if i is not None])))
        if r.organization_id == actor.organization_id
    }
    cand = [by_id[i] for i in (_uuid(x) for x in comparison.candidate_run_ids) if i is not None and i in by_id]
    base = [by_id[i] for i in (_uuid(x) for x in comparison.baseline_run_ids) if i is not None and i in by_id]
    missing = len(comparison.candidate_run_ids) + len(comparison.baseline_run_ids) - len(cand) - len(base)
    if missing:
        warnings.append(f"{missing} run(s) referenced by the comparison no longer exist")
    return _Subject(project, candidate, version, cand, base, comparison=comparison, warnings=warnings)


def _pick_metric(rows: Sequence[ExperimentMetric]) -> tuple[float, str]:
    """The most trusted final measurement: evaluator > platform > self_reported; final (no step) or last step."""

    def rank(row: ExperimentMetric) -> tuple[int, int, int, float]:
        return (
            _SOURCE_RANK.get(row.source, 3),
            0 if row.step is None else 1,
            -(row.step or 0),
            -(row.created_at.timestamp() if row.created_at else 0.0),
        )

    best = min(rows, key=rank)
    source = best.source if best.source in _SOURCE_RANK else "self_reported"
    return float(best.value), source


def _run_records(
    db: Session, runs: Sequence[ExperimentRun]
) -> tuple[dict[uuid.UUID, RunRecord], dict[uuid.UUID, dict[str, str]]]:
    ids = [r.id for r in runs]
    rows_by_run: dict[uuid.UUID, dict[str, list[ExperimentMetric]]] = {i: {} for i in ids}
    if ids:
        for row in db.scalars(select(ExperimentMetric).where(ExperimentMetric.experiment_run_id.in_(ids))):
            rows_by_run.setdefault(row.experiment_run_id, {}).setdefault(row.name, []).append(row)
    records: dict[uuid.UUID, RunRecord] = {}
    sources: dict[uuid.UUID, dict[str, str]] = {}
    for run in runs:
        values: dict[str, Any] = {}
        run_sources: dict[str, str] = {}
        for name, metric_rows in rows_by_run.get(run.id, {}).items():
            values[name], run_sources[name] = _pick_metric(metric_rows)
        for name, value in (run.metrics or {}).items():
            if str(name) not in values and isinstance(value, int | float) and not isinstance(value, bool):
                values[str(name)] = float(value)
                run_sources[str(name)] = "self_reported"
        records[run.id] = RunRecord.model_validate(
            {"run_id": str(run.id), "seed": run.seed, "status": run.status, "metrics": values}
        )
        sources[run.id] = run_sources
    return records, sources


def _metric_sources(per_run: Iterable[dict[str, str]]) -> dict[str, str]:
    seen: dict[str, set[str]] = {}
    for mapping in per_run:
        for name, source in mapping.items():
            seen.setdefault(name, set()).add(source)
    out: dict[str, str] = {}
    for name, sources in seen.items():
        if not sources <= INDEPENDENT_SOURCES:
            out[name] = "self_reported"
        elif sources == {"evaluator"}:
            out[name] = "evaluator"
        else:
            out[name] = "platform"
    return out


def _resource_usage(runs: Sequence[ExperimentRun], jobs: Mapping[uuid.UUID, ComputeJob]) -> list[ResourceUsage]:
    out: list[ResourceUsage] = []
    for run in runs:
        job = jobs.get(run.compute_job_id) if run.compute_job_id else None
        if job is None:
            continue
        usage = dict(job.resource_usage or {})
        reserved = usage.get("reserved") if isinstance(usage.get("reserved"), dict) else None
        request = reserved or dict(job.resource_request or {})
        wall = _finite_nonneg(usage.get("wall_seconds"))
        peak_bytes = _finite_nonneg(usage.get("max_memory_bytes"))
        gpu_count = _finite_nonneg(request.get("gpu_count")) or 0.0
        out.append(
            ResourceUsage(
                run_id=str(run.id),
                cpu_seconds=_finite_nonneg(usage.get("cpu_seconds")),
                wall_seconds=wall,
                peak_memory_mb=peak_bytes / (1024 * 1024) if peak_bytes is not None else None,
                gpu_seconds=gpu_count * wall if gpu_count and wall is not None else None,
                requested_cpu=_finite_pos(request.get("cpu")),
                requested_memory_mb=_finite_pos(request.get("memory_mb")),
                cost_usd=_finite_nonneg(float(job.cost_usd)) if job.cost_usd is not None else None,
                oom_killed=job.status_reason == "oom",
                timed_out=job.status == ExecutionStatus.TIMED_OUT,
                exit_code=job.exit_code,
            )
        )
    return out


def _build_view(db: Session, subject: _Subject, spec: ExperimentSpec | None) -> ExperimentView:
    all_runs = [*subject.runs, *subject.baseline_runs]
    records, sources = _run_records(db, all_runs)
    job_ids = [r.compute_job_id for r in subject.runs if r.compute_job_id is not None]
    jobs = {j.id: j for j in db.scalars(select(ComputeJob).where(ComputeJob.id.in_(job_ids)))} if job_ids else {}
    return ExperimentView.model_validate(
        {
            "experiment_id": str(subject.experiment.id) if subject.experiment else None,
            "version_id": str(subject.version.id) if subject.version else None,
            "status": subject.experiment.status if subject.experiment else None,
            "spec": spec,
            "runs": [records[r.id] for r in subject.runs],
            "baseline_runs": [records[r.id] for r in subject.baseline_runs],
            "resource_usage": _resource_usage(subject.runs, jobs),
            "metric_sources": _metric_sources(sources[r.id] for r in all_runs),
        }
    )


# -- inputs --------------------------------------------------------------------------------------
def _artifact_input(db: Session, actor: Actor, version_id: uuid.UUID | str, project: Project, name: str) -> InputRef:
    from aegis_api.lab.data.artifacts import ArtifactQuarantined, get_artifact_version

    version = get_artifact_version(db, actor, version_id, permission="artifact:download")
    if version.project_id != project.id:
        raise ValidationFailed(f"Input '{name}' must be an artifact of the experiment's project")
    if version.scan_status == "infected":
        raise ArtifactQuarantined()
    return InputRef(
        name=name,
        source="artifact_version",
        ref_id=str(version.id),
        storage_key=version.storage_key,
        size_bytes=int(version.size_bytes),
        sha256=version.checksum,
    )


def _dataset_input(
    db: Session, actor: Actor, version_id: uuid.UUID | str, split: str | None, project: Project, name: str
) -> InputRef:
    from aegis_api.lab.data.datasets import resolve_dataset_object

    version = get_owned(db, DatasetVersion, version_id, actor, label="Dataset version")
    if version.project_id != project.id:
        raise ValidationFailed(f"Input '{name}' must be a dataset version of the experiment's project")
    target = resolve_dataset_object(db, actor, version.id, split, for_evaluator=True)
    splits: dict[str, Any] = version.splits or {}
    visibility: str | None
    if split is not None:
        visibility = str((splits.get(split) or {}).get("visibility") or "experiment")
    else:
        restricted = any(isinstance(s, dict) and s.get("visibility") == "evaluator_only" for s in splits.values())
        visibility = "evaluator_only" if restricted else "experiment"
    return InputRef(
        name=name,
        source="dataset_split",
        ref_id=str(version.id),
        storage_key=target.key,
        size_bytes=int(target.size_bytes),
        sha256=target.checksum or None,
        split=split,
        visibility=visibility,
    )


def _run_output_version(db: Session, run: ExperimentRun, filename: str) -> uuid.UUID | None:
    """Artifact version of the output ``filename`` of a run (platform job manifest first, then linked artifacts)."""

    def matches(path: str) -> int:
        if path == filename:
            return 2
        return 1 if path.rsplit("/", 1)[-1] == filename else 0

    if run.compute_job_id is not None:
        job = db.get(ComputeJob, run.compute_job_id)
        if job is not None and job.organization_id == run.organization_id:
            files = (job.output_manifest or {}).get("files") or {}
            ranked = sorted(
                ((matches(str(path)), str(path)) for path, info in files.items() if isinstance(info, dict)),
                reverse=True,
            )
            for score, path in ranked:
                if score and files[path].get("artifact_version_id"):
                    return _uuid(files[path]["artifact_version_id"])
    artifacts = db.scalars(
        select(Artifact)
        .where(Artifact.experiment_run_id == run.id, Artifact.deleted_at.is_(None))
        .order_by(Artifact.created_at.desc())
    ).all()
    ranked_artifacts = sorted(((matches(a.name), a) for a in artifacts), key=lambda item: -item[0])
    for score, artifact in ranked_artifacts:
        if score and artifact.current_version_id is not None:
            return artifact.current_version_id
    return None


def _heldout_input(
    db: Session, actor: Actor, spec: ExperimentSpec | None, project: Project, filename: str, warnings: list[str]
) -> InputRef | None:
    """The reference file from an ``evaluator_only`` split of a dataset declared in the spec."""
    if spec is None:
        return None
    candidates: list[tuple[tuple[int, int, int], DatasetVersion, str]] = []
    for order, use in enumerate(spec.datasets):
        version = db.get(DatasetVersion, _uuid(use.dataset_version_id)) if _uuid(use.dataset_version_id) else None
        if version is None or version.organization_id != actor.organization_id or version.project_id != project.id:
            warnings.append(f"dataset version {use.dataset_version_id} of the spec is not available")
            continue
        splits: dict[str, Any] = version.splits or {}
        names = [use.split] if use.split else sorted(splits)
        for split_name in names:
            info = splits.get(split_name) if split_name else None
            if not isinstance(info, dict) or info.get("visibility") != "evaluator_only":
                continue
            exact = 0 if str(info.get("filename") or "") == filename else 1
            candidates.append(((exact, _HELDOUT_ROLE_ORDER.get(use.role, 9), order), version, str(split_name)))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    _, version, split_name = candidates[0]
    return _dataset_input(db, actor, version.id, split_name, project, filename)


def _code_snapshot_input(db: Session, actor: Actor, version: ExperimentVersion | None) -> InputRef | None:
    if version is None or version.code_snapshot_id is None:
        return None
    snapshot = db.get(CodeSnapshot, version.code_snapshot_id)
    if snapshot is None or snapshot.organization_id != actor.organization_id or not snapshot.storage_key:
        return None
    manifest = {str(k): v for k, v in (snapshot.files_manifest or {}).items() if isinstance(v, dict)}
    size = sum(int(v.get("size") or 0) for path, v in manifest.items() if path.endswith((".py", ".ipynb"))) or min(
        MAX_CODE_ARCHIVE_BYTES, int(get_storage().stat(snapshot.storage_key).size)
    )
    return InputRef(
        name="code_snapshot",
        source="code_snapshot",
        ref_id=str(snapshot.id),
        storage_key=snapshot.storage_key,
        size_bytes=size,
        sha256=snapshot.content_hash,
        manifest=manifest,
    )


def _resolve_inputs(
    db: Session,
    actor: Actor,
    *,
    kind: str,
    config: Mapping[str, Any],
    subject: _Subject,
    spec: ExperimentSpec | None,
    explicit: Mapping[str, Mapping[str, Any]] | None,
    warnings: list[str],
) -> list[InputRef]:
    refs: dict[str, InputRef] = {}
    for name, ref in (explicit or {}).items():
        if ref.get("artifact_version_id"):
            refs[name] = _artifact_input(db, actor, ref["artifact_version_id"], subject.project, name)
        elif ref.get("dataset_version_id"):
            refs[name] = _dataset_input(db, actor, ref["dataset_version_id"], ref.get("split"), subject.project, name)
        else:
            raise ValidationFailed(f"Input '{name}' needs an artifact_version_id or a dataset_version_id")
    if kind in FILE_EVALUATORS:
        if subject.run is None:
            raise ValidationFailed(
                f"The {kind} evaluator recomputes metrics from one run's predictions: pass experiment_run_id"
            )
        predictions_field, reference_field = FILE_EVALUATORS[kind]
        predictions = str(config[predictions_field])
        reference = str(config[reference_field])
        if predictions not in refs:
            version_id = _run_output_version(db, subject.run, predictions)
            if version_id is not None:
                refs[predictions] = _artifact_input(db, actor, version_id, subject.project, predictions)
            else:
                warnings.append(f"the run produced no output named {predictions!r}")
        if reference not in refs:
            heldout = _heldout_input(db, actor, spec, subject.project, reference, warnings)
            if heldout is not None:
                refs[reference] = heldout
            else:
                warnings.append(f"no evaluator_only dataset split provides {reference!r}")
    if kind == "code_quality":
        code = _code_snapshot_input(db, actor, subject.version)
        if code is not None:
            refs.setdefault(code.name, code)
    return list(refs.values())


def _independence(actor: Actor, subject: _Subject, request_config: Mapping[str, Any] | None) -> tuple[bool, list[str]]:
    """Is the evaluator itself outside the control of the agent that generated the result?"""
    notes: list[str] = []
    generators = {
        g
        for g in (
            subject.experiment.created_by_agent_run_id if subject.experiment else None,
            subject.version.created_by_agent_run_id if subject.version else None,
        )
        if g is not None
    }
    if actor.kind == "agent" and request_config:
        notes.append("the evaluator configuration was supplied by an agent")
    if actor.agent_run_id is not None and actor.agent_run_id in generators:
        notes.append("the evaluation was requested by the agent run that generated the experiment")
    return not notes, notes


def prepare_evaluation(
    db: Session,
    actor: Actor,
    *,
    evaluator_key: str,
    evaluator_version: str | None = None,
    experiment_run_id: uuid.UUID | str | None = None,
    experiment_id: uuid.UUID | str | None = None,
    comparison_id: uuid.UUID | str | None = None,
    config: Mapping[str, Any] | None = None,
    inputs: Mapping[str, Any] | None = None,
) -> EvaluationPlan:
    """Authorise and plan one evaluation (``evaluation:run`` in the subject's project).

    Raises ``ValidationFailed`` for an invalid configuration or subject, ``NotFound`` for unknown/foreign ids and
    ``PayloadTooLarge`` when the inputs exceed :data:`MAX_EVALUATION_INPUT_BYTES`.
    """
    evaluator = resolve_evaluator(db, actor, evaluator_key, evaluator_version)
    kind = evaluator.kind
    if kind not in BUILTIN_KINDS:
        raise ValidationFailed(f"Evaluator kind '{kind}' is not executable by this platform version")
    if evaluator.organization_id is None and evaluator.version != _engine_version(kind):
        raise ValidationFailed(
            f"Built-in evaluator '{evaluator.key}' version {evaluator.version} is no longer executable; "
            f"use version {_engine_version(kind)}",
            code="evaluator_version_retired",
        )
    subject = _load_subject(
        db,
        actor,
        kind,
        experiment_run_id=experiment_run_id,
        experiment_id=experiment_id,
        comparison_id=comparison_id,
        permission="evaluation:run",
    )
    warnings = list(subject.warnings)
    spec = _parse_spec(subject.version, warnings)
    request_config = dict(config or {})
    merged = _deep_merge(evaluator.config or {}, request_config)
    if kind == "metric" and subject.run is not None and "min_runs" not in merged:
        merged["min_runs"] = 1  # run-level success criteria: one run is the whole sample
    engine = get_evaluator(kind)
    try:
        normalized: dict[str, Any] = engine.parse_config(merged).model_dump(mode="json")
    except EvaluatorConfigError as exc:
        raise ValidationFailed(str(exc), code="invalid_evaluator_config") from exc
    explicit: dict[str, dict[str, Any]] = {}
    for name, ref in (inputs or {}).items():
        raw = ref.model_dump(mode="json") if hasattr(ref, "model_dump") else dict(ref)
        explicit[str(name)] = {k: v for k, v in raw.items() if v is not None}
    refs = _resolve_inputs(
        db, actor, kind=kind, config=normalized, subject=subject, spec=spec, explicit=explicit, warnings=warnings
    )
    total = sum(ref.size_bytes for ref in refs)
    if total > MAX_EVALUATION_INPUT_BYTES:
        raise PayloadTooLarge(
            f"Evaluation inputs total {total} bytes, above the {MAX_EVALUATION_INPUT_BYTES} byte limit"
        )
    view = _build_view(db, subject, spec)
    independent, notes = _independence(actor, subject, request_config)
    experiment = subject.experiment
    request = {
        "evaluator_key": evaluator.key,
        "evaluator_version": evaluator.version,
        "experiment_run_id": str(subject.run.id) if subject.run else None,
        "experiment_id": str(experiment.id) if experiment and subject.run is None and not subject.comparison else None,
        "comparison_id": str(subject.comparison.id) if subject.comparison else None,
        "config": request_config,
        "inputs": explicit,
    }
    return EvaluationPlan(
        organization_id=actor.organization_id,
        workspace_id=subject.project.workspace_id,
        project_id=subject.project.id,
        mission_id=experiment.mission_id if experiment else None,
        experiment_id=experiment.id if experiment else None,
        experiment_run_id=subject.run.id if subject.run else None,
        comparison_id=subject.comparison.id if subject.comparison else None,
        experiment_version_id=subject.version.id if subject.version else None,
        evaluator_id=evaluator.id,
        evaluator_key=evaluator.key,
        evaluator_version=evaluator.version,
        evaluator_kind=kind,
        evaluator_org_id=evaluator.organization_id,
        evaluator_config_hash=evaluator.config_hash,
        config=normalized,
        config_hash=config_digest(kind, normalized),
        request=request,
        view=view,
        context=EvalContext(config=normalized),
        inputs=refs,
        warnings=warnings,
        evaluator_independent=independent,
        independence_notes=notes,
    )


# =============================================================================================
# Reading inputs (object storage only — no database access)
# =============================================================================================
def _read_code_snapshot(data: bytes, ref: InputRef, warnings: list[str]) -> dict[str, bytes]:
    """Python sources and notebooks of a stored code snapshot, read in memory with strict bounds."""
    from aegis_api.lab.execution.archive import UnsafeArchiveMember, normalize_member_path

    files: dict[str, bytes] = {}
    total = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
            for count, member in enumerate(tar):
                if count >= MAX_CODE_FILES * 4:
                    warnings.append("code snapshot has too many entries; the rest were not analysed")
                    break
                if not member.isfile():
                    continue
                try:
                    path = normalize_member_path(member.name)
                except UnsafeArchiveMember:
                    warnings.append("code snapshot contains an unsafe path; entry skipped")
                    continue
                if path is None:
                    continue
                name = path.as_posix()
                if not name.endswith((".py", ".ipynb")):
                    continue
                if member.size > MAX_CODE_FILE_BYTES:
                    warnings.append(f"code file {name!r} exceeds {MAX_CODE_FILE_BYTES} bytes; not analysed")
                    continue
                if len(files) >= MAX_CODE_FILES or total + member.size > MAX_CODE_TOTAL_BYTES:
                    warnings.append("code snapshot exceeds the analysis budget; the rest was not analysed")
                    break
                handle = tar.extractfile(member)
                if handle is None:
                    continue
                content = handle.read(MAX_CODE_FILE_BYTES + 1)
                total += len(content)
                files[name] = content
    except (tarfile.TarError, EOFError, OSError, zlib.error) as exc:
        warnings.append(f"code snapshot could not be read: {type(exc).__name__}")
    return files


def load_bundle(plan: EvaluationPlan) -> tuple[ArtifactBundle, list[str]]:
    """Read every planned input from object storage (bounded; size verified) → ``(bundle, warnings)``.

    Declared checksums travel in the bundle metadata and are verified by the evaluators, which record any
    mismatch as evidence. Storage outages propagate as ``ServiceUnavailable`` (retryable).
    """
    storage = get_storage()
    files: dict[str, bytes] = {}
    metadata: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for ref in plan.inputs:
        if ref.source == "code_snapshot":
            archive = storage.get_bytes(ref.storage_key, MAX_CODE_ARCHIVE_BYTES)
            for path, content in _read_code_snapshot(archive, ref, warnings).items():
                files[path] = content
                entry = ref.manifest.get(path) or {}
                metadata[path] = {"code_snapshot_id": ref.ref_id}
                if entry.get("sha256"):
                    metadata[path]["sha256"] = str(entry["sha256"])
            continue
        data = storage.get_bytes(ref.storage_key, max(ref.size_bytes, 1))
        if len(data) != ref.size_bytes:
            warnings.append(f"{ref.name}: stored object size {len(data)} differs from the recorded {ref.size_bytes}")
        files[ref.name] = data
        meta: dict[str, Any] = {}
        if ref.sha256:
            meta["sha256"] = ref.sha256
        if ref.source == "artifact_version":
            meta["artifact_version_id"] = ref.ref_id
        else:
            meta["dataset_version_id"] = ref.ref_id
            if ref.split is not None:
                meta["split"] = ref.split
            if ref.visibility is not None:
                meta["visibility"] = ref.visibility
        metadata[ref.name] = meta
    return ArtifactBundle(files=files, metadata=metadata), warnings


def evaluate_plan(plan: EvaluationPlan, bundle: ArtifactBundle) -> tuple[EvaluationResult | None, str | None, float]:
    """Run the pure evaluator → ``(result, error, duration_ms)``; never raises for evaluator failures."""
    engine = get_evaluator(plan.evaluator_kind)
    started = time.perf_counter()
    with span("lab.evaluation.evaluate", evaluator=plan.evaluator_key, kind=plan.evaluator_kind):
        try:
            result = engine.evaluate(plan.view, bundle, plan.context)
            error = None
        except EvaluatorConfigError as exc:
            result, error = None, f"invalid evaluator configuration: {exc}"[:2000]
        except Exception as exc:  # a defect in an evaluator must fail this run, not the caller
            log.exception("evaluator_crashed", evaluator=plan.evaluator_key, kind=plan.evaluator_kind)
            result, error = None, f"evaluator error ({type(exc).__name__})"
    return result, error, round((time.perf_counter() - started) * 1000.0, 3)


# =============================================================================================
# Persistence
# =============================================================================================
def _scalar_metrics(metrics: Mapping[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for name, value in metrics.items():
        if name in _BOOKKEEPING_METRICS or isinstance(value, bool) or not isinstance(value, int | float):
            continue
        number = float(value)
        if math.isfinite(number):
            out[str(name)[:120]] = number
    return out


def _passed_label(passed: bool | None) -> str:
    return "undetermined" if passed is None else ("passed" if passed else "failed")


def _new_run(plan: EvaluationPlan, *, status: str, mode: Mode, idempotency_key: str | None) -> EvaluationRun:
    inputs: dict[str, Any] = {
        "request": plan.request,
        "mode": mode,
        "evaluator": {
            "id": str(plan.evaluator_id),
            "key": plan.evaluator_key,
            "version": plan.evaluator_version,
            "kind": plan.evaluator_kind,
            "builtin": plan.evaluator_org_id is None,
            "config_hash": plan.evaluator_config_hash,
            "engine_version": _engine_version(plan.evaluator_kind),
        },
        "config": plan.config,
        "config_hash": plan.config_hash,
        **plan.subject,
        "run_ids": [r.run_id for r in plan.view.runs if r.run_id],
        "baseline_run_ids": [r.run_id for r in plan.view.baseline_runs if r.run_id],
        "metric_sources": dict(plan.view.metric_sources),
        "files": [ref.describe() for ref in plan.inputs],
    }
    if idempotency_key:
        inputs["idempotency_key"] = idempotency_key
    return EvaluationRun(
        id=uuid.uuid4(),
        organization_id=plan.organization_id,
        workspace_id=plan.workspace_id,
        project_id=plan.project_id,
        mission_id=plan.mission_id,
        experiment_id=plan.experiment_id,
        experiment_run_id=plan.experiment_run_id,
        comparison_id=plan.comparison_id,
        evaluator_id=plan.evaluator_id,
        evaluator_key=plan.evaluator_key,
        evaluator_version=plan.evaluator_version,
        status=status,
        metrics={},
        warnings=[],
        evidence=[],
        inputs=inputs,
        independent=False,
    )


def persist_evaluation(
    db: Session,
    actor: Actor,
    plan: EvaluationPlan,
    *,
    result: EvaluationResult | None,
    error: str | None,
    duration_ms: float,
    started_at: datetime,
    load_warnings: Sequence[str] = (),
    run: EvaluationRun | None = None,
    mode: Mode = "inline",
    idempotency_key: str | None = None,
) -> EvaluationRun:
    """Record the outcome of an evaluation (new row, or a PENDING/RUNNING row created earlier)."""
    if run is None:
        run = _new_run(plan, status=RunState.RUNNING, mode=mode, idempotency_key=idempotency_key)
        run.started_at = started_at
        db.add(run)
        db.flush()
    elif run.status == RunState.PENDING:
        assert_transition("run", run.status, RunState.RUNNING)
        run.status = RunState.RUNNING
        run.started_at = run.started_at or started_at
    target = RunState.COMPLETED if result is not None else RunState.FAILED
    assert_transition("run", run.status, target)
    independent = bool(result is not None and result.independent and plan.evaluator_independent)
    run.status = target
    run.completed_at = utcnow()
    run.error = error
    run.passed = result.passed if result is not None else None
    run.metrics = dict(result.metrics) if result is not None else {}
    run.confidence = result.confidence if result is not None else None
    run.evidence = list(result.evidence) if result is not None else []
    run.warnings = _clip_warnings([*plan.warnings, *load_warnings, *(result.warnings if result else [])])
    run.independent = independent
    run.inputs = {
        **(run.inputs or {}),
        "duration_ms": duration_ms,
        "details": result.details if result is not None else {},
        "result_evaluator": (
            {"key": result.evaluator_key, "version": result.evaluator_version, "kind": result.kind}
            if result is not None
            else None
        ),
        "independence": {
            "evaluator": plan.evaluator_independent,
            "result": result.independent if result is not None else None,
            "notes": plan.independence_notes,
        },
    }
    db.flush()
    recomputed: dict[str, float] = {}
    if (
        result is not None
        and plan.experiment_run_id is not None
        and plan.evaluator_kind in RECOMPUTING_KINDS
        and independent
    ):
        split = next((ref.split for ref in plan.heldout_inputs if ref.split), None)
        recomputed = _scalar_metrics(result.metrics)
        for name, value in sorted(recomputed.items()):
            db.add(
                ExperimentMetric(
                    organization_id=plan.organization_id,
                    experiment_run_id=plan.experiment_run_id,
                    name=name,
                    value=value,
                    split=split[:32] if split else None,
                    source="evaluator",
                    evaluation_run_id=run.id,
                )
            )
        db.flush()
    if plan.heldout_inputs:
        audit(
            db,
            actor,
            HELDOUT_DATA_READ,
            "evaluation_run",
            run.id,
            after={
                "evaluator": plan.evaluator_key,
                "inputs": [
                    {"dataset_version_id": ref.ref_id, "split": ref.split, "sha256": ref.sha256}
                    for ref in plan.heldout_inputs
                ],
            },
        )
    evidence = append_evidence(
        db,
        organization_id=plan.organization_id,
        kind="evaluation",
        title=f"Evaluation {plan.evaluator_key}@{plan.evaluator_version}: {_passed_label(run.passed)}",
        content={
            "evaluation_run_id": str(run.id),
            "evaluator": {"key": plan.evaluator_key, "version": plan.evaluator_version, "kind": plan.evaluator_kind},
            "config_hash": plan.config_hash,
            **plan.subject,
            "status": run.status,
            "passed": run.passed,
            "confidence": run.confidence,
            "independent": independent,
            "metrics": _scalar_metrics(run.metrics),
            "evaluator_sourced_metrics": sorted(recomputed),
            "inputs": [ref.describe() for ref in plan.inputs],
            "error": error,
        },
    )
    run.evidence = [*run.evidence, {"check": "evidence_record", "evidence_id": str(evidence.id)}]
    emit(
        db,
        organization_id=plan.organization_id,
        type=EventType.EVALUATION_COMPLETED,
        payload={
            "evaluation_run_id": str(run.id),
            "evaluator_key": plan.evaluator_key,
            "evaluator_version": plan.evaluator_version,
            "status": run.status,
            "passed": run.passed,
            "confidence": run.confidence,
            "independent": independent,
            **plan.subject,
        },
        mission_id=plan.mission_id,
        project_id=plan.project_id,
        workspace_id=plan.workspace_id,
        subject_type="evaluation_run",
        subject_id=run.id,
        actor=actor,
    )
    db.flush()
    return run


def _execute_in_session(db: Session, actor: Actor, plan: EvaluationPlan, *, mode: Mode) -> EvaluationRun:
    started_at = utcnow()
    try:
        bundle, load_warnings = load_bundle(plan)
    except ServiceUnavailable:
        raise
    except AppError as exc:
        return persist_evaluation(
            db,
            actor,
            plan,
            result=None,
            error=f"input could not be read: {exc.message}"[:2000],
            duration_ms=0.0,
            started_at=started_at,
            mode=mode,
        )
    result, error, duration = evaluate_plan(plan, bundle)
    return persist_evaluation(
        db,
        actor,
        plan,
        result=result,
        error=error,
        duration_ms=duration,
        started_at=started_at,
        load_warnings=load_warnings,
        mode=mode,
    )


def run_evaluation(
    db: Session,
    actor: Actor,
    *,
    evaluator_key: str,
    evaluator_version: str | None = None,
    experiment_run_id: uuid.UUID | str | None = None,
    experiment_id: uuid.UUID | str | None = None,
    comparison_id: uuid.UUID | str | None = None,
    config: Mapping[str, Any] | None = None,
    inputs: Mapping[str, Any] | None = None,
) -> EvaluationRun:
    """Plan, evaluate and persist one evaluation in the caller's session (inputs bounded by
    :data:`MAX_EVALUATION_INPUT_BYTES`). The returned run is ``COMPLETED`` (verdict in ``passed``) or ``FAILED``
    (``error``) — evaluator problems never raise."""
    plan = prepare_evaluation(
        db,
        actor,
        evaluator_key=evaluator_key,
        evaluator_version=evaluator_version,
        experiment_run_id=experiment_run_id,
        experiment_id=experiment_id,
        comparison_id=comparison_id,
        config=config,
        inputs=inputs,
    )
    return _execute_in_session(db, actor, plan, mode="inline")


def submit_evaluation(
    db: Session, actor: Actor, data: Any
) -> tuple[EvaluationRun, Literal["inline", "workflow"], str | None]:
    """HTTP entry point → ``(run, mode, workflow_run_id)``.

    Inputs up to :data:`INLINE_INPUT_LIMIT_BYTES` are evaluated inline (``mode="inline"``). Larger evaluations are
    recorded ``PENDING`` and handed to the ``EvaluationWorkflow`` (``mode="workflow"``), which executes them in the
    ``evaluation.run`` activity; when that workflow is not deployed the request fails with 503.
    """
    plan = prepare_evaluation(
        db,
        actor,
        evaluator_key=data.evaluator_key,
        evaluator_version=data.evaluator_version,
        experiment_run_id=data.experiment_run_id,
        experiment_id=data.experiment_id,
        comparison_id=data.comparison_id,
        config=data.config,
        inputs=data.inputs,
    )
    if plan.total_bytes <= INLINE_INPUT_LIMIT_BYTES:
        return _execute_in_session(db, actor, plan, mode="inline"), "inline", None
    from aegis_api.lab.workflows.definitions import has_flow
    from aegis_api.lab.workflows.launcher import launch_workflow

    if not has_flow(EVALUATION_WORKFLOW):
        raise ServiceUnavailable(
            f"Evaluation inputs total {plan.total_bytes} bytes (inline limit {INLINE_INPUT_LIMIT_BYTES}); "
            "queued evaluations need the EvaluationWorkflow, which is not available on this deployment",
            code="evaluation_workflow_unavailable",
        )
    run = _new_run(plan, status=RunState.PENDING, mode="workflow", idempotency_key=None)
    db.add(run)
    db.flush()
    workflow = launch_workflow(
        db,
        actor,
        EVALUATION_WORKFLOW,
        subject_type="evaluation_run",
        subject_id=run.id,
        input={"evaluation_run_id": str(run.id), **{k: v for k, v in plan.subject.items() if v is not None}},
        project_id=plan.project_id,
        mission_id=plan.mission_id,
    )
    run.inputs = {**run.inputs, "workflow_run_id": str(workflow.id)}
    db.flush()
    return run, "workflow", str(workflow.id)


# =============================================================================================
# Worker-side execution (short transactions; bytes are read and evaluated outside any transaction)
# =============================================================================================
def run_summary(run: EvaluationRun) -> dict[str, Any]:
    return {
        "evaluation_run_id": str(run.id),
        "evaluator_key": run.evaluator_key,
        "evaluator_version": run.evaluator_version,
        "status": run.status,
        "passed": run.passed,
        "confidence": run.confidence,
        "independent": run.independent,
    }


def _plan_kwargs(request: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "evaluator_key": str(request["evaluator_key"]),
        "evaluator_version": request.get("evaluator_version"),
        "experiment_run_id": request.get("experiment_run_id"),
        "experiment_id": request.get("experiment_id"),
        "comparison_id": request.get("comparison_id"),
        "config": request.get("config") or None,
        "inputs": request.get("inputs") or None,
    }


def execute_pending_evaluation(actor: Actor, evaluation_run_id: uuid.UUID | str) -> dict[str, Any]:
    """Execute a queued (``PENDING``) evaluation run; idempotent (finished runs return their summary)."""
    started_at = utcnow()
    with tenant_uow(actor) as db:
        run = get_owned(db, EvaluationRun, evaluation_run_id, actor, label="Evaluation run")
        if run.status in _TERMINAL:
            return run_summary(run)
        if run.project_id is not None:
            load_project(db, actor, run.project_id, "evaluation:run")
        request = dict((run.inputs or {}).get("request") or {})
        run_id = run.id
        if run.status == RunState.PENDING:
            assert_transition("run", run.status, RunState.RUNNING)
            run.status = RunState.RUNNING
            run.started_at = started_at
    try:
        with tenant_uow(actor) as db:
            plan = prepare_evaluation(db, actor, **_plan_kwargs(request))
    except ServiceUnavailable:
        raise
    except AppError as exc:
        return _fail_run(actor, run_id, f"evaluation could not be planned: {exc.message}")
    result: EvaluationResult | None = None
    error: str | None = None
    duration = 0.0
    load_warnings: list[str] = []
    try:
        bundle, load_warnings = load_bundle(plan)
    except ServiceUnavailable:
        raise
    except AppError as exc:
        error = f"input could not be read: {exc.message}"
    else:
        result, error, duration = evaluate_plan(plan, bundle)
    with tenant_uow(actor) as db:
        advisory_xact_lock(db, f"evaluation-run:{run_id}")
        row = db.get(EvaluationRun, run_id)
        if row is None:
            raise NotFound("Evaluation run not found")
        if row.status in _TERMINAL:
            return run_summary(row)
        persist_evaluation(
            db,
            actor,
            plan,
            result=result,
            error=error,
            duration_ms=duration,
            started_at=row.started_at or started_at,
            load_warnings=load_warnings,
            run=row,
            mode="workflow",
        )
        return run_summary(row)


def _fail_run(actor: Actor, run_id: uuid.UUID, message: str) -> dict[str, Any]:
    with tenant_uow(actor) as db:
        advisory_xact_lock(db, f"evaluation-run:{run_id}")
        row = db.get(EvaluationRun, run_id)
        if row is None:
            raise NotFound("Evaluation run not found")
        if row.status not in _TERMINAL:
            if row.status == RunState.PENDING:
                row.status = RunState.RUNNING
            assert_transition("run", row.status, RunState.FAILED)
            row.status = RunState.FAILED
            row.error = message[:2000]
            row.completed_at = utcnow()
            db.flush()
        return run_summary(row)


def _idempotency_key(plan: EvaluationPlan, scope: str) -> str:
    material = {
        "scope": scope,
        "evaluator": [plan.evaluator_key, plan.evaluator_version, plan.config_hash],
        "subject": plan.subject,
        "runs": sorted(f"{r.run_id}:{r.status}" for r in [*plan.view.runs, *plan.view.baseline_runs]),
        "files": sorted(f"{ref.name}:{ref.ref_id}:{ref.split}:{ref.sha256}" for ref in plan.inputs),
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _find_idempotent(db: Session, plan: EvaluationPlan, key: str) -> EvaluationRun | None:
    stmt = select(EvaluationRun).where(
        EvaluationRun.organization_id == plan.organization_id,
        EvaluationRun.evaluator_key == plan.evaluator_key,
        EvaluationRun.inputs["idempotency_key"].astext == key,
        EvaluationRun.status.in_([RunState.COMPLETED, RunState.FAILED]),
    )
    if plan.experiment_run_id is not None:
        stmt = stmt.where(EvaluationRun.experiment_run_id == plan.experiment_run_id)
    elif plan.experiment_id is not None:
        stmt = stmt.where(EvaluationRun.experiment_id == plan.experiment_id)
    return db.scalars(stmt.order_by(EvaluationRun.created_at.desc()).limit(1)).first()


def evaluate_detached(
    actor: Actor,
    *,
    evaluator_key: str,
    scope: str,
    evaluator_version: str | None = None,
    experiment_run_id: uuid.UUID | str | None = None,
    experiment_id: uuid.UUID | str | None = None,
    comparison_id: uuid.UUID | str | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Worker-side evaluation: plan (short txn) → read + evaluate (no txn) → persist (short txn).

    Idempotent within ``scope`` (typically the workflow run id): the same evaluator/config/subject/run set is
    evaluated once and re-executions return the recorded run.
    """
    kwargs: dict[str, Any] = {
        "evaluator_key": evaluator_key,
        "evaluator_version": evaluator_version,
        "experiment_run_id": experiment_run_id,
        "experiment_id": experiment_id,
        "comparison_id": comparison_id,
        "config": config,
    }
    with tenant_uow(actor) as db:
        plan = prepare_evaluation(db, actor, **kwargs)
        key = _idempotency_key(plan, scope)
        existing = _find_idempotent(db, plan, key)
        if existing is not None:
            return run_summary(existing)
    started_at = utcnow()
    result: EvaluationResult | None = None
    error: str | None = None
    duration = 0.0
    load_warnings: list[str] = []
    try:
        bundle, load_warnings = load_bundle(plan)
    except ServiceUnavailable:
        raise
    except AppError as exc:
        error = f"input could not be read: {exc.message}"
    else:
        result, error, duration = evaluate_plan(plan, bundle)
    with tenant_uow(actor) as db:
        advisory_xact_lock(db, f"evaluation:{plan.organization_id}:{key}")
        existing = _find_idempotent(db, plan, key)
        if existing is not None:
            return run_summary(existing)
        run = persist_evaluation(
            db,
            actor,
            plan,
            result=result,
            error=error,
            duration_ms=duration,
            started_at=started_at,
            load_warnings=load_warnings,
            mode="activity",
            idempotency_key=key,
        )
        return run_summary(run)


def default_run_evaluators(db: Session, actor: Actor, experiment_run_id: uuid.UUID | str) -> list[str]:
    """Default evaluators for one run: recomputing evaluators named by the spec's metrics (first — they produce
    evaluator-sourced metrics), then ``metric`` when the spec pre-registers success criteria."""
    run = get_owned(db, ExperimentRun, experiment_run_id, actor, label="Experiment run")
    load_project(db, actor, run.project_id, "evaluation:run")
    version = db.get(ExperimentVersion, run.experiment_version_id)
    spec = _parse_spec(version, [])
    keys: list[str] = []
    if spec is not None:
        for metric in spec.metrics:
            if metric.evaluator_key in FILE_EVALUATORS and metric.evaluator_key not in keys:
                keys.append(metric.evaluator_key)
        if spec.success_criteria:
            keys.append("metric")
    return keys


def default_experiment_evaluators(
    db: Session, actor: Actor, experiment_id: uuid.UUID | str
) -> tuple[list[str], dict[str, str]]:
    """Default experiment-level evaluators → ``(keys, skipped {key: reason})``: ``metric`` (success criteria),
    ``benchmark`` + ``statistical`` (need baseline runs) and ``resource`` (platform-measured usage)."""
    subject = _load_subject(
        db,
        actor,
        "benchmark",
        experiment_run_id=None,
        experiment_id=experiment_id,
        comparison_id=None,
        permission="evaluation:run",
    )
    spec = _parse_spec(subject.version, [])
    keys: list[str] = []
    skipped: dict[str, str] = {}
    if spec is not None and spec.success_criteria:
        keys.append("metric")
    for key in ("benchmark", "statistical"):
        if subject.baseline_runs:
            keys.append(key)
        else:
            skipped[key] = "no baseline runs to compare against"
    keys.append("resource")
    return keys, skipped


def aggregate_passed(values: Sequence[bool | None]) -> bool | None:
    """``False`` if any evaluation failed, ``True`` if all passed, else ``None`` (undetermined)."""
    if any(v is False for v in values):
        return False
    if values and all(v is True for v in values):
        return True
    return None


# =============================================================================================
# Queries
# =============================================================================================
def get_evaluation(db: Session, actor: Actor, evaluation_run_id: uuid.UUID | str) -> EvaluationRun:
    run = get_owned(db, EvaluationRun, evaluation_run_id, actor, label="Evaluation run")
    if run.project_id is not None:
        load_project(db, actor, run.project_id, "evaluation:read")
    else:
        actor.require("evaluation:read")
    return run


def list_evaluations(
    db: Session,
    actor: Actor,
    params: CursorParams,
    *,
    project_id: uuid.UUID | None = None,
    mission_id: uuid.UUID | None = None,
    experiment_id: uuid.UUID | None = None,
    experiment_run_id: uuid.UUID | None = None,
    comparison_id: uuid.UUID | None = None,
    evaluator_key: str | None = None,
    status: str | None = None,
    passed: bool | None = None,
    mapper: Any = None,
) -> CursorPage[Any]:
    """Evaluation runs in projects visible to the caller, newest first (``evaluation:read``)."""
    stmt = select(EvaluationRun).where(EvaluationRun.organization_id == actor.organization_id)
    if project_id is not None:
        project = load_project(db, actor, project_id, "evaluation:read")
        stmt = stmt.where(EvaluationRun.project_id == project.id)
    else:
        actor.require("evaluation:read")
        visible = visible_project_ids(db, actor)
        if visible is not None:
            stmt = stmt.where(EvaluationRun.project_id.in_(visible))
    for column, value in (
        (EvaluationRun.mission_id, mission_id),
        (EvaluationRun.experiment_id, experiment_id),
        (EvaluationRun.experiment_run_id, experiment_run_id),
        (EvaluationRun.comparison_id, comparison_id),
        (EvaluationRun.evaluator_key, evaluator_key),
        (EvaluationRun.status, status),
        (EvaluationRun.passed, passed),
    ):
        if value is not None:
            stmt = stmt.where(column == value)
    return paginate_keyset(
        db,
        stmt,
        params,
        time_col=EvaluationRun.created_at,
        id_col=EvaluationRun.id,
        mapper=mapper or (lambda row: row),
    )
