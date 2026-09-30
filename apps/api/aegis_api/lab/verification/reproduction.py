"""Reproduction service: independent re-execution of the SAME immutable experiment version with fresh seeds,
and the deterministic comparison of original vs reproduced metrics.

* :func:`request_reproduction` records a ``Reproduction`` (protocol: seeds, tolerance, run-key prefix) and
  schedules the runs through the experiments context (``experiments.runs.schedule_runs`` with role
  ``reproduction`` and ``run_key_prefix = "repro:<reproduction id>"`` — idempotent per run key). Execution
  happens in the workflow (``ExperimentWorkflow``), never on the request thread.
* :func:`evaluate_reproduction` runs the pure ``ReproductionEvaluator`` (``engines.lab.evaluators``) over the
  original and reproduced runs (per metric, aggregated over seeds, plus per-seed pairs for transparency) →
  verdict ``reproduced | partially_reproduced | not_reproduced | inconclusive``, metric deltas and the
  environment diff (image digest, backend, dependencies). A ``not_reproduced`` verdict records a
  ``REPRODUCIBILITY_FAILURE`` through the failures context.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.errors import Conflict, NotFound, ServiceUnavailable, ValidationFailed
from aegis_api.lab.core.access import get_owned, load_project
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.events import EventType, emit
from aegis_api.lab.core.locks import advisory_xact_lock
from aegis_api.lab.models import (
    ExecutionEnvironment,
    Experiment,
    ExperimentRun,
    ExperimentVersion,
    Failure,
    Reproduction,
    ScientificClaim,
    Verification,
    VerificationRun,
)
from aegis_api.lab.verification.claims import link_rows
from aegis_api.lab.verification.common import (
    SUCCESS_RUN_STATUSES,
    TERMINAL_RUN_STATUSES,
    anchor,
    canonical_hash,
    jsonable,
    launch_flow,
    load_run_metrics,
    log,
    metric_sources,
    optional_module,
    parse_spec,
    run_record,
    uuid_set,
)
from aegis_api.lab.verification.schemas import MAX_SEED, ReproductionOut, ReproductionTolerance
from engines.lab.evaluators.base import EvalContext, EvaluatorConfigError, ExperimentView
from engines.lab.evaluators.reproduction import ReproductionEvaluator
from engines.lab.failures import FailureSignals, classify_failure
from engines.lab.states import FailureType, RunState, assert_transition

REPRODUCTION_ROLE = "reproduction"
REPRODUCTION_SEED_OFFSET = 10007
RUN_KEY_PREFIX = "repro"
MAX_REPRODUCTION_SEEDS = 64


def reproduction_out(reproduction: Reproduction) -> ReproductionOut:
    return ReproductionOut.model_validate(reproduction)


def run_key_prefix(reproduction_id: uuid.UUID) -> str:
    return f"{RUN_KEY_PREFIX}:{reproduction_id}"


# ---------------------------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------------------------
def derive_seeds(original_seeds: Sequence[int | None], count: int | None = None) -> list[int]:
    """Fresh seeds derived deterministically from the original ones (``seed + 10007``, collision-free)."""
    base = [int(s) for s in original_seeds if s is not None]
    source = base if base else list(range(count if count is not None else len(original_seeds)))
    used = set(base)
    seeds: list[int] = []
    for seed in source:
        candidate = (seed + REPRODUCTION_SEED_OFFSET) % (MAX_SEED + 1)
        while candidate in used:
            candidate = (candidate + REPRODUCTION_SEED_OFFSET) % (MAX_SEED + 1)
        used.add(candidate)
        seeds.append(candidate)
    return seeds


def validate_seeds(seeds: Iterable[Any]) -> list[int]:
    values = list(seeds)
    if not values or len(values) > MAX_REPRODUCTION_SEEDS:
        raise ValidationFailed(f"seeds must contain 1-{MAX_REPRODUCTION_SEEDS} integers")
    out: list[int] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_SEED:
            raise ValidationFailed(f"seeds must be integers between 0 and {MAX_SEED}")
        out.append(value)
    if len(set(out)) != len(out):
        raise ValidationFailed("seeds must be unique")
    return out


def normalize_tolerance(tolerance: ReproductionTolerance | Mapping[str, Any] | None) -> dict[str, Any]:
    try:
        tol = (
            tolerance
            if isinstance(tolerance, ReproductionTolerance)
            else ReproductionTolerance.model_validate(dict(tolerance or {}))
        )
    except ValidationError as exc:
        raise ValidationFailed("Invalid reproduction tolerance", details={"errors": exc.errors()[:20]}) from exc
    return {
        "abs_tol": tol.abs_tol,
        "rel_tol": tol.rel_tol,
        "one_sided": tol.one_sided,
        "per_metric": {
            name: {k: v for k, v in (("abs_tol", t.abs_tol), ("rel_tol", t.rel_tol)) if v is not None}
            for name, t in sorted(tol.per_metric.items())
        },
    }


# ---------------------------------------------------------------------------------------------
# Scheduling through the experiments context
# ---------------------------------------------------------------------------------------------
def _scheduler() -> Callable[..., Any]:
    """``experiments.runs.schedule_runs`` (fails explicitly when the experiments context is not deployed)."""
    module = optional_module("aegis_api.lab.experiments.runs")
    schedule = getattr(module, "schedule_runs", None) if module is not None else None
    if schedule is None:
        raise ServiceUnavailable(
            "Experiment scheduling is not available; reproduction runs cannot be scheduled",
            code="experiments_unavailable",
        )
    return schedule  # type: ignore[no-any-return]


def _as_runs(db: Session, result: Any) -> list[ExperimentRun]:
    items: Iterable[Any]
    if isinstance(result, Mapping):
        items = result.get("runs") or result.get("run_ids") or []
    elif hasattr(result, "runs"):
        items = result.runs
    elif hasattr(result, "run_ids"):
        items = result.run_ids
    else:
        items = result or []
    runs: list[ExperimentRun] = []
    for item in items:
        if isinstance(item, ExperimentRun):
            runs.append(item)
            continue
        ids = uuid_set([getattr(item, "id", item)])
        row = db.get(ExperimentRun, next(iter(ids))) if ids else None
        if row is not None:
            runs.append(row)
    return runs


def reproduction_runs(db: Session, reproduction: Reproduction) -> list[ExperimentRun]:
    """Runs of a reproduction: recorded ids ∪ runs of the version carrying the reproduction's run-key prefix."""
    ids = sorted(uuid_set(reproduction.reproduction_run_ids or []))
    conditions = [
        (ExperimentRun.experiment_version_id == reproduction.experiment_version_id)
        & ExperimentRun.run_key.like(f"{run_key_prefix(reproduction.id)}:%")
    ]
    if ids:
        conditions.append(ExperimentRun.id.in_(ids))
    return list(
        db.scalars(
            select(ExperimentRun).where(or_(*conditions)).order_by(ExperimentRun.run_number, ExperimentRun.id)
        ).all()
    )


