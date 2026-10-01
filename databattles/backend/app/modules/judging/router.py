from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import Field, field_validator

from app.core.deps import Actor, get_actor, require_user
from app.core.schemas import Message, Schema
from app.core.time import ensure_aware
from app.modules.competitions.service import get_by_slug, load_managed, load_visible
from app.modules.judging import service

router = APIRouter(tags=["judging"])


class Criterion(Schema):
    key: str = Field(min_length=2, max_length=32)
    label: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=300)
    min: float = 0
    max: float = 10
    weight: float = 1
    allow_decimal: bool = False


class RubricIn(Schema):
    criteria: list[Criterion] | None = None
    reveal_scores_to_judges: bool | None = None
    blind_judging: bool | None = None


class RubricOut(Schema):
    criteria: list[dict[str, Any]]
    version: int
    reveal_scores_to_judges: bool
    blind_judging: bool
    locked: bool
    finalized: bool


class AssignIn(Schema):
    handle: str = Field(max_length=31)
    team_id: uuid.UUID | None = None
    panel: str | None = Field(default=None, max_length=60)


class ConflictIn(Schema):
    judge_id: uuid.UUID
    team_id: uuid.UUID
    reason: str = Field(min_length=3, max_length=300)


class ScoreIn(Schema):
    scores: dict[str, float]
    feedback: str | None = Field(default=None, max_length=5000)
    submit: bool = False


class EventSubmissionIn(Schema):
    title: str = Field(min_length=3, max_length=140)
    summary: str | None = Field(default=None, max_length=300)
    description_md: str | None = Field(default=None, max_length=50_000)
    repo_url: str | None = Field(default=None, max_length=500)
    demo_url: str | None = Field(default=None, max_length=500)
    video_url: str | None = Field(default=None, max_length=500)


class SlotIn(Schema):
    team_id: uuid.UUID | None = None
    starts_at: datetime
    ends_at: datetime
    location: str | None = Field(default=None, max_length=200)
    meeting_url: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=500)

    @field_validator("starts_at", "ends_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        return ensure_aware(v)  # type: ignore[return-value]


def _rubric_out(rubric: Any) -> RubricOut:
    if rubric is None:
        return RubricOut(criteria=[], version=0, reveal_scores_to_judges=False, blind_judging=False, locked=False, finalized=False)
    return RubricOut(criteria=rubric.criteria, version=rubric.version, reveal_scores_to_judges=rubric.reveal_scores_to_judges,
                     blind_judging=rubric.blind_judging, locked=rubric.locked_at is not None, finalized=rubric.finalized_at is not None)


@router.get("/competitions/{slug}/rubric", response_model=RubricOut)
def get_rubric(slug: str, actor: Actor = Depends(get_actor)) -> RubricOut:
    comp = load_visible(actor, slug)
    return _rubric_out(service.get_rubric(actor.db, comp))


@router.put("/competitions/{slug}/rubric", response_model=RubricOut)
def put_rubric(slug: str, data: RubricIn, actor: Actor = Depends(require_user)) -> RubricOut:
    comp = load_managed(actor, slug)
    criteria = [c.model_dump() for c in data.criteria] if data.criteria is not None else None
    return _rubric_out(service.upsert_rubric(actor, comp, criteria, data.reveal_scores_to_judges, data.blind_judging))


@router.post("/competitions/{slug}/judges", response_model=Message, status_code=201)
def assign(slug: str, data: AssignIn, actor: Actor = Depends(require_user)) -> Message:
    service.assign_judge(actor, load_managed(actor, slug), data.handle, data.team_id, data.panel)
    return Message(message="Judge assigned.")


@router.delete("/competitions/{slug}/judges/{assignment_id}", response_model=Message)
def unassign(slug: str, assignment_id: uuid.UUID, actor: Actor = Depends(require_user)) -> Message:
    service.remove_assignment(actor, load_managed(actor, slug), assignment_id)
    return Message(message="Assignment removed.")


@router.post("/competitions/{slug}/judging/conflicts", response_model=Message, status_code=201)
def conflict(slug: str, data: ConflictIn, actor: Actor = Depends(require_user)) -> Message:
    comp = get_by_slug(actor.db, slug)
    if comp is None:
        from app.core.errors import NotFound

        raise NotFound()
    service.declare_conflict(actor, comp, data.judge_id, data.team_id, data.reason)
    return Message(message="Conflict recorded.")


@router.get("/competitions/{slug}/judging/queue")
def queue(slug: str, actor: Actor = Depends(require_user)) -> dict[str, Any]:
    comp = get_by_slug(actor.db, slug)
    if comp is None:
        from app.core.errors import NotFound

        raise NotFound()
    return service.judge_queue(actor, comp)


@router.put("/competitions/{slug}/judging/scores/{team_id}", response_model=Message)
def score(slug: str, team_id: uuid.UUID, data: ScoreIn, actor: Actor = Depends(require_user)) -> Message:
    comp = get_by_slug(actor.db, slug)
    if comp is None:
        from app.core.errors import NotFound

        raise NotFound()
    row = service.submit_score(actor, comp, team_id, data.scores, data.feedback, data.submit)
    return Message(message="Score submitted." if row.status == "submitted" else "Draft saved.")


@router.get("/competitions/{slug}/judging/overview")
def overview(slug: str, actor: Actor = Depends(require_user)) -> dict[str, Any]:
    comp = get_by_slug(actor.db, slug)
    if comp is None:
        from app.core.errors import NotFound

        raise NotFound()
    return service.scores_overview(actor, comp)


@router.post("/competitions/{slug}/judging/finalize", response_model=Message)
def finalize(slug: str, actor: Actor = Depends(require_user)) -> Message:
    service.finalize_judging(actor, load_managed(actor, slug))
    return Message(message="Judging finalized; scores are locked.")


@router.get("/competitions/{slug}/judging/scores.csv", response_class=Response)
def export(slug: str, actor: Actor = Depends(require_user)) -> Response:
    comp = load_managed(actor, slug)
    return Response(service.export_scores_csv(actor, comp), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{comp.slug}-judging.csv"'})


@router.get("/competitions/{slug}/project-submissions")
def project_submissions(slug: str, actor: Actor = Depends(get_actor)) -> list[dict[str, Any]]:
    return service.event_submissions(actor, load_visible(actor, slug))


@router.put("/competitions/{slug}/project-submission", response_model=Message)
def upsert_project_submission(slug: str, data: EventSubmissionIn, actor: Actor = Depends(require_user)) -> Message:
    service.upsert_event_submission(actor, load_visible(actor, slug), data.model_dump())
    return Message(message="Project submission saved.")


@router.get("/competitions/{slug}/presentations")
def slots(slug: str, actor: Actor = Depends(get_actor)) -> list[dict[str, Any]]:
    return service.list_slots(actor, load_visible(actor, slug))


@router.post("/competitions/{slug}/presentations", response_model=Message, status_code=201)
def add_slot(slug: str, data: SlotIn, actor: Actor = Depends(require_user)) -> Message:
    service.set_slot(actor, load_managed(actor, slug), data.model_dump())
    return Message(message="Presentation slot added.")


@router.delete("/competitions/{slug}/presentations/{slot_id}", response_model=Message)
def delete_slot(slug: str, slot_id: uuid.UUID, actor: Actor = Depends(require_user)) -> Message:
    service.delete_slot(actor, load_managed(actor, slug), slot_id)
    return Message(message="Slot removed.")


@router.get("/judge/events")
def my_judging(actor: Actor = Depends(require_user)) -> list[dict[str, Any]]:
    return service.judge_events(actor)
