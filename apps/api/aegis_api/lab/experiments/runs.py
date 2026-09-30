"""Experiment runs: scheduling into the sandbox, results ingestion with metric provenance, finalisation.

* :func:`schedule_runs` creates one :class:`ExperimentRun` per seed (``run_key = "<prefix>:<role>:<seed>"``,
  unique per version → idempotent) and admits one compute job per run through the execution fabric
  (``submit_job``; idempotency key ``exprun:<run id>``). Jobs mount the spec's dataset versions (evaluator-only
  splits are refused here *and* by the fabric), the code snapshot, ``params.json`` = parameters + ``seed`` and the
  seed environment. Nothing runs in the caller: the ExperimentWorkflow runs each job (``execution.run_job``) and
  then calls :func:`complete_run`.
* :func:`complete_run` records the outcome of a finished job. Platform-measured values (wall/CPU time, peak
  memory, exit code) are stored with ``source="platform"``; values from the experiment's own ``metrics.json`` are
  stored with ``source="self_reported"`` and are never trusted alone (independent evaluators add
  ``source="evaluator"`` rows later).
* :func:`finalize_experiment` closes the current version: ``COMPLETED`` when every run is terminal and at least one
  succeeded, ``FAILED`` otherwise, with a per-metric summary across seeds.
"""

from __future__ import annotations

import json
import math
import re
import uuid
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import structlog
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aegis_api.errors import Conflict, ValidationFailed
from aegis_api.lab.core.access import get_owned, load_project
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.errors import PolicyDenied
from aegis_api.lab.core.events import EventType, emit
from aegis_api.lab.core.evidence import append_evidence
from aegis_api.lab.core.locks import advisory_xact_lock
from aegis_api.lab.core.pagination import CursorPage, CursorParams, paginate, paginate_keyset
from aegis_api.lab.experiments import code as code_module
from aegis_api.lab.experiments.schemas import RUN_ROLES
from aegis_api.lab.experiments.service import (
    compute_prices,
    load_experiment,
    require_current_version,
    set_status,
    version_spec,
)
from aegis_api.lab.models import (
    Artifact,
    ArtifactVersion,
    ComputeJob,
    DatasetVersion,
    ExecutionEnvironment,
    Experiment,
    ExperimentMetric,
    ExperimentRun,
    ExperimentVersion,
    Hypothesis,
    Mission,
)
from aegis_api.lab.usage.recorder import increment_mission_spend
from aegis_api.schemas.common import Page, PageParams
from engines.lab.compute_cost import estimate_cost
from engines.lab.experiment_spec import MAX_SEED_VALUE, ExperimentSpec
from engines.lab.states import (
    EXECUTION_TERMINAL,
    MISSION_TERMINAL,
    ExecutionStatus,
    ExperimentStatus,
    HypothesisStatus,
    allowed_transitions,
    assert_transition,
)
from engines.lab.statistics import describe

log = structlog.get_logger("aegis.lab.experiments.runs")

E = ExecutionStatus
X = ExperimentStatus
RUN_PREFIX_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
SUCCESS_STATUSES = frozenset({E.SUCCEEDED, E.VERIFICATION_PENDING, E.VERIFIED})
JOB_TO_RUN_STATUS: dict[str, str] = {
    E.SUCCEEDED: E.SUCCEEDED,
    E.VERIFICATION_PENDING: E.SUCCEEDED,
    E.VERIFIED: E.SUCCEEDED,
    E.FAILED: E.FAILED,
    E.TIMED_OUT: E.TIMED_OUT,
    E.CANCELLED: E.CANCELLED,
}
PLATFORM_METRICS = ("wall_seconds", "cpu_seconds", "max_memory_mb", "exit_code")
MAX_STEP = 2**31 - 1
METRIC_SOURCES = ("platform", "evaluator", "self_reported")
MIB = 1024 * 1024


# =============================================================================================
# Helpers
# =============================================================================================
def run_path(current: str, target: str) -> list[str]:
    """Shortest legal path ``current → target`` through the execution state machine (excluding ``current``)."""
    if current == target:
        return []
    queue: deque[tuple[str, list[str]]] = deque([(current, [])])
    seen = {current}
    while queue:
        state, path = queue.popleft()
        for nxt in sorted(allowed_transitions("execution", state)):
            if nxt in seen:
                continue
            if nxt == target:
                return [*path, nxt]
            seen.add(nxt)
            queue.append((nxt, [*path, nxt]))
    assert_transition("execution", current, target)  # raises InvalidTransitionError with the allowed set
    return [target]  # pragma: no cover