def _original_runs(db: Session, version_id: uuid.UUID) -> list[ExperimentRun]:
    rows = db.scalars(
        select(ExperimentRun)
        .where(
            ExperimentRun.experiment_version_id == version_id,
            ExperimentRun.status.in_(sorted(SUCCESS_RUN_STATUSES)),
            ExperimentRun.role != REPRODUCTION_ROLE,
        )
        .order_by(ExperimentRun.run_number, ExperimentRun.id)
    ).all()
    return [run for run in rows if not run.run_key.startswith(f"{RUN_KEY_PREFIX}:")]


def _pair_runs(originals: Sequence[ExperimentRun], reproduced: Sequence[ExperimentRun]) -> dict[uuid.UUID, uuid.UUID]:
    """reproduction run id → original run id (explicit ``reproduction_of_run_id`` first, then by seed order)."""
    pairs: dict[uuid.UUID, uuid.UUID] = {}
    original_ids = {run.id for run in originals}
    for run in reproduced:
        if run.reproduction_of_run_id in original_ids:
            pairs[run.id] = run.reproduction_of_run_id  # type: ignore[assignment]
    free_originals = [o for o in sorted(originals, key=lambda r: (r.seed is None, r.seed, r.run_number)) if o.id not in pairs.values()]
    free_reproduced = [r for r in sorted(reproduced, key=lambda r: (r.seed is None, r.seed, r.run_number)) if r.id not in pairs]
    for rep, orig in zip(free_reproduced, free_originals, strict=False):
        pairs[rep.id] = orig.id
    return pairs


