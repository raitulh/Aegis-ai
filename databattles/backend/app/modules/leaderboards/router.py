from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import Field

from app.core.deps import Actor, get_actor, require_user
from app.core.errors import Forbidden
from app.core.permissions import can_manage_competition
from app.core.schemas import Schema
from app.modules.competitions.service import build_detail, load_managed, load_visible
from app.modules.leaderboards import service

router = APIRouter(tags=["leaderboards"])


class LeaderboardMember(Schema):
    id: uuid.UUID
    handle: str
    display_name: str
    avatar_url: str | None = None
    university: str | None = None


class LeaderboardRow(Schema):
    rank: int | None
    team_id: str
    team_name: str | None = None
    is_solo: bool = False
    members: list[LeaderboardMember] = []
    score: float | None = None
    public_score: float | None = None
    submission_id: str | None = None
    submitted_at: str | None = None
    entries: int | None = None
    judge_count: int | None = None
    label: str | None = None
    is_viewer: bool = False


class LeaderboardOut(Schema):
    metric: str | None
    direction: str
    rules_version: int
    evaluator_version: str | None
    is_final: bool
    snapshot_version: int | None
    finalized_at: datetime | None
    visibility: str
    rows: list[LeaderboardRow]
    total: int
    page: int
    page_size: int
    viewer_row: LeaderboardRow | None
    hidden_reason: str | None


class SnapshotOut(Schema):
    version: int
    kind: str
    source: str
    rules_version: int
    evaluator_version: str | None
    reason: str | None
    is_current: bool
    created_at: datetime
    team_count: int


class CorrectionIn(Schema):
    reason: str = Field(min_length=5, max_length=500)


@router.get("/competitions/{slug}/leaderboard", response_model=LeaderboardOut)
def leaderboard(slug: str, page: int = Query(1, ge=1, le=10_000), page_size: int = Query(50, ge=1, le=200),
                around_me: bool = Query(False), actor: Actor = Depends(get_actor)) -> dict[str, Any]:
    comp = load_visible(actor, slug)
    return service.leaderboard(actor, comp, page=page, page_size=page_size, around_me=around_me)


@router.get("/competitions/{slug}/leaderboard/private-preview", response_model=list[LeaderboardRow],
            summary="Organizer-only preview of the final (private) ranking before finalization")
def private_preview(slug: str, actor: Actor = Depends(require_user)) -> list[dict[str, Any]]:
    comp = load_managed(actor, slug)
    if comp.scoring_mode == "judged":
        from app.modules.judging.service import aggregate_results

        return aggregate_results(actor.db, comp)
    return service.compute_final_rows(actor.db, comp)


@router.post("/competitions/{slug}/finalize")
def finalize(slug: str, actor: Actor = Depends(require_user)) -> Any:
    comp = load_managed(actor, slug)
    service.finalize(actor, comp)
    return build_detail(actor, comp)


@router.post("/competitions/{slug}/results/corrections", response_model=SnapshotOut)
def correct(slug: str, data: CorrectionIn, actor: Actor = Depends(require_user)) -> SnapshotOut:
    comp = load_visible(actor, slug)
    snap = service.correct_results(actor, comp, data.reason)
    return SnapshotOut(version=snap.version, kind=snap.kind, source=snap.source, rules_version=snap.rules_version,
                       evaluator_version=snap.evaluator_version, reason=snap.reason, is_current=snap.is_current,
                       created_at=snap.created_at, team_count=len(snap.rows))


@router.get("/competitions/{slug}/results/history", response_model=list[SnapshotOut])
def history(slug: str, actor: Actor = Depends(get_actor)) -> list[SnapshotOut]:
    comp = load_visible(actor, slug)
    return [SnapshotOut(version=s.version, kind=s.kind, source=s.source, rules_version=s.rules_version,
                        evaluator_version=s.evaluator_version, reason=s.reason, is_current=s.is_current, created_at=s.created_at,
                        team_count=len(s.rows)) for s in service.snapshot_history(actor.db, comp)]


@router.get("/competitions/{slug}/results.csv", response_class=Response)
def export(slug: str, actor: Actor = Depends(require_user)) -> Response:
    comp = load_visible(actor, slug)
    if not can_manage_competition(actor, comp):
        raise Forbidden()
    return Response(service.export_csv(actor, comp), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{comp.slug}-results.csv"'})