def _advance_run(run: ExperimentRun, target: str) -> None:
    for step in run_path(run.status, target):
        assert_transition("execution", run.status, step)
        run.status = step


def get_run(db: Session, actor: Actor, run_id: uuid.UUID | str, *permissions: str) -> ExperimentRun:
    run = get_owned(db, ExperimentRun, run_id, actor, label="Experiment run")
    load_project(db, actor, run.project_id, *(permissions or ("experiment:read",)))
    return run


def list_runs(
    db: Session,
    actor: Actor,
    experiment_id: uuid.UUID | str,
    params: CursorParams,
    *,
    version_id: uuid.UUID | str | None = None,
    status: str | None = None,
    mapper: Callable[[ExperimentRun], Any] | None = None,
) -> CursorPage[Any]:
    experiment = load_experiment(db, actor, experiment_id, "experiment:read")
    stmt = select(ExperimentRun).where(ExperimentRun.experiment_id == experiment.id)
    if version_id is not None:
        stmt = stmt.where(ExperimentRun.experiment_version_id == version_id)
    if status is not None:
        if status.upper() not in {s.value for s in ExecutionStatus}:
            raise ValidationFailed(f"Unknown run status {status!r}")
        stmt = stmt.where(ExperimentRun.status == status.upper())
    return paginate_keyset(
        db,
        stmt,
        params,
        time_col=ExperimentRun.created_at,
        id_col=ExperimentRun.id,
        mapper=mapper or (lambda r: r),
        descending=False,
    )


def list_run_metrics(
    db: Session,
    actor: Actor,
    run_id: uuid.UUID | str,
    params: PageParams,
    *,
    name: str | None = None,
    source: str | None = None,
    mapper: Callable[[ExperimentMetric], Any] | None = None,
) -> Page[Any]:
    run = get_run(db, actor, run_id)
    stmt = select(ExperimentMetric).where(ExperimentMetric.experiment_run_id == run.id)
    if name:
        stmt = stmt.where(ExperimentMetric.name == name)
    if source:
        if source not in METRIC_SOURCES:
            raise ValidationFailed(f"source must be one of {', '.join(METRIC_SOURCES)}")
        stmt = stmt.where(ExperimentMetric.source == source)
    stmt = stmt.order_by(
        ExperimentMetric.name, ExperimentMetric.source, ExperimentMetric.step.nulls_first(), ExperimentMetric.id
    )
    return paginate(db, stmt, params, mapper or (lambda m: m))


def run_metric_rows(db: Session, run_id: uuid.UUID) -> list[ExperimentMetric]:
    return list(
        db.scalars(
            select(ExperimentMetric)
            .where(ExperimentMetric.experiment_run_id == run_id)
            .order_by(ExperimentMetric.name, ExperimentMetric.source, ExperimentMetric.created_at)
        )
    )


def run_artifacts(db: Session, run: ExperimentRun) -> list[tuple[Artifact, ArtifactVersion | None]]:
    artifacts = list(
        db.scalars(
            select(Artifact)
            .where(Artifact.experiment_run_id == run.id, Artifact.deleted_at.is_(None))
            .order_by(Artifact.created_at, Artifact.id)
        )
    )
    out: list[tuple[Artifact, ArtifactVersion | None]] = []
    for artifact in artifacts:
        version = db.get(ArtifactVersion, artifact.current_version_id) if artifact.current_version_id else None
        out.append((artifact, version))
    return out


def final_values(rows: Sequence[ExperimentMetric]) -> dict[tuple[str, str], float]:
    """The final value per (metric, source): the highest step, then the latest row."""
    best: dict[tuple[str, str], tuple[int, Any, float]] = {}
    for row in rows:
        if not math.isfinite(row.value):
            continue
        key = (row.name, row.source)
        rank = (row.step if row.step is not None else -1, row.created_at, row.value)
        if key not in best or (rank[0], rank[1]) >= (best[key][0], best[key][1]):
            best[key] = rank
    return {key: value[2] for key, value in best.items()}


