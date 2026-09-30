"""HTTP API for research: tasks (literature search, web research, Gemini Deep Research with plan review),
research events, scientific sources and papers (tag "Research").

Research never runs in the request thread: ``POST /research/tasks`` persists the task and launches its
workflow after commit (202). Plan approval/revision records the human decision; the follow-up provider
interaction starts right after commit.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from aegis_api.deps import get_db
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.deps import get_actor, lab_rate_limit
from aegis_api.lab.core.idempotency import Idempotency, idempotency
from aegis_api.lab.core.pagination import CursorPage, CursorParams, cursor_params
from aegis_api.lab.data.schemas import ErrorEnvelope
from aegis_api.lab.research import deep_research, service, sources
from aegis_api.lab.research.schemas import (
    CancelIn,
    PaperOut,
    PlanApproveIn,
    PlanReviseIn,
    ResearchEventOut,
    ResearchTaskCreate,
    ResearchTaskDetailOut,
    ResearchTaskOut,
    SourceCreate,
    SourceKind,
    SourceOut,
)
from aegis_api.schemas.common import Page, PageParams

router = APIRouter(prefix="/api/v1", tags=["Research"])


def _err(description: str) -> dict[str, Any]:
    return {"model": ErrorEnvelope, "description": description}


READ_ERRORS: dict[int | str, dict[str, Any]] = {
    401: _err("Authentication required"),
    403: _err("Missing permission"),
    404: _err("Not found (or owned by another organization)"),
}
WRITE_ERRORS: dict[int | str, dict[str, Any]] = {
    **READ_ERRORS,
    409: _err("Invalid state transition or conflict"),
    422: _err("Invalid input"),
}


# -- tasks -----------------------------------------------------------------------------------------
@router.post(
    "/research/tasks",
    response_model=ResearchTaskOut,
    status_code=202,
    summary="Create a research task",
    description=(
        "Create a literature search, web research or Gemini Deep Research task (`research:run`). The task is "
        "executed by a background workflow; this call only validates, authorizes and records it. Deep research "
        "requires the `deep_research` feature, external-model consent and the `research.deep_research` policy "
        "(a `require_approval` decision creates an approval the task waits for — see `approval_id`). Checks the "
        "`max_research_jobs` quota and the mission's research budget. Supports `Idempotency-Key`."
    ),
    responses={
        **WRITE_ERRORS,
        403: _err("Missing permission, policy denied, feature disabled or quota exceeded"),
        409: _err("Mission budget exceeded"),
        429: _err("Research rate limit exceeded"),
        503: _err("Deep research provider not configured"),
    },
    dependencies=[Depends(lab_rate_limit("research"))],
)
def create_task(
    body: ResearchTaskCreate,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
    idem: Idempotency = Depends(idempotency),
) -> Any:
    if (replay := idem.replay()) is not None:
        return replay
    task = service.create_research_task(db, actor, body)
    return idem.remember(202, ResearchTaskOut.model_validate(task))


@router.get(
    "/research/tasks",
    response_model=CursorPage[ResearchTaskOut],
    summary="List research tasks",
    description="Research tasks in visible projects, newest first (`research:read`). Cursor-paginated.",
    responses=READ_ERRORS,
)
def list_tasks(
    project_id: uuid.UUID | None = Query(None),
    mission_id: uuid.UUID | None = Query(None),
    status: str | None = Query(None, max_length=16),
    kind: str | None = Query(None, max_length=24),
    params: CursorParams = Depends(cursor_params),
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> Any:
    return service.list_research_tasks(
        db,
        actor,
        params,
        project_id=project_id,
        mission_id=mission_id,
        status=status,
        kind=kind,
        mapper=ResearchTaskOut.model_validate,
    )


@router.get(
    "/research/tasks/{task_id}",
    response_model=ResearchTaskDetailOut,
    summary="Get a research task",
    description="A research task with its plan (deep research), provider status, usage and report text.",
    responses=READ_ERRORS,
)
def get_task(task_id: uuid.UUID, actor: Actor = Depends(get_actor), db: Session = Depends(get_db)) -> Any:
    return ResearchTaskDetailOut.model_validate(service.get_research_task(db, actor, task_id))


@router.get(
    "/research/tasks/{task_id}/events",
    response_model=CursorPage[ResearchEventOut],
    summary="List research events",
    description=(
        "Progress of a research task in order (status changes, provider step summaries, plan and report "
        "milestones). Cursor-paginated; poll with the returned cursor or follow the lab event stream."
    ),
    responses=READ_ERRORS,
)
def list_events(
    task_id: uuid.UUID,
    params: CursorParams = Depends(cursor_params),
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> Any:
    return service.list_research_events(db, actor, task_id, params, mapper=ResearchEventOut.model_validate)


@router.post(
    "/research/tasks/{task_id}/plan/approve",
    response_model=ResearchTaskOut,
    status_code=200,
    summary="Approve a deep research plan",
    description=(
        "Human decision (`research:run`, signed-in person) on a collaborative-planning task in `PLAN_REVIEW`: "
        "the task becomes `APPROVED` and the research interaction starts right after (→ `RUNNING`)."
    ),
    responses={**WRITE_ERRORS, 403: _err("Not a signed-in human or missing permission")},
)
def approve_plan(
    task_id: uuid.UUID,
    body: PlanApproveIn | None = None,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> Any:
    task = deep_research.approve_plan(db, actor, task_id, (body or PlanApproveIn()).feedback)
    return ResearchTaskOut.model_validate(task)


@router.post(
    "/research/tasks/{task_id}/plan/revise",
    response_model=ResearchTaskOut,
    status_code=200,
    summary="Request a revised deep research plan",
    description="Human feedback on the plan (`PLAN_REVIEW` → `PLANNING`); a revised plan is prepared.",
    responses={**WRITE_ERRORS, 403: _err("Not a signed-in human or missing permission")},
)
def revise_plan(
    task_id: uuid.UUID, body: PlanReviseIn, actor: Actor = Depends(get_actor), db: Session = Depends(get_db)
) -> Any:
    return ResearchTaskOut.model_validate(deep_research.revise_plan(db, actor, task_id, body.feedback))


@router.post(
    "/research/tasks/{task_id}/cancel",
    response_model=ResearchTaskOut,
    status_code=200,
    summary="Cancel a research task",
    description=(
        "Cancel a task that has not finished (`research:run`): a pending approval is withdrawn, the workflow "
        "is cancelled and a running Deep Research interaction is cancelled at the provider."
    ),
    responses=WRITE_ERRORS,
)
def cancel_task(
    task_id: uuid.UUID,
    body: CancelIn | None = None,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> Any:
    task = service.cancel_research_task(db, actor, task_id, reason=(body or CancelIn()).reason)
    return ResearchTaskOut.model_validate(task)


# -- sources ---------------------------------------------------------------------------------------
@router.get(
    "/research/sources",
    response_model=Page[SourceOut],
    summary="List scientific sources",
    description=(
        "Sources in visible projects (`research:read`). Filters: `project_id`, `source_type`, "
        "`research_task_id`; `q` runs full-text search over title and abstract (ranked unless `sort` is set)."
    ),
    responses=READ_ERRORS,
)
def list_sources(
    project_id: uuid.UUID | None = Query(None),
    source_type: str | None = Query(None, max_length=24),
    research_task_id: uuid.UUID | None = Query(None),
    q: str | None = Query(None, min_length=1, max_length=300),
    sort: str | None = Query(None, description="created_at | publication_date | title (prefix - for desc)"),
    params: PageParams = Depends(),
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> Any:
    return sources.list_sources(
        db,
        actor,
        params,
        project_id=project_id,
        source_type=source_type,
        research_task_id=research_task_id,
        q=q,
        sort=sort,
        mapper=SourceOut.model_validate,
    )


@router.get(
    "/research/sources/{source_id}",
    response_model=SourceOut,
    summary="Get a scientific source",
    description="A source with its deterministic trust metadata (rules applied and their inputs).",
    responses=READ_ERRORS,
)
def get_source(source_id: uuid.UUID, actor: Actor = Depends(get_actor), db: Session = Depends(get_db)) -> Any:
    return SourceOut.model_validate(sources.get_source(db, actor, source_id))


@router.post(
    "/research/sources",
    response_model=SourceOut,
    status_code=201,
    summary="Register a source manually",
    description=(
        "Add a paper, preprint, web page, dataset or book to a project (`research:run`). De-duplicated by DOI "
        "and canonical URL (an existing source is refreshed and returned). Trust metadata is computed by "
        "deterministic rules. The URL is stored, never fetched. Supports `Idempotency-Key`."
    ),
    responses=WRITE_ERRORS,
)
def create_source(
    body: SourceCreate,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
    idem: Idempotency = Depends(idempotency),
) -> Any:
    if (replay := idem.replay()) is not None:
        return replay
    rows = sources.upsert_sources(
        db, actor, body.project_id, [sources.SourceInput.from_create(body)], discovered_by="user"
    )
    return idem.remember(201, SourceOut.model_validate(rows[0]))


# -- papers ----------------------------------------------------------------------------------------
@router.get(
    "/papers",
    response_model=Page[PaperOut],
    summary="List papers",
    description=(
        "Papers and preprints in visible projects (`research:read`). Filters: `project_id`, `source_type` "
        "(paper|preprint), `q` (full text), `year_from`/`year_to`; `sort` by `publication_date`, `created_at` "
        "or `title` (prefix `-` for descending; default newest first, or relevance with `q`)."
    ),
    responses=READ_ERRORS,
)
def list_papers(
    project_id: uuid.UUID | None = Query(None),
    source_type: SourceKind | None = Query(None),
    q: str | None = Query(None, min_length=1, max_length=300),
    year_from: int | None = Query(None, ge=1600, le=2200),
    year_to: int | None = Query(None, ge=1600, le=2200),
    sort: str | None = Query(None),
    params: PageParams = Depends(),
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> Any:
    return sources.list_sources(
        db,
        actor,
        params,
        project_id=project_id,
        source_type=source_type,
        q=q,
        papers_only=True,
        year_from=year_from,
        year_to=year_to,
        sort=sort,
        mapper=PaperOut.model_validate,
    )


@router.get(
    "/papers/{paper_id}",
    response_model=PaperOut,
    summary="Get a paper",
    description="A paper or preprint with bibliographic and trust metadata.",
    responses=READ_ERRORS,
)
def get_paper(paper_id: uuid.UUID, actor: Actor = Depends(get_actor), db: Session = Depends(get_db)) -> Any:
    return PaperOut.model_validate(sources.get_paper(db, actor, paper_id))