def request_reproduction(
    db: Session,
    actor: Actor,
    experiment_id: uuid.UUID | str,
    *,
    seeds: Sequence[int] | None = None,
    tolerance: ReproductionTolerance | Mapping[str, Any] | None = None,
    launch: bool = True,
) -> Reproduction:
    """Record a reproduction of the experiment's current immutable version and schedule its runs."""
    experiment = get_owned(db, Experiment, experiment_id, actor, label="Experiment")
    try:
        project = load_project(db, actor, experiment.project_id, "verification:run")
    except NotFound as exc:
        raise NotFound("Experiment not found") from exc
    if experiment.current_version_id is None:
        raise Conflict("The experiment has no version to reproduce", code="nothing_to_reproduce")
    version = db.get(ExperimentVersion, experiment.current_version_id)
    if version is None:
        raise Conflict("The experiment has no version to reproduce", code="nothing_to_reproduce")
    originals = _original_runs(db, version.id)
    if not originals:
        raise Conflict(
            "The experiment has no successful runs of its current version to reproduce", code="nothing_to_reproduce"
        )
    chosen = validate_seeds(seeds) if seeds is not None else derive_seeds([r.seed for r in originals], len(originals))
    tolerance_doc = normalize_tolerance(tolerance)
    reproduction_id = uuid.uuid4()
    prefix = run_key_prefix(reproduction_id)
    tolerance_doc["protocol"] = {
        "role": REPRODUCTION_ROLE,
        "seeds": chosen,
        "seed_derivation": "explicit" if seeds is not None else f"original seed + {REPRODUCTION_SEED_OFFSET}",
        "run_key_prefix": prefix,
        "experiment_version_id": str(version.id),
        "spec_hash": version.spec_hash,
    }
    reproduction = Reproduction(
        id=reproduction_id,
        organization_id=actor.organization_id,
        workspace_id=project.workspace_id,
        project_id=project.id,
        mission_id=experiment.mission_id,
        experiment_id=experiment.id,
        experiment_version_id=version.id,
        original_run_ids=[str(r.id) for r in originals],
        reproduction_run_ids=[],
        tolerance=tolerance_doc,
        status=RunState.PENDING,
        metric_deltas={},
        environment_diff={},
        requested_by_id=actor.user_id,
    )
    db.add(reproduction)
    db.flush()

    schedule = _scheduler()
    result = schedule(db, actor, experiment.id, role=REPRODUCTION_ROLE, seeds=chosen, run_key_prefix=prefix)
    db.flush()
    scheduled = {run.id: run for run in _as_runs(db, result)}
    for run in reproduction_runs(db, reproduction):
        scheduled.setdefault(run.id, run)
    foreign = [run for run in scheduled.values() if run.experiment_version_id != version.id]
    if foreign:
        raise Conflict(
            "Reproduction runs must use the same immutable experiment version as the original runs",
            code="reproduction_version_mismatch",
        )
    runs = sorted(scheduled.values(), key=lambda r: (r.run_number, str(r.id)))
    for rep_id, orig_id in _pair_runs(originals, runs).items():
        run = scheduled[rep_id]
        if run.reproduction_of_run_id is None:
            run.reproduction_of_run_id = orig_id
    reproduction.reproduction_run_ids = [str(r.id) for r in runs]
    assert_transition("run", reproduction.status, RunState.RUNNING)
    reproduction.status = RunState.RUNNING
    reproduction.started_at = utcnow()
    db.flush()
    if launch:
        reproduction.workflow_run_id = launch_flow(
            db,
            actor,
            "ExperimentWorkflow",
            subject_type="experiment",
            subject_id=experiment.id,
            flow_input={
                "experiment_id": str(experiment.id),
                "role": REPRODUCTION_ROLE,
                "seeds": chosen,
                "run_key_prefix": prefix,
                "reproduction_id": str(reproduction.id),
            },
            project_id=project.id,
            mission_id=experiment.mission_id,
            workflow_key=f"repro-{reproduction.id}",
        )
        db.flush()
    return reproduction