# =============================================================================================
# Scheduling
# =============================================================================================
def _dataset_inputs(db: Session, actor: Actor, experiment: Experiment, spec: ExperimentSpec) -> list[Any]:
    from aegis_api.lab.execution.schemas import JobInput

    inputs: list[Any] = []
    for use in spec.datasets:
        try:
            version_id = uuid.UUID(use.dataset_version_id)
        except ValueError as exc:
            raise ValidationFailed(f"dataset version {use.dataset_version_id!r} is not a valid id") from exc
        row = get_owned(db, DatasetVersion, version_id, actor, label="Dataset version")
        if row.project_id != experiment.project_id:
            raise ValidationFailed(f"dataset version {row.id} belongs to a different project")
        splits = row.splits or {}
        if use.split is not None:
            info = splits.get(use.split)
            if isinstance(info, dict) and info.get("visibility") == "evaluator_only":
                raise PolicyDenied(
                    f"Split {use.split!r} of dataset version {row.id} is evaluator-only and is never mounted into "
                    "experiment sandboxes"
                )
        elif any(isinstance(v, dict) and v.get("visibility") == "evaluator_only" for v in splits.values()):
            raise PolicyDenied(
                f"Dataset version {row.id} has evaluator-only splits; the experiment must name an experiment split"
            )
        inputs.append(
            JobInput(
                kind="dataset_version",
                ref_id=row.id,
                split=use.split,
                path=f"input/{use.effective_mount_path}",
            )
        )
    return inputs


def _submit_job(
    db: Session,
    actor: Actor,
    experiment: Experiment,
    version: ExperimentVersion,
    spec: ExperimentSpec,
    run: ExperimentRun,
    inputs: list[Any],
    delivery: code_module.SeedDelivery,
    role: str,
) -> ComputeJob:
    from aegis_api.lab.execution import service as execution_service
    from aegis_api.lab.execution.schemas import JobSpec
    from engines.lab.sandbox import NetworkPolicy, ResourceRequest

    if not version.command:
        raise ValidationFailed("The experiment version has no command to run")
    seed = int(run.seed) if run.seed is not None else 0
    try:
        job_spec = JobSpec(
            project_id=experiment.project_id,
            image=None,
            command=list(version.command),
            env=delivery.env_for(seed),
            inputs=inputs,
            parameters={**spec.parameters, code_module.SEED_PARAMETER: seed},
            resources=ResourceRequest(
                cpu=spec.resources.cpu,
                memory_mb=spec.resources.memory_mb,
                disk_mb=spec.resources.disk_mb,
                gpu_type=spec.resources.gpu_type,
                gpu_count=spec.resources.gpu_count,
            ),
            timeout_seconds=version.timeout_seconds,
            network=NetworkPolicy(mode=spec.network.mode, hosts=list(spec.network.hosts)),
            mission_id=experiment.mission_id,
            experiment_id=experiment.id,
            experiment_run_id=run.id,
            code_snapshot_id=version.code_snapshot_id,
            environment_id=version.environment_id,
            purpose="reproduction" if role == "reproduction" else "experiment",
            idempotency_key=f"exprun:{run.id}",
        )
    except ValidationError as exc:
        raise ValidationFailed(
            "The experiment version cannot be turned into a compute job",
            details={"errors": exc.errors(include_url=False, include_context=False)},
        ) from exc
    return execution_service.submit_job(db, actor, job_spec)


def _seed_list(seeds: Sequence[int] | None, spec: ExperimentSpec) -> list[int]:
    raw = list(seeds) if seeds is not None else list(spec.seeds)
    out: list[int] = []
    for seed in raw:
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0 or seed > MAX_SEED_VALUE:
            raise ValidationFailed(f"seeds must be integers in [0, {MAX_SEED_VALUE}]")
        if seed not in out:
            out.append(seed)
    if not out:
        raise ValidationFailed("No seeds to schedule")
    if len(out) > 1000:
        raise ValidationFailed("At most 1000 seeds can be scheduled at once")
    return out


