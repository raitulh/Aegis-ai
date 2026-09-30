"""HTTP API for the evaluator registry and evaluation runs (tag "Evaluation").

Evaluations run platform-side: the evaluator reads the run's output artifacts and held-out labels from
``evaluator_only`` dataset splits (which experiments never see) and records metrics, a verdict, confidence and
checksummed evidence. Evaluations whose inputs exceed the inline limit are queued on the EvaluationWorkflow (202).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from aegis_api.deps import get_db
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.deps import get_actor, lab_rate_limit, require_actor
from aegis_api.lab.core.idempotency import Idempotency, idempotency
from aegis_api.lab.core.pagination import CursorPage, CursorParams, cursor_params
from aegis_api.lab.evaluation import service
from aegis_api.lab.evaluation.schemas import (
    EVALUATOR_KEY_PATTERN,
    EvaluationCreate,
    EvaluationRunOut,
    EvaluationSubmitOut,
    EvaluatorCreate,
    EvaluatorOut,
)
from aegis_api.lab.models import EvaluationRun, LabEvaluator
from aegis_api.schemas.common import Page, PageParams

router = APIRouter(prefix="/api/v1", tags=["Evaluation"])

_E = {"description": "Error envelope `{error: {code, message, request_id, details}}`"}
_READ_ERRORS: dict[int | str, dict[str, Any]] = {401: _E, 403: _E, 404: _E, 422: _E}
_WRITE_ERRORS: dict[int | str, dict[str, Any]] = {401: _E, 403: _E, 404: _E, 409: _E, 422: _E, 429: _E}


def evaluator_out(row: LabEvaluator) -> EvaluatorOut:
    out = EvaluatorOut.model_validate(row)
    return out.model_copy(update=service.evaluator_info(row.kind))


def evaluation_out(row: EvaluationRun) -> EvaluationRunOut:
    return EvaluationRunOut.model_validate(row)


@router.get(
    "/evaluators",
    response_model=Page[EvaluatorOut],
    status_code=200,
    summary="List evaluators",
    description="Built-in evaluators (seeded from the evaluation engine) and your organization's evaluator "
    "versions. Filter by `kind` or `key`; deprecated built-in versions are hidden unless `include_deprecated`. "
    "Requires `evaluation:read`.",
    responses=_READ_ERRORS,
)
def list_evaluators(
    kind: str | None = Query(default=None, max_length=24),
    key: str | None = Query(default=None, max_length=80),
    include_deprecated: bool = Query(default=False),
    params: PageParams = Depends(),
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> Any:
    return service.list_evaluators(
        db, actor, params, kind=kind, key=key, include_deprecated=include_deprecated, mapper=evaluator_out
    )


@router.get(
    "/evaluators/{key}/versions",
    response_model=list[EvaluatorOut],
    status_code=200,
    summary="List the versions of an evaluator",
    description="Every version of one evaluator key (organization versions first, newest first). Versions are "
    "immutable; an organization version overrides the built-in with the same key. Requires `evaluation:read`.",
    responses=_READ_ERRORS,
)
def list_evaluator_versions(
    key: str = Path(pattern=EVALUATOR_KEY_PATTERN),
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> Any:
    return [evaluator_out(row) for row in service.evaluator_versions(db, actor, key)]


@router.post(
    "/evaluators",
    response_model=EvaluatorOut,
    status_code=201,
    summary="Create an organization evaluator version",
    description="Registers an immutable, versioned configuration of a built-in evaluator kind for your "
    "organization — e.g. `kind: custom` with `config.expression` (a restricted boolean rule such as "
    "`accuracy >= 0.9 and latency_ms < 200`, compiled by a safe AST walker; unsafe expressions are rejected with "
    "422). Signed-in humans with `evaluation:run` only. A duplicate key+version returns 409 "
    "(`evaluator_version_exists`). Supports `Idempotency-Key`.",
    responses={**_WRITE_ERRORS, 409: {"description": "The evaluator version already exists"}},
)
def create_evaluator(
    body: EvaluatorCreate,
    idem: Idempotency = Depends(idempotency),
    actor: Actor = Depends(require_actor("evaluation:run")),
    db: Session = Depends(get_db),
) -> Any:
    if (replay := idem.replay()) is not None:
        return replay
    row = service.create_evaluator(db, actor, body)
    return idem.remember(201, evaluator_out(row))


@router.post(
    "/evaluations",
    response_model=EvaluationSubmitOut,
    status_code=201,
    summary="Run an evaluation",
    description="Evaluates one experiment run, experiment (current version) or comparison with a versioned "
    "evaluator, platform-side. The classification/regression evaluators recompute metrics from the run's "
    "`predictions.csv` output and held-out labels read from an `evaluator_only` dataset split declared in the "
    "spec (or explicit `inputs`), and record evaluator-sourced metric rows. Inputs up to 5 MB are evaluated inline "
    "(201, `mode: inline`); larger evaluations are queued on the EvaluationWorkflow (202, `mode: workflow`). "
    "Requires `evaluation:run` in the project. Supports `Idempotency-Key`.",
    responses={
        **_WRITE_ERRORS,
        202: {"model": EvaluationSubmitOut, "description": "Queued on the EvaluationWorkflow"},
        413: {"description": "Inputs exceed the evaluation size limit"},
        503: {"description": "Object storage or the evaluation workflow is unavailable"},
    },
)
def create_evaluation(
    body: EvaluationCreate,
    idem: Idempotency = Depends(idempotency),
    _rate: None = Depends(lab_rate_limit("execution")),
    actor: Actor = Depends(require_actor("evaluation:run")),
    db: Session = Depends(get_db),
) -> Any:
    if (replay := idem.replay()) is not None:
        return replay
    run, mode, workflow_run_id = service.submit_evaluation(db, actor, body)
    out = EvaluationSubmitOut(mode=mode, evaluation=evaluation_out(run), workflow_run_id=workflow_run_id)
    if mode == "inline":
        return idem.remember(201, out)
    idem.remember(202, out)
    return JSONResponse(out.model_dump(mode="json"), status_code=202)


@router.get(
    "/evaluations",
    response_model=CursorPage[EvaluationRunOut],
    status_code=200,
    summary="List evaluation runs",
    description="Evaluation runs in projects you can see, newest first (cursor pagination). Filters: `project_id`, "
    "`mission_id`, `experiment_id`, `experiment_run_id`, `comparison_id`, `evaluator_key`, `status`, `passed`. "
    "Requires `evaluation:read`.",
    responses=_READ_ERRORS,
)
def list_evaluations(
    project_id: uuid.UUID | None = Query(default=None),
    mission_id: uuid.UUID | None = Query(default=None),
    experiment_id: uuid.UUID | None = Query(default=None),
    experiment_run_id: uuid.UUID | None = Query(default=None),
    comparison_id: uuid.UUID | None = Query(default=None),
    evaluator_key: str | None = Query(default=None, max_length=80),
    status: str | None = Query(default=None, max_length=16),
    passed: bool | None = Query(default=None),
    params: CursorParams = Depends(cursor_params),
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> Any:
    return service.list_evaluations(
        db,
        actor,
        params,
        project_id=project_id,
        mission_id=mission_id,
        experiment_id=experiment_id,
        experiment_run_id=experiment_run_id,
        comparison_id=comparison_id,
        evaluator_key=evaluator_key,
        status=status.upper() if status else None,
        passed=passed,
        mapper=evaluation_out,
    )


@router.get(
    "/evaluations/{evaluation_run_id}",
    response_model=EvaluationRunOut,
    status_code=200,
    summary="Get an evaluation run",
    description="Verdict (`passed`: true/false/null when undetermined), metrics, confidence, warnings, evidence "
    "(input checksums, criteria checks), the evaluator key/version/config hash and whether the evaluation is "
    "independent of the agent that produced the result. Requires `evaluation:read`.",
    responses=_READ_ERRORS,
)
def get_evaluation(
    evaluation_run_id: uuid.UUID, actor: Actor = Depends(get_actor), db: Session = Depends(get_db)
) -> Any:
    return evaluation_out(service.get_evaluation(db, actor, evaluation_run_id))