def get_reproduction(db: Session, actor: Actor, reproduction_id: uuid.UUID | str) -> Reproduction:
    reproduction = get_owned(db, Reproduction, reproduction_id, actor, label="Reproduction")
    try:
        load_project(db, actor, reproduction.project_id, "verification:read")
    except NotFound as exc:
        raise NotFound("Reproduction not found") from exc
    return reproduction


# ---------------------------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------------------------
def _manifest_summary(runs: Sequence[ExperimentRun]) -> dict[str, Any]:
    def values(key: str) -> list[str]:
        found = {str((r.environment_manifest or {}).get(key)) for r in runs if (r.environment_manifest or {}).get(key)}
        return sorted(found)

    dependencies = sorted(
        {canonical_hash((r.environment_manifest or {}).get("dependencies")) for r in runs if (r.environment_manifest or {}).get("dependencies")}
    )
    return {
        "image": values("image"),
        "image_digest": values("image_digest"),
        "backend": values("backend"),
        "python_version": values("python_version"),
        "dependencies_hashes": dependencies,
    }


def environment_diff(
    originals: Sequence[ExperimentRun], reproduced: Sequence[ExperimentRun], environment: ExecutionEnvironment | None
) -> dict[str, Any]:
    original = _manifest_summary(originals)
    reproduction = _manifest_summary(reproduced)

    def changed(key: str) -> bool | None:
        if not original[key] or not reproduction[key]:
            return None
        return original[key] != reproduction[key]

    return {
        "original": original,
        "reproduction": reproduction,
        "image_changed": changed("image"),
        "image_digest_changed": changed("image_digest"),
        "backend_changed": changed("backend"),
        "python_version_changed": changed("python_version"),
        "dependencies_changed": changed("dependencies_hashes"),
        "environment": (
            {
                "environment_id": str(environment.id),
                "image": environment.image,
                "pinned_digest": environment.image_digest,
                "content_hash": environment.content_hash,
            }
            if environment is not None
            else None
        ),
    }


def _evaluated_metrics(version: ExperimentVersion, originals: Sequence[ExperimentRun], sources: Mapping[str, str]) -> tuple[list[str], str | None]:
    spec = parse_spec(version.spec)
    if spec is not None and spec.metrics:
        primary = next((m.name for m in spec.metrics if m.primary), spec.metrics[0].name)
        return sorted(spec.metric_names), primary
    # Without a declared metric set, compare what the experiment measured (never platform resource metrics).
    names = sorted(name for name, source in sources.items() if source != "platform")
    return names, None


