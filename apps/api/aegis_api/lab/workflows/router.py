"""HTTP API for durable workflow runs (tag "Workflows"): observe, cancel, retry and signal."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from aegis_api.deps import get_db
from aegis_api.errors import Forbidden
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.deps import get_actor
from aegis_api.lab.core.idempotency import Idempotency, idempotency
from aegis_api.lab.core.pagination import CursorPage, CursorParams, cursor_params
from aegis_api.lab.workflows import launcher, service
from aegis_api.lab.workflows.schemas import (
    WorkflowRetryIn,
    WorkflowRunDetail,
    WorkflowRunOut,
    WorkflowSignalIn,
    WorkflowSignalOut,
)
from engines.lab.states import WorkflowStatus

router = APIRouter(prefix="/api/v1", tags=["Workflows"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"description": "Missing permission"},
    404: {"description": "Workflow run not found (or not visible)"},
}
_TRANSITION_ERRORS: dict[int | str, dict[str, Any]] = {
    **_ERRORS,
    409: {"description": "The run's current status does not allow this operation (invalid_state_transition)"},
}


def _require_any(actor: Actor, *permissions: str) -> None:
    if not any(actor.has(p) for p in permissions):
        raise Forbidden(f"Requires one of: {', '.join(permissions)}")


@router.get(
    "/workflow-runs",
    response_model=CursorPage[WorkflowRunOut],
    status_code=200,
    summary="List workflow runs",
    description=(
        "Durable workflow runs of the organization, newest first (cursor pagination). Runs of restricted "
        "projects are only listed for their members. Requires `mission:read` or `execution:read`."
    ),
    responses=_ERRORS,
)
def list_workflow_runs(
    kind: str | None = Query(None, max_length=48, description="Workflow kind, e.g. MissionWorkflow"),
    status: WorkflowStatus | None = Query(None, description="Run status"),
    mission_id: uuid.UUID | None = Query(None),
    project_id: uuid.UUID | None = Query(None),
    subject_type: str | None = Query(None, max_length=32),
    subject_id: str | None = Query(None, max_length=64),
    parent_workflow_run_id: uuid.UUID | None = Query(None),
    params: CursorParams = Depends(cursor_params),
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> CursorPage[WorkflowRunOut]:
    _require_any(actor, "mission:read", "execution:read")
    flt = service.RunFilter(
        kind=kind,
        status=status.value if status else None,
        mission_id=mission_id,
        project_id=project_id,
        subject_type=subject_type,
        subject_id=subject_id,
        parent_workflow_run_id=parent_workflow_run_id,
    )
    return service.list_runs(db, actor, flt, params)


@router.get(
    "/workflow-runs/{workflow_run_id}",
    response_model=WorkflowRunDetail,
    status_code=200,
    summary="Get a workflow run",
    description=(
        "A workflow run with its recorded steps (local engine), direct children and — on the Temporal "
        "engine — Temporal's description of the execution. Requires `mission:read` or `execution:read`."
    ),
    responses=_ERRORS,
)
def get_workflow_run(
    workflow_run_id: uuid.UUID,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> WorkflowRunDetail:
    _require_any(actor, "mission:read", "execution:read")
    return service.run_detail(db, actor, workflow_run_id)


@router.post(
    "/workflow-runs/{workflow_run_id}/cancel",
    response_model=WorkflowRunOut,
    status_code=202,
    summary="Cancel a workflow run",
    description=(
        "Requests cancellation. PENDING runs are cancelled immediately; running runs receive the request "
        "at their next step and end CANCELLED. Idempotent for already-cancelled runs. Requires "
        "`mission:cancel` or `execution:manage`."
    ),
    responses=_TRANSITION_ERRORS,
)
def cancel_workflow_run(
    workflow_run_id: uuid.UUID,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> WorkflowRunOut:
    _require_any(actor, "mission:cancel", "execution:manage")
    run = launcher.cancel_workflow(db, actor, workflow_run_id)
    return WorkflowRunOut.from_run(run)


@router.post(
    "/workflow-runs/{workflow_run_id}/retry",
    response_model=WorkflowRunOut,
    status_code=202,
    summary="Retry a failed workflow run",
    description=(
        "Starts a new attempt of a FAILED or TIMED_OUT run. On the local engine the new attempt resumes from "
        "the point of failure (completed steps are not executed again) unless `from_scratch` is set. "
        "Supports `Idempotency-Key`. Requires `mission:run`."
    ),
    responses=_TRANSITION_ERRORS,
)
def retry_workflow_run(
    workflow_run_id: uuid.UUID,
    body: WorkflowRetryIn | None = None,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
    idem: Idempotency = Depends(idempotency),
) -> Any:
    actor.require("mission:run")
    if (replay := idem.replay()) is not None:
        return replay
    run = launcher.retry_workflow(db, actor, workflow_run_id, from_scratch=bool(body and body.from_scratch))
    return idem.remember(202, WorkflowRunOut.from_run(run))


@router.post(
    "/workflow-runs/{workflow_run_id}/signal",
    response_model=WorkflowSignalOut,
    status_code=202,
    summary="Signal a workflow run",
    description=(
        "Delivers a signal to a waiting workflow. Allowed names: `resume` (requires `mission:run`) and "
        "`approval` (requires `approval:decide` and a signed-in human; approval decisions are normally sent "
        "by the approvals service). Supports `Idempotency-Key`."
    ),
    responses={**_TRANSITION_ERRORS, 422: {"description": "Unknown signal name or invalid payload"}},
)
def signal_workflow_run(
    workflow_run_id: uuid.UUID,
    body: WorkflowSignalIn,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
    idem: Idempotency = Depends(idempotency),
) -> Any:
    if body.name == "approval":
        actor.require("approval:decide")
        actor.require_human("signal approval")
    else:
        actor.require("mission:run")
    if (replay := idem.replay()) is not None:
        return replay
    run = service.get_run(db, actor, workflow_run_id)
    run = launcher.signal_workflow(db, run.id, body.name, body.payload, actor=actor)
    out = WorkflowSignalOut(workflow_run_id=str(run.id), name=body.name, status=run.status)
    return idem.remember(202, out)