def schedule_runs(
    db: Session,
    actor: Actor,
    experiment_id: uuid.UUID | str,
    *,
    role: str = "candidate",
    seeds: Sequence[int] | None = None,
    run_key_prefix: str = "default",
) -> list[ExperimentRun]:
    """Create (or return) the runs of the current version for ``seeds`` and admit their compute jobs.

    Idempotent: the same prefix/role/seed returns the same run and job. The experiment must be ``QUEUED`` or
    ``COMPLETED`` (re-run) with a validated version. Mission budget: one experiment is counted per version (first
    scheduling) and the worst-case compute cost of the new runs must fit. Jobs that need a human approval stay
    ``QUEUED`` with their approval id in ``status_reason``.
    """
    if role not in RUN_ROLES:
        raise ValidationFailed(f"role must be one of {', '.join(RUN_ROLES)}")
    if not RUN_PREFIX_RE.match(run_key_prefix or ""):
        raise ValidationFailed("run_key_prefix must be 1-64 characters of letters, digits, '_', '.' or '-'")
    experiment = load_experiment(db, actor, experiment_id, "experiment:execute")
    version = require_current_version(db, experiment)
    advisory_xact_lock(db, f"experiment-schedule:{version.id}")
    spec = version_spec(version)
    seed_list = _seed_list(seeds, spec)
    keys = [f"{run_key_prefix}:{role}:{seed}" for seed in seed_list]
    existing = {
        run.run_key: run
        for run in db.scalars(
            select(ExperimentRun).where(
                ExperimentRun.experiment_version_id == version.id, ExperimentRun.run_key.in_(keys)
            )
        )
    }
    if len(existing) == len(keys) and all(run.compute_job_id for run in existing.values()):
        return [existing[key] for key in keys]
    if not version.validation_passed:
        raise Conflict("The current version has not passed design validation", code="experiment_not_validated")
    if experiment.status not in (X.QUEUED, X.COMPLETED):
        raise Conflict(
            f"Runs cannot be scheduled while the experiment is {experiment.status} (QUEUED or COMPLETED required)",
            code="invalid_state_transition",
        )
    mission = db.get(Mission, experiment.mission_id) if experiment.mission_id else None
    if mission is not None and mission.status in MISSION_TERMINAL:
        raise Conflict(f"Mission is {mission.status}; no runs can be scheduled", code="mission_not_active")
    first_for_version = not db.scalar(
        select(func.count(ExperimentRun.id)).where(ExperimentRun.experiment_version_id == version.id)
    )
    new_runs = len(keys) - len(existing)
    if mission is not None:
        from aegis_api.lab.governance.budgets import enforce_budget

        if first_for_version:
            enforce_budget(db, actor, mission, "experiment", count=1)
        per_run, _basis = estimate_cost(spec.resources, spec.timeout_seconds, compute_prices())
        if new_runs:
            enforce_budget(db, actor, mission, "compute", estimated_usd=per_run * new_runs)
    if experiment.status == X.COMPLETED:
        set_status(db, actor, experiment, X.QUEUED, f"re-running version {version.version}")
    delivery = code_module.seed_delivery(spec)
    environment = db.get(ExecutionEnvironment, version.environment_id) if version.environment_id else None
    inputs = _dataset_inputs(db, actor, experiment, spec)
    next_number = int(
        db.scalar(select(func.max(ExperimentRun.run_number)).where(ExperimentRun.experiment_id == experiment.id)) or 0
    )
    runs: list[ExperimentRun] = []
    job_ids: list[str] = []
    approvals: list[str] = []
    for seed, key in zip(seed_list, keys, strict=True):
        run = existing.get(key)
        if run is None:
            next_number += 1
            run = ExperimentRun(
                organization_id=experiment.organization_id,
                workspace_id=experiment.workspace_id,
                project_id=experiment.project_id,
                mission_id=experiment.mission_id,
                experiment_id=experiment.id,
                experiment_version_id=version.id,
                run_number=next_number,
                run_key=key,
                role=role,
                seed=seed,
                status=E.QUEUED,
                attempt=1,
                metrics={},
                environment_manifest={
                    "seed": seed,
                    "seed_delivery": delivery.as_dict(),
                    "environment_id": str(environment.id) if environment else None,
                    "environment_hash": environment.content_hash if environment else None,
                    "expected_image_digest": environment.image_digest if environment else None,
                    "code_snapshot_id": str(version.code_snapshot_id) if version.code_snapshot_id else None,
                    "spec_hash": version.spec_hash,
                },
                cost_usd=Decimal(0),
                created_by_id=actor.user_id,
            )
            db.add(run)
            db.flush()
        if run.compute_job_id is None:
            job = _submit_job(db, actor, experiment, version, spec, run, inputs, delivery, role)
            run.compute_job_id = job.id
            if job.approval_id is not None:
                run.status_reason = f"awaiting_approval:{job.approval_id}"
        stored_job = db.get(ComputeJob, run.compute_job_id)
        if stored_job is not None:
            job_ids.append(str(stored_job.id))
            if stored_job.approval_id is not None and stored_job.status == E.QUEUED:
                approvals.append(str(stored_job.approval_id))
        runs.append(run)
    db.flush()
    if first_for_version and mission is not None:
        increment_mission_spend(db, mission.id, experiments=1)
    if role == "candidate" and experiment.hypothesis_id is not None:
        hypothesis = db.get(Hypothesis, experiment.hypothesis_id)
        if hypothesis is not None and hypothesis.status in (
            HypothesisStatus.EXPERIMENT_DESIGNED,
            HypothesisStatus.INCONCLUSIVE,
        ):
            from aegis_api.lab.hypotheses.service import platform_advance

            platform_advance(
                db, actor, hypothesis, HypothesisStatus.TESTING, f"runs scheduled for experiment {experiment.id}"
            )
    emit(
        db,
        organization_id=experiment.organization_id,
        type=EventType.EXPERIMENT_QUEUED,
        payload={
            "experiment_id": str(experiment.id),
            "version_id": str(version.id),
            "role": role,
            "run_ids": [str(r.id) for r in runs],
            "job_ids": job_ids,
            "approval_ids": approvals,
        },
        mission_id=experiment.mission_id,
        project_id=experiment.project_id,
        workspace_id=experiment.workspace_id,
        subject_type="experiment",
        subject_id=experiment.id,
        actor=actor,
    )
    return runs