def evaluate_reproduction(db: Session, actor: Actor, reproduction_id: uuid.UUID | str) -> Reproduction:
    """Compare original and reproduced runs (idempotent: a completed reproduction is returned unchanged)."""
    reproduction = get_owned(db, Reproduction, reproduction_id, actor, label="Reproduction")
    try:
        load_project(db, actor, reproduction.project_id, "verification:run")
    except NotFound as exc:
        raise NotFound("Reproduction not found") from exc
    advisory_xact_lock(db, f"reproduction:{reproduction.id}")
    db.refresh(reproduction)
    if reproduction.status == RunState.COMPLETED:
        return reproduction
    if reproduction.status in (RunState.FAILED, RunState.CANCELLED):
        raise Conflict(f"The reproduction is {reproduction.status.lower()}", code="reproduction_not_active")
    runs = reproduction_runs(db, reproduction)
    if not runs:
        raise Conflict("No reproduction runs have been scheduled yet", code="reproduction_not_scheduled")
    pending = [str(r.id) for r in runs if r.status not in TERMINAL_RUN_STATUSES]
    if pending:
        raise Conflict(
            "Reproduction runs are still in progress", code="reproduction_in_progress", details={"pending": pending}
        )
    version = db.get(ExperimentVersion, reproduction.experiment_version_id)
    experiment = db.get(Experiment, reproduction.experiment_id)
    if version is None or experiment is None:
        raise NotFound("Reproduction not found")
    originals = list(
        db.scalars(
            select(ExperimentRun).where(ExperimentRun.id.in_(sorted(uuid_set(reproduction.original_run_ids or []))))
        ).all()
    )
    metrics = load_run_metrics(db, [*originals, *runs])
    sources = metric_sources(metrics.values())
    names, primary = _evaluated_metrics(version, originals, sources)
    tolerance = dict(reproduction.tolerance or {})
    config: dict[str, Any] = {
        "metrics": names,
        "primary_metric": primary,
        "abs_tol": float(tolerance.get("abs_tol", 0.0)),
        "rel_tol": float(tolerance.get("rel_tol", 0.05)),
        "per_metric": {k: v for k, v in (tolerance.get("per_metric") or {}).items() if k in names},
        "one_sided": bool(tolerance.get("one_sided", False)),
    }
    view = ExperimentView(
        experiment_id=str(experiment.id),
        version_id=str(version.id),
        status=experiment.status,
        spec=parse_spec(version.spec),
        runs=[run_record(r, metrics.get(r.id)) for r in runs],
        baseline_runs=[run_record(r, metrics.get(r.id)) for r in originals],
        metric_sources=sources,
    )
    evaluator = ReproductionEvaluator()
    try:
        result = evaluator.evaluate(view, None, EvalContext(config=config))
    except EvaluatorConfigError as exc:
        raise ValidationFailed(f"Invalid reproduction tolerance: {exc}") from exc
    verdict = str(result.details.get("verdict", "inconclusive"))

    pairs = _pair_runs(originals, runs)
    by_id = {r.id: r for r in [*originals, *runs]}
    per_seed = []
    for rep_id, orig_id in sorted(pairs.items(), key=lambda item: str(item[0])):
        rep, orig = by_id[rep_id], by_id[orig_id]
        values = {}
        for name in names:
            original_value = metrics[orig.id].values.get(name) if orig.status in SUCCESS_RUN_STATUSES else None
            reproduced_value = metrics[rep.id].values.get(name) if rep.status in SUCCESS_RUN_STATUSES else None
            values[name] = {
                "original": original_value,
                "reproduced": reproduced_value,
                "delta": (
                    reproduced_value - original_value
                    if original_value is not None and reproduced_value is not None
                    else None
                ),
            }
        per_seed.append(
            {
                "original_run_id": str(orig.id),
                "reproduction_run_id": str(rep.id),
                "original_seed": orig.seed,
                "reproduction_seed": rep.seed,
                "reproduction_status": rep.status,
                "metrics": values,
            }
        )
    failed_runs = [str(r.id) for r in runs if r.status not in SUCCESS_RUN_STATUSES]
    reproduction.metric_deltas = jsonable(
        {
            "metrics": {name: result.metrics[name] for name in names if name in result.metrics},
            "reproduced_fraction": result.metrics.get("reproduced_fraction"),
            "primary_metric": primary,
            "per_seed": per_seed,
            "failed_reproduction_runs": failed_runs,
            "metric_sources": {name: sources.get(name) for name in names},
            "evaluator": {
                "key": result.evaluator_key,
                "version": result.evaluator_version,
                "confidence": result.confidence,
                "independent": result.independent,
                "warnings": result.warnings,
            },
        }
    )
    environment = db.get(ExecutionEnvironment, version.environment_id) if version.environment_id else None
    reproduction.environment_diff = jsonable(environment_diff(originals, runs, environment))
    if reproduction.status == RunState.PENDING:
        assert_transition("run", reproduction.status, RunState.RUNNING)
        reproduction.status = RunState.RUNNING
        reproduction.started_at = reproduction.started_at or utcnow()
    assert_transition("run", reproduction.status, RunState.COMPLETED)
    reproduction.status = RunState.COMPLETED
    reproduction.verdict = verdict
    reproduction.completed_at = utcnow()
    db.flush()

    evidence = anchor(
        db,
        reproduction.organization_id,
        "reproduction",
        f"Reproduction {reproduction.id} of experiment {experiment.id}: {verdict}",
        {
            "reproduction_id": reproduction.id,
            "experiment_id": experiment.id,
            "experiment_version_id": version.id,
            "spec_hash": version.spec_hash,
            "verdict": verdict,
            "original_run_ids": reproduction.original_run_ids,
            "reproduction_run_ids": [str(r.id) for r in runs],
            "tolerance": {k: v for k, v in tolerance.items() if k != "protocol"},
            "metrics_hash": canonical_hash(reproduction.metric_deltas),
            "environment_diff_hash": canonical_hash(reproduction.environment_diff),
        },
    )
    emit(
        db,
        organization_id=reproduction.organization_id,
        type=EventType.REPRODUCTION_COMPLETED,
        payload={
            "reproduction_id": str(reproduction.id),
            "experiment_id": str(experiment.id),
            "verdict": verdict,
            "reproduced_fraction": result.metrics.get("reproduced_fraction"),
            "evidence_id": str(evidence.id),
        },
        mission_id=reproduction.mission_id,
        project_id=reproduction.project_id,
        workspace_id=reproduction.workspace_id,
        subject_type="reproduction",
        subject_id=reproduction.id,
        actor=actor,
    )
    _sync_verification_checks(db, reproduction)
    if verdict == "not_reproduced":
        record_reproduction_failure(db, actor, reproduction, experiment)
    return reproduction


