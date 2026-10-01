from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Header, Query, Response, UploadFile
from pydantic import Field
from sqlalchemy import select

from app.core.deps import Actor, require_user
from app.core.errors import NotFound
from app.core.pagination import Page, PageParams, make_page
from app.core.schemas import Schema, UserMini, user_mini
from app.models.submission import Submission
from app.models.user import User
from app.modules.competitions.service import load_visible
from app.modules.submissions import service
from app.modules.teams.service import team_for_user

router = APIRouter(tags=["submissions"])


class SubmissionOut(Schema):
    id: uuid.UUID
    status: str
    filename: str
    description: str | None
    size_bytes: int
    row_count: int | None
    submitted_at: datetime
    completed_at: datetime | None
    public_score: float | None
    private_score: float | None = None  # organizers only, or everyone after finalization
    secondary_scores: dict[str, Any]
    error_code: str | None
    error_message: str | None
    error_details: list[dict[str, Any]]
    is_final_selected: bool
    invalidated: bool
    invalidation_reason: str | None
    submitter: UserMini | None
    team_id: uuid.UUID
    team_name: str | None = None
    sha256_prefix: str
    evaluator_version: str | None
    config_version: int | None


class SelectIn(Schema):
    selected: bool


class InvalidateIn(Schema):
    reason: str = Field(min_length=3, max_length=500)


def to_out(sub: Submission, submitter: User | None, *, reveal_private: bool, team_name: str | None = None) -> SubmissionOut:
    secondary = {k: ({"public": v.get("public"), "private": v.get("private")} if reveal_private else {"public": v.get("public")})
                 for k, v in (sub.secondary_scores or {}).items()}
    return SubmissionOut(
        id=sub.id, status=sub.status, filename=sub.filename, description=sub.description, size_bytes=sub.size_bytes,
        row_count=sub.row_count, submitted_at=sub.submitted_at, completed_at=sub.completed_at, public_score=sub.public_score,
        private_score=sub.private_score if reveal_private else None, secondary_scores=secondary, error_code=sub.error_code,
        error_message=sub.error_message, error_details=list(sub.error_details or []), is_final_selected=sub.is_final_selected,
        invalidated=sub.invalidated_at is not None, invalidation_reason=sub.invalidation_reason, submitter=user_mini(submitter),
        team_id=sub.team_id, team_name=team_name, sha256_prefix=sub.sha256[:12], evaluator_version=sub.evaluator_version,
        config_version=sub.config_version,
    )


@router.post("/competitions/{slug}/submissions", response_model=SubmissionOut, status_code=202)
def create_submission(
    slug: str,
    response: Response,
    file: UploadFile = File(...),
    description: str | None = Form(None, max_length=500),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key", max_length=80),
    actor: Actor = Depends(require_user),
) -> SubmissionOut:
    comp = load_visible(actor, slug)
    sub, created = service.create_submission(actor, comp, filename=file.filename or "", stream=file.file,
                                             description=description, idempotency_key=idempotency_key)
    if not created:
        response.status_code = 200
    return to_out(sub, actor.user, reveal_private=False)


@router.get("/competitions/{slug}/submissions", response_model=Page[SubmissionOut])
def my_team_submissions(slug: str, params: PageParams = Depends(), actor: Actor = Depends(require_user)) -> dict:
    comp = load_visible(actor, slug)
    team = team_for_user(actor.db, comp.id, actor.id)
    if team is None:
        return make_page([], 0, params)
    rows, total = service.list_team_submissions(actor, comp, team, params.page_size, params.offset)
    users = {u.id: u for u in actor.db.scalars(select(User).where(User.id.in_([r.user_id for r in rows if r.user_id])))}
    final = comp.lifecycle in ("finalized", "archived")
    return make_page([to_out(r, users.get(r.user_id), reveal_private=final) for r in rows], total, params)


@router.get("/submissions/{submission_id}", response_model=SubmissionOut)
def get_submission(submission_id: uuid.UUID, actor: Actor = Depends(require_user)) -> SubmissionOut:
    sub, comp, manager = service.get_for_viewer(actor, submission_id)
    submitter = actor.db.get(User, sub.user_id) if sub.user_id else None
    return to_out(sub, submitter, reveal_private=manager or comp.lifecycle in ("finalized", "archived"))


@router.post("/submissions/{submission_id}/cancel", response_model=SubmissionOut)
def cancel(submission_id: uuid.UUID, actor: Actor = Depends(require_user)) -> SubmissionOut:
    sub = service.cancel(actor, submission_id)
    return to_out(sub, actor.db.get(User, sub.user_id) if sub.user_id else None, reveal_private=False)


@router.post("/competitions/{slug}/submissions/{submission_id}/select", response_model=SubmissionOut)
def select_final(slug: str, submission_id: uuid.UUID, data: SelectIn, actor: Actor = Depends(require_user)) -> SubmissionOut:
    comp = load_visible(actor, slug)
    sub = service.select_final(actor, comp, submission_id, data.selected)
    return to_out(sub, actor.db.get(User, sub.user_id) if sub.user_id else None, reveal_private=False)


@router.get("/competitions/{slug}/submissions/all", response_model=Page[SubmissionOut], summary="Organizer view of all submissions")
def all_submissions(slug: str, params: PageParams = Depends(), status: str | None = Query(None, max_length=16),
                    actor: Actor = Depends(require_user)) -> dict:
    comp = load_visible(actor, slug)
    rows, total = service.organizer_list(actor, comp, status, params.page_size, params.offset)
    users = {u.id: u for u in actor.db.scalars(select(User).where(User.id.in_([s.user_id for s, _ in rows if s.user_id])))}
    return make_page([to_out(s, users.get(s.user_id), reveal_private=True, team_name=t.name) for s, t in rows], total, params)


@router.post("/competitions/{slug}/submissions/{submission_id}/invalidate", response_model=SubmissionOut)
def invalidate(slug: str, submission_id: uuid.UUID, data: InvalidateIn, actor: Actor = Depends(require_user)) -> SubmissionOut:
    comp = load_visible(actor, slug)
    sub = service.invalidate(actor, comp, submission_id, data.reason)
    if sub is None:
        raise NotFound()
    return to_out(sub, actor.db.get(User, sub.user_id) if sub.user_id else None, reveal_private=True)