# =============================================================================================
# Results
# =============================================================================================
def _self_reported_items(doc: Mapping[str, Any] | None) -> tuple[list[dict[str, Any]], list[str]]:
    """Normalise the job's ``metrics`` (parsed metrics.json, or a flat ``{name: number}`` mapping)."""
    from aegis_api.lab.execution.outputs import parse_metrics

    if not doc:
        return [], []
    errors = [str(e)[:300] for e in (doc.get("errors") or [])] if isinstance(doc.get("errors"), list) else []
    if isinstance(doc.get("items"), list):
        payload: Any = doc["items"]
    elif isinstance(doc.get("values"), Mapping):
        payload = dict(doc["values"])
    else:
        payload = {k: v for k, v in doc.items() if k not in ("source", "errors")}
    parsed = parse_metrics(json.dumps(payload, default=str).encode("utf-8"))
    items = [
        item
        for item in parsed["items"]
        if len(item["name"]) <= 120 and (item.get("step") is None or 0 <= int(item["step"]) <= MAX_STEP)
    ]
    return items, [*errors, *parsed["errors"]][:50]


def _platform_values(result: Any) -> dict[str, float]:
    usage = dict(result.resource_usage or {})
    values: dict[str, float] = {}
    wall = usage.get("wall_seconds", result.duration_seconds)
    if isinstance(wall, int | float) and math.isfinite(wall):
        values["wall_seconds"] = float(wall)
    cpu = usage.get("cpu_seconds")
    if isinstance(cpu, int | float) and math.isfinite(cpu):
        values["cpu_seconds"] = float(cpu)
    memory = usage.get("max_memory_bytes")
    if isinstance(memory, int | float) and math.isfinite(memory):
        values["max_memory_mb"] = round(float(memory) / MIB, 3)
    elif isinstance(usage.get("max_memory_mb"), int | float):
        values["max_memory_mb"] = float(usage["max_memory_mb"])
    if result.exit_code is not None:
        values["exit_code"] = float(result.exit_code)
    return values