def _sync_verification_checks(db: Session, reproduction: Reproduction) -> None:
    """Complete the ``reproduction`` checks of verifications waiting for this reproduction; link it as evidence."""
    checks = db.scalars(select(VerificationRun).where(VerificationRun.reproduction_id == reproduction.id)).all()
    now = utcnow()
    for check in checks:
        if check.status == RunState.COMPLETED:
            continue
        if check.status == RunState.PENDING:
            assert_transition("run", check.status, RunState.RUNNING)
            check.status = RunState.RUNNING
        assert_transition("run", check.status, RunState.COMPLETED)
        check.status = RunState.COMPLETED
        check.passed = reproduction.verdict == "reproduced" if reproduction.verdict != "inconclusive" else None
        check.result = jsonable(
            {
                "reproduction_id": reproduction.id,
                "verdict": reproduction.verdict,
                "reproduced_fraction": (reproduction.metric_deltas or {}).get("reproduced_fraction"),
                "independent": True,
            }
        )
        check.completed_at = now
        verification = db.get(Verification, check.verification_id)
        claim = db.get(ScientificClaim, verification.claim_id) if verification is not None else None
        if claim is not None:
            link_rows(db, claim, [("reproduction", reproduction, "context")], note="reproduction of the claimed result")
    db.flush()


