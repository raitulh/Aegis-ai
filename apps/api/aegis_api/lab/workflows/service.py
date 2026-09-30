"""Read side of workflow runs: tenant- and project-visibility-scoped listing and detail."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from aegis_api.errors import NotFound
from aegis_api.lab.core.access import get_owned, load_project, visible_project_ids
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.pagination import CursorPage, CursorParams, paginate_keyset
from aegis_api.lab.models import WorkflowRun, WorkflowStep
from aegis_api.lab.workflows import runs
from aegis_api.lab.workflows.schemas import WorkflowRunDetail, WorkflowRunOut, WorkflowStepOut

log = structlog.get_logger("aegis.lab.workflows.service")

MAX_DETAIL_STEPS = 500
MAX_DETAIL_CHILDREN = 100


@dataclass(frozen=True)
class RunFilter:
    kind: str | None = None
    status: str | None = None
    mission_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    subject_type: str | None = None
    subject_id: str | None = None
    parent_workflow_run_id: uuid.UUID | None = None


def _visibility(db: Session, actor: Actor) -> Any:
    ids = visible_project_ids(db, actor)
    if ids is None:
        return None
    return or_(WorkflowRun.project_id.is_(None), WorkflowRun.project_id.in_(ids))


def list_runs(db: Session, actor: Actor, flt: RunFilter, params: CursorParams) -> CursorPage[WorkflowRunOut]:
    stmt = select(WorkflowRun).where(WorkflowRun.organization_id == actor.organization_id)
    if flt.project_id is not None:
        load_project(db, actor, flt.project_id)
        stmt = stmt.where(WorkflowRun.project_id == flt.project_id)
    clause = _visibility(db, actor)
    if clause is not None:
        stmt = stmt.where(clause)
    if flt.kind:
        stmt = stmt.where(WorkflowRun.kind == flt.kind)
    if flt.status:
        stmt = stmt.where(WorkflowRun.status == flt.status)
    if flt.mission_id is not None:
        stmt = stmt.where(WorkflowRun.mission_id == flt.mission_id)
    if flt.subject_type:
        stmt = stmt.where(WorkflowRun.subject_type == flt.subject_type)
    if flt.subject_id:
        stmt = stmt.where(WorkflowRun.subject_id == flt.subject_id)
    if flt.parent_workflow_run_id is not None:
        stmt = stmt.where(WorkflowRun.parent_workflow_run_id == flt.parent_workflow_run_id)
    return paginate_keyset(
        db, stmt, params, time_col=WorkflowRun.created_at, id_col=WorkflowRun.id, mapper=WorkflowRunOut.from_run
    )


def get_run(db: Session, actor: Actor, workflow_run_id: uuid.UUID | str) -> WorkflowRun:
    """Load a run the actor may see (404 for foreign tenants and invisible projects)."""
    run = get_owned(db, WorkflowRun, workflow_run_id, actor, label="Workflow run")
    if run.project_id is not None:
        try:
            load_project(db, actor, run.project_id)
        except NotFound as exc:
            raise NotFound("Workflow run not found") from exc
    return run


def _step_error(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        data = json.loads(value)
    except ValueError:
        return {"type": "Error", "message": value}
    return data if isinstance(data, dict) else None


def run_detail(db: Session, actor: Actor, workflow_run_id: uuid.UUID | str) -> WorkflowRunDetail:
    run = get_run(db, actor, workflow_run_id)
    base = WorkflowRunOut.from_run(run).model_dump()
    counts = dict(
        db.execute(
            select(WorkflowStep.status, func.count())
            .where(WorkflowStep.workflow_run_id == run.id)
            .group_by(WorkflowStep.status)
        ).all()
    )
    steps = db.scalars(
        select(WorkflowStep)
        .where(WorkflowStep.workflow_run_id == run.id)
        .order_by(WorkflowStep.created_at, WorkflowStep.step_key)
        .limit(MAX_DETAIL_STEPS + 1)
    ).all()
    children = db.scalars(
        select(WorkflowRun)
        .where(WorkflowRun.parent_workflow_run_id == run.id)
        .order_by(WorkflowRun.created_at)
        .limit(MAX_DETAIL_CHILDREN)
    ).all()
    temporal: dict[str, Any] | None = None
    if run.engine == runs.ENGINE_TEMPORAL:
        temporal = temporal_description(run)
    return WorkflowRunDetail(
        **base,
        steps=[WorkflowStepOut.from_step(s, _step_error(s.error)) for s in steps[:MAX_DETAIL_STEPS]],
        step_counts={str(k): int(v) for k, v in counts.items()},
        steps_truncated=len(steps) > MAX_DETAIL_STEPS,
        children=[WorkflowRunOut.from_run(c) for c in children],
        temporal=temporal,
    )


def temporal_description(run: WorkflowRun) -> dict[str, Any]:
    """Temporal's view of the execution (best effort; never fails the request)."""
    from aegis_api.lab.workflows.temporal_engine import get_bridge

    try:
        data = get_bridge().describe(runs.temporal_workflow_id(run))
    except Exception as exc:
        log.info("temporal_describe_unavailable", workflow_run_id=str(run.id), reason=str(exc))
        return {"available": False, "error": "Temporal is unavailable"}
    return {"available": True, **data}