def complete_run(db: Session, actor: Actor, run_id: uuid.UUID | str, job_result: Mapping[str, Any]) -> ExperimentRun:
    """Record a finished job's outcome on its run (idempotent once the run is terminal).

    When the run's compute job is finished in the database, the stored job result is authoritative; the supplied
    ``job_result`` must reference the run's own job.
    """
    from aegis_api.lab.execution import service as execution_service
    from aegis_api.lab.execution.schemas import JobResult

    run = get_run(db, actor, run_id, "experiment:execute")
    if run.status in EXECUTION_TERMINAL:
        return run
    try:
        result = JobResult.model_validate(dict(job_result))
    except ValidationError as exc:
        raise ValidationFailed(
            "job_result is not a valid job result", details={"errors": exc.errors(include_url=False)}
        ) from exc
    if run.compute_job_id is not None and result.job_id != str(run.compute_job_id):
        raise ValidationFailed("job_result belongs to a different compute job than this run")
    job = db.get(ComputeJob, run.compute_job_id) if run.compute_job_id is not None else None
    result_source = "payload"
    if job is not None and job.status in execution_service.FINISHED_STATUSES:
        result = execution_service.job_result(job)
        result_source = "compute_job"
    target = JOB_TO_RUN_STATUS.get(result.status)
    if target is None:
        raise ValidationFailed(f"The compute job has not finished (status {result.status})")
    experiment = db.get(Experiment, run.experiment_id)
    version = db.get(ExperimentVersion, run.experiment_version_id)
    assert experiment is not None and version is not None
    if experiment.status == X.QUEUED:
        set_status(db, actor, experiment, X.RUNNING, "experiment runs are executing")
    _advance_run(run, target)
    run.exit_code = result.exit_code
    run.duration_seconds = result.duration_seconds
    run.cost_usd = Decimal(str(result.cost_usd or 0))
    run.error = (result.error or None) and str(result.error)[:8000]
    if job is not None:
        run.started_at = job.started_at
        run.completed_at = job.completed_at
        run.attempt = job.attempt
    items, metric_errors = _self_reported_items(result.metrics)
    platform = _platform_values(result)
    for name, value in platform.items():
        db.add(
            ExperimentMetric(
                organization_id=run.organization_id,
                experiment_run_id=run.id,
                name=name,
                value=value,
                source="platform",
            )
        )
    for item in items:
        db.add(
            ExperimentMetric(
                organization_id=run.organization_id,
                experiment_run_id=run.id,
                name=item["name"],
                value=float(item["value"]),
                step=item.get("step"),
                source="self_reported",
            )
        )
    self_reported: dict[str, float] = {}
    for item in items:
        self_reported[item["name"]] = float(item["value"])
    run.metrics = self_reported
    spec = version_spec(version)
    primary = spec.primary_metric
    reason = result.reason
    if (
        target == E.SUCCEEDED
        and primary is not None
        and primary.source == "self_reported"
        and primary.name not in self_reported
    ):
        reason = f"missing_primary_metric: {primary.name!r} was not reported in metrics.json"
    run.status_reason = reason[:2000] if reason else None
    expected_digest = (run.environment_manifest or {}).get("expected_image_digest")
    image_digest = job.image_digest if job is not None else None
    manifest = {
        **(run.environment_manifest or {}),
        "compute_job_id": str(run.compute_job_id) if run.compute_job_id else None,
        "job_result_source": result_source,
        "backend": job.backend if job is not None else None,
        "image": job.image if job is not None else None,
        "image_digest": image_digest,
        "digest_verified": (image_digest == expected_digest) if image_digest and expected_digest else None,
        "command": list(job.command or []) if job is not None else list(version.command or []),
        "resources": dict(job.resource_request or {}) if job is not None else dict(version.resource_request or {}),
        "network_policy": dict(job.network_policy or {}) if job is not None else dict(version.network_policy or {}),
        "resource_usage": dict(result.resource_usage or {}),
        "platform_metrics": platform,
        "outputs": dict(result.outputs),
        "logs_artifact_version_id": result.logs_artifact_version_id,
        "metric_errors": metric_errors,
    }
    run.environment_manifest = manifest
    db.flush()
    append_evidence(
        db,
        organization_id=run.organization_id,
        kind="experiment_run",
        title=f"Experiment run {run.id} {run.status}",
        content={
            "experiment_run_id": str(run.id),
            "experiment_id": str(run.experiment_id),
            "version_id": str(run.experiment_version_id),
            "spec_hash": version.spec_hash,
            "seed": run.seed,
            "status": run.status,
            "exit_code": run.exit_code,
            "compute_job_id": manifest["compute_job_id"],
            "image_digest": image_digest,
            "platform_metrics": platform,
            "self_reported_metrics": self_reported,
            "outputs": dict(result.outputs),
        },
    )
    event = EventType.EXPERIMENT_COMPLETED if target == E.SUCCEEDED else EventType.EXPERIMENT_FAILED
    emit(
        db,
        organization_id=run.organization_id,
        type=event,
        payload={
            "experiment_id": str(run.experiment_id),
            "experiment_run_id": str(run.id),
            "status": run.status,
            "reason": run.status_reason,
            "seed": run.seed,
            "exit_code": run.exit_code,
            "metrics": self_reported,
            "metric_source": "self_reported",
        },
        mission_id=run.mission_id,
        project_id=run.project_id,
        workspace_id=run.workspace_id,
        subject_type="experiment_run",
        subject_id=run.id,
        actor=actor,
    )
    return run


