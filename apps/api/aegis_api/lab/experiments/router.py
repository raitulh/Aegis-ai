"""HTTP API for experiments (tag "Experiments"): design, immutable versions, validation, execution requests,
runs and metrics (with provenance), comparisons and reproducibility packages.

Execution requests return ``202`` and never run anything in the request thread: they apply governance and launch
the ExperimentWorkflow after commit; sandboxed jobs run on execution workers.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from aegis_api.deps import get_db
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.deps import get_actor, lab_rate_limit, require_actor
from aegis_api.lab.core.idempotency import Idempotency, idempotency
from aegis_api.lab.core.pagination import CursorPage, CursorParams, cursor_params
from aegis_api.lab.experiments import comparisons, reproducibility, runs, service
from aegis_api.lab.experiments.schemas import (
    ArchiveRequest,
    ComparisonCreate,
    ComparisonOut,
    ExecuteOut,
    ExperimentCreate,
    ExperimentDetailOut,
    ExperimentMetricOut,
    ExperimentOut,
    ExperimentRunDetailOut,
    ExperimentRunOut,
    ExperimentVersionCreate,
    ExperimentVersionOut,
    RunArtifactOut,
    RunJobOut,
    RunSummaryOut,
    ValidationOut,
)
from aegis_api.lab.models import ComputeJob, Experiment, ExperimentRun
from aegis_api.schemas.common import Page, PageParams

router = APIRouter(prefix="/api/v1", tags=["Experiments"])

_E = {"description": "Error envelope `{error: {code, message, request_id, details}}`"}
READ_ERRORS: dict[int | str, dict[str, Any]] = {401: _E, 403: _E, 404: _E, 422: _E}
WRITE_ERRORS: dict[int | str, dict[str, Any]] = {401: _E, 403: _E, 404: _E, 409: _E, 422: _E}


def _detail(db: Session, experiment: Experiment) -> ExperimentDetailOut:
    out = ExperimentDetailOut.model_validate(experiment)
    version = service.current_version(db, experiment)
    if version is not None:
        out.current_version = ExperimentVersionOut.model_validate(version)
        out.validation_report = dict(version.validation_report or {})
        out.run_summary = RunSummaryOut.model_validate(runs.run_summary(db, version.id))
    return out


def _run_detail(db: Session, run: ExperimentRun) -> ExperimentRunDetailOut:
    out = ExperimentRunDetailOut.model_validate(run)
    out.metric_rows = [ExperimentMetricOut.model_validate(m) for m in runs.run_metric_rows(db, run.id)]
    out.artifacts = [
        RunArtifactOut(
            artifact_id=str(artifact.id),
            name=artifact.name,
            kind=artifact.kind,
            version_id=str(version.id) if version is not None else None,
            checksum=version.checksum if version is not None else None,
            size_bytes=version.size_bytes if version is not None else None,
            mime_type=version.mime_type if version is not None else None,
        )
        for artifact, version in runs.run_artifacts(db, run)
    ]
    job = db.get(ComputeJob, run.compute_job_id) if run.compute_job_id else None
    if job is not None and job.organization_id == run.organization_id:
        out.job = RunJobOut(
            id=str(job.id),
            status=job.status,
            status_reason=job.status_reason,
            backend=job.backend,
            image=job.image,
            image_digest=job.image_digest,
            exit_code=job.exit_code,
            approval_id=str(job.approval_id) if job.approval_id else None,
            estimated_cost_usd=job.estimated_cost_usd,
            cost_usd=job.cost_usd,
        )
    return out


@router.post(
    "/experiments",
    response_model=ExperimentDetailOut,
    status_code=201,
    summary="Create an experiment",
    description=(
        "Creates an experiment and its immutable version 1 from an `ExperimentSpec`. Inline code becomes a "
        "content-addressed code snapshot; the runtime a content-addressed environment. The design validator and "
        "platform checks produce a validation report stored on the version: a passing design is `QUEUED`, a failing "
        "one stays `DRAFT` with actionable issues (never executed). Hypothesis, baseline, parent and dataset versions "
        "must belong to the same project. Supports `Idempotency-Key`. Requires `experiment:create`."
    ),
    responses={**WRITE_ERRORS, 409: {"description": "Mission not active or hypothesis archived"}},
)
def create_experiment(
    body: ExperimentCreate,
    idem: Idempotency = Depends(idempotency),
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> Any:
    if (replay := idem.replay()) is not None:
        return replay
    experiment = service.create_experiment(db, actor, body)
    return idem.remember(201, _detail(db, experiment))


@router.get(
    "/experiments",
    response_model=CursorPage[ExperimentOut],
    status_code=200,
    summary="List experiments",
    description=(
        "Experiments of projects you can see (cursor pagination). Filters: `project_id`, `mission_id`, "
        "`hypothesis_id`, `status`, `kind`; `sort` is `-created_at` (default), `created_at`, `-updated_at` or "
        "`updated_at`. Requires `experiment:read`."
    ),
    responses=READ_ERRORS,
)
def list_experiments(
    project_id: uuid.UUID | None = Query(default=None),
    mission_id: uuid.UUID | None = Query(default=None),
    hypothesis_id: uuid.UUID | None = Query(default=None),
    status: str | None = Query(default=None, max_length=16),
    kind: str | None = Query(default=None, max_length=16),
    sort: str | None = Query(default=None, max_length=16),
    params: CursorParams = Depends(cursor_params),
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> Any:
    return service.list_experiments(
        db,
        actor,
        params,
        project_id=project_id,
        mission_id=mission_id,
        hypothesis_id=hypothesis_id,
        status=status,
        kind=kind,
        sort=sort,
        mapper=ExperimentOut.model_validate,
    )


@router.get(
    "/experiments/{experiment_id}",
    response_model=ExperimentDetailOut,
    status_code=200,
    summary="Get an experiment",
    description="The experiment with its current version, validation report and run summary. Requires `experiment:read`.",
    responses=READ_ERRORS,
)
def get_experiment(
    experiment_id: uuid.UUID, actor: Actor = Depends(get_actor), db: Session = Depends(get_db)
) -> ExperimentDetailOut:
    return _detail(db, service.get_experiment(db, actor, experiment_id))


@router.get(
    "/experiments/{experiment_id}/versions",
    response_model=Page[ExperimentVersionOut],
    status_code=200,
    summary="List experiment versions",
    description="Immutable versions, newest first. Requires `experiment:read`.",
    responses=READ_ERRORS,
)
def list_versions(
    experiment_id: uuid.UUID,
    params: PageParams = Depends(),
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> Any:
    return service.list_versions(db, actor, experiment_id, params, mapper=ExperimentVersionOut.model_validate)


@router.post(
    "/experiments/{experiment_id}/versions",
    response_model=ExperimentDetailOut,
    status_code=201,
    summary="Create a new experiment version",
    description=(
        "Adds a new immutable version (existing versions are never overwritten) and re-validates it. Allowed while "
        "the experiment is DRAFT, FAILED or COMPLETED (409 otherwise); `spec.kind` cannot change. Requires "
        "`experiment:create`."
    ),
    responses=WRITE_ERRORS,
)
def create_version(
    experiment_id: uuid.UUID,
    body: ExperimentVersionCreate,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> ExperimentDetailOut:
    service.create_version(db, actor, experiment_id, body.spec, change_summary=body.change_summary)
    return _detail(db, service.get_experiment(db, actor, experiment_id))


@router.post(
    "/experiments/{experiment_id}/validate",
    response_model=ValidationOut,
    status_code=200,
    summary="Re-validate the current version",
    description=(
        "Re-runs design validation on the current version's spec with current limits, datasets and evaluators. An "
        "unchanged outcome only updates the status (DRAFT → QUEUED when it passes); a changed outcome is recorded "
        "as a new immutable version with the same spec. Requires `experiment:create`."
    ),
    responses=WRITE_ERRORS,
)
def validate_experiment(
    experiment_id: uuid.UUID, actor: Actor = Depends(get_actor), db: Session = Depends(get_db)
) -> ValidationOut:
    outcome = service.validate_experiment(db, actor, experiment_id)
    return ValidationOut(
        experiment_id=str(outcome.experiment.id),
        version_id=str(outcome.version.id),
        version=outcome.version.version,
        passed=bool(outcome.version.validation_passed),
        status=outcome.experiment.status,
        new_version_created=outcome.new_version_created,
        report=outcome.report,
    )


@router.post(
    "/experiments/{experiment_id}/execute",
    response_model=ExecuteOut,
    status_code=202,
    summary="Execute an experiment",
    description=(
        "Requests execution of the current, validated version: applies the governance policy `experiment.execute` "
        "(deny → 403; approval required → `awaiting_approval` with `approval_id`) and launches the "
        "ExperimentWorkflow after commit, which schedules one sandboxed run per seed. A running workflow is returned "
        "instead of starting a second one. Supports `Idempotency-Key`; rate limited. Requires `experiment:execute`."
    ),
    responses={
        **WRITE_ERRORS,
        409: {"description": "Not validated, wrong status or mission not active"},
        429: _E,
        503: {"description": "Workflow engine or ExperimentWorkflow unavailable"},
    },
)
def execute_experiment(
    experiment_id: uuid.UUID,
    idem: Idempotency = Depends(idempotency),
    _rate: None = Depends(lab_rate_limit("execution")),
    actor: Actor = Depends(require_actor("experiment:execute")),
    db: Session = Depends(get_db),
) -> Any:
    if (replay := idem.replay()) is not None:
        return replay
    request = service.request_execution(db, actor, experiment_id)
    return idem.remember(202, ExecuteOut(**request.as_dict()))


@router.get(
    "/experiments/{experiment_id}/runs",
    response_model=CursorPage[ExperimentRunOut],
    status_code=200,
    summary="List experiment runs",
    description="Runs of the experiment (oldest first; filter by `version_id` and `status`). Requires `experiment:read`.",
    responses=READ_ERRORS,
)
def list_runs(
    experiment_id: uuid.UUID,
    version_id: uuid.UUID | None = Query(default=None),
    status: str | None = Query(default=None, max_length=24),
    params: CursorParams = Depends(cursor_params),
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> Any:
    return runs.list_runs(
        db,
        actor,
        experiment_id,
        params,
        version_id=version_id,
        status=status,
        mapper=ExperimentRunOut.model_validate,
    )


@router.get(
    "/experiment-runs/{run_id}",
    response_model=ExperimentRunDetailOut,
    status_code=200,
    summary="Get an experiment run",
    description=(
        "One run with its metrics (each with its source: platform, evaluator or self_reported), output artifacts, "
        "environment manifest and compute job summary. Requires `experiment:read`."
    ),
    responses=READ_ERRORS,
)
def get_run(
    run_id: uuid.UUID, actor: Actor = Depends(get_actor), db: Session = Depends(get_db)
) -> ExperimentRunDetailOut:
    return _run_detail(db, runs.get_run(db, actor, run_id))


@router.get(
    "/experiment-runs/{run_id}/metrics",
    response_model=Page[ExperimentMetricOut],
    status_code=200,
    summary="List run metrics",
    description=(
        "Metric rows of a run; filter by `name` and `source` (platform | evaluator | self_reported). Self-reported "
        "values are written by the experiment code and are never sufficient on their own for verification. "
        "Requires `experiment:read`."
    ),
    responses=READ_ERRORS,
)
def list_run_metrics(
    run_id: uuid.UUID,
    name: str | None = Query(default=None, max_length=120),
    source: str | None = Query(default=None, max_length=16),
    params: PageParams = Depends(),
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> Any:
    return runs.list_run_metrics(
        db, actor, run_id, params, name=name, source=source, mapper=ExperimentMetricOut.model_validate
    )


@router.post(
    "/comparisons",
    response_model=ComparisonOut,
    status_code=201,
    summary="Compare two experiments",
    description=(
        "Compares the current versions of a baseline and a candidate experiment of the same project on one metric "
        "(default: the candidate's primary metric) using the candidate's pre-registered statistical plan. The "
        "configuration diff between the two specs is always recorded; the metric source (evaluator / platform / "
        "self_reported) is reported and self-reported values are flagged. Ablation and sensitivity candidates "
        "produce ablation/sensitivity comparisons. Requires `evaluation:run`."
    ),
    responses=WRITE_ERRORS,
)
def create_comparison(
    body: ComparisonCreate, actor: Actor = Depends(get_actor), db: Session = Depends(get_db)
) -> ComparisonOut:
    comparison = comparisons.compare(db, actor, body.baseline_experiment_id, body.candidate_experiment_id, body.metric)
    return ComparisonOut.model_validate(comparison)


@router.get(
    "/comparisons/{comparison_id}",
    response_model=ComparisonOut,
    status_code=200,
    summary="Get a comparison",
    description="Statistics, verdict, configuration diff and run ids of a comparison. Requires `experiment:read`.",
    responses=READ_ERRORS,
)
def get_comparison(
    comparison_id: uuid.UUID, actor: Actor = Depends(get_actor), db: Session = Depends(get_db)
) -> ComparisonOut:
    return ComparisonOut.model_validate(comparisons.get_comparison(db, actor, comparison_id))


@router.get(
    "/experiments/{experiment_id}/comparisons",
    response_model=Page[ComparisonOut],
    status_code=200,
    summary="List comparisons of an experiment",
    description="Comparisons where the experiment is the baseline or the candidate, newest first.",
    responses=READ_ERRORS,
)
def list_comparisons(
    experiment_id: uuid.UUID,
    params: PageParams = Depends(),
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> Any:
    return comparisons.list_for_experiment(db, actor, experiment_id, params, mapper=ComparisonOut.model_validate)


@router.get(
    "/experiments/{experiment_id}/reproducibility-package",
    response_model=dict[str, Any],
    status_code=200,
    summary="Get the reproducibility package",
    description=(
        "Everything needed to reproduce and audit a version: spec + validation report, code snapshot, dataset "
        "versions (checksums, splits, licences), environment (image, digest, dependencies, lock), env-var policy and "
        "seed delivery, seeds, command, resources, per-run hardware/backend, logs and outputs with checksums, metrics "
        "with sources, evaluator versions, agent model provenance and comparisons. `format=zip` streams a zip with "
        "`manifest.json`, `README.md` (exact reproduction commands), per-seed params and the code. Requires "
        "`experiment:read`."
    ),
    responses={**READ_ERRORS, 200: {"content": {"application/json": {}, "application/zip": {}}}, 413: _E},
)
def get_reproducibility_package(
    experiment_id: uuid.UUID,
    format: Literal["json", "zip"] = Query(default="json"),
    version: int | None = Query(default=None, ge=1),
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> Any:
    if format == "json":
        return reproducibility.reproducibility_package(db, actor, experiment_id, version)
    files = reproducibility.build_package_files(db, actor, experiment_id, version)
    return StreamingResponse(
        reproducibility.stream_package_zip(files),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{files.filename}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


@router.post(
    "/experiments/{experiment_id}/archive",
    response_model=ExperimentOut,
    status_code=200,
    summary="Archive an experiment",
    description=(
        "Archives the experiment (state machine permitting; 409 while runs are active). Versions, runs and metrics "
        "are kept. Requires `experiment:create`."
    ),
    responses=WRITE_ERRORS,
)
def archive_experiment(
    experiment_id: uuid.UUID,
    body: ArchiveRequest | None = None,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> ExperimentOut:
    reason = body.reason if body is not None else None
    return ExperimentOut.model_validate(service.archive_experiment(db, actor, experiment_id, reason))