def record_reproduction_failure(
    db: Session, actor: Actor, reproduction: Reproduction, experiment: Experiment
) -> uuid.UUID | None:
    """Record a REPRODUCIBILITY_FAILURE (failures context when deployed; idempotent per reproduction)."""
    existing = db.scalar(
        select(Failure.id).where(
            Failure.experiment_id == experiment.id,
            Failure.failure_type == FailureType.REPRODUCIBILITY_FAILURE,
            Failure.classification["reproduction_id"].astext == str(reproduction.id),
        )
    )
    if existing is not None:
        return existing  # type: ignore[no-any-return]
    deltas = reproduction.metric_deltas or {}
    message = (
        f"Reproduction {reproduction.id} of experiment {experiment.id} did not reproduce the original result "
        f"(reproduced fraction {deltas.get('reproduced_fraction')})"
    )
    signals = {
        "stage": "reproduction",
        "reproduction_verdict": reproduction.verdict,
        "error_message": message,
        "n_seeds": len(reproduction.reproduction_run_ids or []),
    }
    service = optional_module("aegis_api.lab.failures.service")
    schemas = optional_module("aegis_api.lab.failures.schemas")
    record = getattr(service, "record_failure", None) if service is not None else None
    failure_input = getattr(schemas, "FailureInput", None) if schemas is not None else None
    if record is not None and failure_input is not None:
        try:
            data = failure_input.model_validate(
                {
                    "stage": "reproduction",
                    "project_id": str(reproduction.project_id),
                    "mission_id": str(reproduction.mission_id) if reproduction.mission_id else None,
                    "experiment_id": str(experiment.id),
                    "signals": signals,
                    "title": f"Reproduction failed: {experiment.title}"[:300],
                }
            )
        except ValidationError:
            log.warning("failure_input_shape_mismatch", reproduction_id=str(reproduction.id))
        else:
            failure = record(db, actor, data)
            failure_id = getattr(failure, "id", None)
            if isinstance(failure, Failure):
                classification = dict(failure.classification or {})
                classification.setdefault("reproduction_id", str(reproduction.id))
                failure.classification = classification
                db.flush()
            return failure_id if isinstance(failure_id, uuid.UUID) else None
    return _record_failure_directly(db, actor, reproduction, experiment, signals, message)


def _record_failure_directly(
    db: Session,
    actor: Actor,
    reproduction: Reproduction,
    experiment: Experiment,
    signals: dict[str, Any],
    message: str,
) -> uuid.UUID:
    """Deterministic classification + Failure row when the failures context is not deployed."""
    classification = classify_failure(FailureSignals.model_validate(signals))
    failure = Failure(
        id=uuid.uuid4(),
        organization_id=reproduction.organization_id,
        workspace_id=reproduction.workspace_id,
        project_id=reproduction.project_id,
        mission_id=reproduction.mission_id,
        experiment_id=experiment.id,
        failure_type=classification.failure_type,
        signature=classification.signature,
        title=f"Reproduction failed: {experiment.title}"[:300],
        detected_at=datetime.now(UTC),
        detected_by="platform",
        evidence=jsonable(
            [
                {
                    "reproduction_id": reproduction.id,
                    "verdict": reproduction.verdict,
                    "metric_deltas": (reproduction.metric_deltas or {}).get("metrics", {}),
                }
            ]
        ),
        classification=jsonable({**classification.model_dump(), "reproduction_id": reproduction.id}),
        root_cause=classification.root_cause,
        root_cause_source="rule",
        confidence=classification.confidence,
        recovery_action={},
        recovery_status="none",
        recurrence_count=1,
        similar_failure_ids=[],
    )
    db.add(failure)
    db.flush()
    anchor(
        db,
        failure.organization_id,
        "failure",
        f"Failure {failure.failure_type}: {experiment.title}",
        {
            "failure_id": failure.id,
            "failure_type": failure.failure_type,
            "signature": failure.signature,
            "reproduction_id": reproduction.id,
            "experiment_id": experiment.id,
            "message": message,
        },
    )
    emit(
        db,
        organization_id=failure.organization_id,
        type=EventType.FAILURE_ANALYZED,
        payload={
            "failure_id": str(failure.id),
            "failure_type": failure.failure_type,
            "signature": failure.signature,
            "experiment_id": str(experiment.id),
            "reproduction_id": str(reproduction.id),
        },
        mission_id=failure.mission_id,
        project_id=failure.project_id,
        workspace_id=failure.workspace_id,
        subject_type="failure",
        subject_id=failure.id,
        actor=actor,
    )
    return failure.id