# =============================================================================================
# Summaries and finalisation
# =============================================================================================
def _summary_values(values: list[float]) -> dict[str, Any]:
    stats = describe(values)
    return {k: stats[k] for k in ("n", "mean", "sd", "min", "max")}


def run_summary(db: Session, version_id: uuid.UUID) -> dict[str, Any]:
    """Status counts and, per metric source, mean/sd/min/max/n of the final values across successful runs."""
    runs = list(db.scalars(select(ExperimentRun).where(ExperimentRun.experiment_version_id == version_id)))
    by_status: dict[str, int] = {}
    for run in runs:
        by_status[run.status] = by_status.get(run.status, 0) + 1
    succeeded = [r for r in runs if r.status in SUCCESS_STATUSES]
    collected: dict[str, dict[str, list[float]]] = {}
    if succeeded:
        rows = db.scalars(
            select(ExperimentMetric).where(ExperimentMetric.experiment_run_id.in_([r.id for r in succeeded]))
        ).all()
        per_run: dict[uuid.UUID, list[ExperimentMetric]] = {}
        for row in rows:
            per_run.setdefault(row.experiment_run_id, []).append(row)
        for run_rows in per_run.values():
            for (name, source), value in final_values(run_rows).items():
                collected.setdefault(source, {}).setdefault(name, []).append(value)
    metrics = {
        source: {name: _summary_values(values) for name, values in sorted(by_name.items())}
        for source, by_name in sorted(collected.items())
    }
    return {
        "version_id": str(version_id),
        "total": len(runs),
        "by_status": dict(sorted(by_status.items())),
        "succeeded": len(succeeded),
        "terminal": bool(runs) and all(r.status in EXECUTION_TERMINAL for r in runs),
        "metrics": metrics,
    }


@dataclass
class FinalizeResult:
    experiment: Experiment
    summary: dict[str, Any]
    changed: bool


def finalize_experiment(db: Session, actor: Actor, experiment_id: uuid.UUID | str) -> FinalizeResult:
    """``COMPLETED`` when all runs of the current version are terminal and ≥ 1 succeeded, ``FAILED`` otherwise."""
    experiment = load_experiment(db, actor, experiment_id, "experiment:execute")
    version = require_current_version(db, experiment)
    summary = run_summary(db, version.id)
    if summary["total"] == 0:
        raise Conflict("The current version has no runs to finalize", code="experiment_without_runs")
    if not summary["terminal"]:
        raise Conflict(
            "Some runs of the current version are still active",
            code="runs_not_finished",
            details={"by_status": summary["by_status"]},
        )
    if experiment.status not in (X.QUEUED, X.RUNNING):
        return FinalizeResult(experiment, summary, changed=False)
    target = X.COMPLETED if summary["succeeded"] else X.FAILED
    if target == X.COMPLETED and experiment.status == X.QUEUED:
        set_status(db, actor, experiment, X.RUNNING, "finalising runs")
    reason = f"{summary['succeeded']}/{summary['total']} run(s) of version {version.version} succeeded"
    set_status(db, actor, experiment, target, reason)
    append_evidence(
        db,
        organization_id=experiment.organization_id,
        kind="experiment_summary",
        title=f"Experiment {experiment.id} {target}",
        content={
            "experiment_id": str(experiment.id),
            "version_id": str(version.id),
            "spec_hash": version.spec_hash,
            "status": target,
            "summary": summary,
        },
    )
    emit(
        db,
        organization_id=experiment.organization_id,
        type=EventType.EXPERIMENT_COMPLETED if target == X.COMPLETED else EventType.EXPERIMENT_FAILED,
        payload={
            "experiment_id": str(experiment.id),
            "version_id": str(version.id),
            "status": target,
            "reason": reason,
            "run_summary": summary,
        },
        mission_id=experiment.mission_id,
        project_id=experiment.project_id,
        workspace_id=experiment.workspace_id,
        subject_type="experiment",
        subject_id=experiment.id,
        actor=actor,
    )
    return FinalizeResult(experiment, summary, changed=True)
