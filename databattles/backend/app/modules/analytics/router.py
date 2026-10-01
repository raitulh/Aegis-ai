"""Organizer analytics for a single competition. Aggregates only; small groups are suppressed."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from app.core.deps import Actor, require_user
from app.core.time import utcnow
from app.evaluation.registry import metric_direction
from app.models.competition import Competition, CompetitionParticipant, Team, TeamMember
from app.models.enums import SubmissionStatus
from app.models.org import Organization
from app.models.submission import Submission
from app.models.user import User
from app.modules.competitions.service import load_managed

router = APIRouter(tags=["analytics"])

MIN_GROUP = 3  # suppress breakdown buckets smaller than this to avoid identifying individuals


def _histogram(values: list[float], bins: int = 12) -> list[dict[str, float]]:
    if not values:
        return []
    lo, hi = min(values), max(values)
    if lo == hi:
        return [{"start": lo, "end": hi, "count": len(values)}]
    width = (hi - lo) / bins
    counts = [0] * bins
    for v in values:
        counts[min(bins - 1, int((v - lo) / width))] += 1
    return [{"start": round(lo + i * width, 6), "end": round(lo + (i + 1) * width, 6), "count": c} for i, c in enumerate(counts)]


@router.get("/competitions/{slug}/analytics")
def competition_analytics(slug: str, actor: Actor = Depends(require_user)) -> dict[str, Any]:
    comp: Competition = load_managed(actor, slug)
    db = actor.db
    now = utcnow()
    since = max(comp.published_at or comp.created_at, now - timedelta(days=120))
    joins = db.execute(select(func.date_trunc("day", CompetitionParticipant.joined_at).label("d"), func.count())
                       .where(CompetitionParticipant.competition_id == comp.id, CompetitionParticipant.joined_at >= since)
                       .group_by("d").order_by("d")).all()
    subs = db.execute(select(func.date_trunc("day", Submission.submitted_at).label("d"), func.count())
                      .where(Submission.competition_id == comp.id, Submission.submitted_at >= since)
                      .group_by("d").order_by("d")).all()
    status_counts = dict(db.execute(select(Submission.status, func.count()).where(Submission.competition_id == comp.id)
                                    .group_by(Submission.status)).all())
    participants = comp.participant_count
    teams_with_scored = db.scalar(select(func.count(func.distinct(Submission.team_id))).where(
        Submission.competition_id == comp.id, Submission.status == SubmissionStatus.scored)) or 0
    submitters = db.scalar(select(func.count(func.distinct(TeamMember.user_id))).where(
        TeamMember.competition_id == comp.id,
        TeamMember.team_id.in_(select(Submission.team_id).where(Submission.competition_id == comp.id)))) or 0
    sizes = db.execute(select(func.count(TeamMember.id)).join(Team, Team.id == TeamMember.team_id)
                       .where(Team.competition_id == comp.id).group_by(Team.id)).scalars().all()
    size_dist: dict[int, int] = {}
    for s in sizes:
        size_dist[s] = size_dist.get(s, 0) + 1
    # Public-score distribution of each team's best submission (organizers only; private scores never included).
    direction = metric_direction(comp.evaluation or {})
    agg = func.max if direction == "maximize" else func.min
    best = [v for v in db.scalars(select(agg(Submission.public_score)).where(
        Submission.competition_id == comp.id, Submission.status == SubmissionStatus.scored, Submission.invalidated_at.is_(None))
        .group_by(Submission.team_id)) if v is not None]
    unis = db.execute(select(Organization.name, func.count()).join(User, User.university_id == Organization.id)
                      .join(CompetitionParticipant, CompetitionParticipant.user_id == User.id)
                      .where(CompetitionParticipant.competition_id == comp.id).group_by(Organization.name)
                      .order_by(func.count().desc())).all()
    shown = [(n, c) for n, c in unis if c >= MIN_GROUP]
    other = sum(c for n, c in unis if c < MIN_GROUP)
    errors = db.execute(select(Submission.error_code, func.count()).where(
        Submission.competition_id == comp.id, Submission.status.in_([SubmissionStatus.rejected, SubmissionStatus.failed]))
        .group_by(Submission.error_code).order_by(func.count().desc()).limit(8)).all()
    return {
        "competition": {"slug": comp.slug, "title": comp.title, "metric": (comp.evaluation or {}).get("metric"), "direction": direction},
        "funnel": {"views": comp.view_count, "participants": participants, "members_who_submitted": submitters,
                   "teams": comp.team_count, "teams_with_scored_submission": teams_with_scored},
        "joins_by_day": [{"day": d.date().isoformat(), "count": n} for d, n in joins],
        "submissions_by_day": [{"day": d.date().isoformat(), "count": n} for d, n in subs],
        "submission_status": status_counts,
        "team_sizes": [{"size": k, "teams": v} for k, v in sorted(size_dist.items())],
        "score_histogram": _histogram(best),
        "universities": [{"name": n, "participants": c} for n, c in shown] + ([{"name": "Other (small groups)", "participants": other}] if other else []),
        "top_errors": [{"code": code or "unknown", "count": n} for code, n in errors],
        "privacy_note": f"Breakdowns with fewer than {MIN_GROUP} people are grouped to protect participant privacy.",
    }
