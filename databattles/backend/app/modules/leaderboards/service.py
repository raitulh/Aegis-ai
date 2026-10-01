"""Leaderboards and result finalization.

Ranking rules (leaderboard rules version 1 — shown to participants):
1. Live (public) leaderboard: each team's best *public* score among valid scored submissions.
2. Final (private) leaderboard: for each team, candidate submissions are the ones the team selected as
   final; if none were selected, the team's top-N by public score (N = final selection limit).
   The team's final score is the best *private* score among its candidates.
3. Ties are broken by the earlier submission time, then by team id (deterministic).
4. Finalization freezes a snapshot. Corrections create a new snapshot version with a recorded reason;
   earlier snapshots are never modified.
"""

from __future__ import annotations

import csv
import io
import uuid
from typing import Any

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import Session

from app.core.audit import record_audit
from app.core.deps import Actor
from app.core.errors import Conflict, Forbidden
from app.core.permissions import can_manage_competition
from app.core.schemas import user_mini
from app.core.time import utcnow
from app.evaluation.registry import EVALUATORS, metric_direction
from app.models.competition import Competition, CompetitionParticipant, Team, TeamMember
from app.models.enums import (
    PENDING_SUBMISSION_STATUSES,
    LeaderboardVisibility,
    Lifecycle,
    NotificationKind,
    ScoringMode,
    SubmissionStatus,
)
from app.models.org import Organization
from app.models.submission import CompetitionResult, LeaderboardSnapshot, Submission
from app.models.user import User
from app.modules.competitions import state
from app.modules.notifications.service import notify

RULES_VERSION = 1


def _direction_sql(direction: str) -> str:
    return "DESC" if direction == "maximize" else "ASC"


def live_ranking(db: Session, comp: Competition) -> list[dict[str, Any]]:
    """Best public score per team, ranked in the database with deterministic tie-breaking."""
    order = _direction_sql(metric_direction(comp.evaluation or {}))
    sql = text(f"""
        WITH best AS (
            SELECT s.team_id, s.id AS submission_id, s.public_score, s.submitted_at,
                   row_number() OVER (PARTITION BY s.team_id ORDER BY s.public_score {order}, s.submitted_at ASC, s.id ASC) AS rn
            FROM submissions s
            WHERE s.competition_id = :cid AND s.status = 'scored' AND s.invalidated_at IS NULL AND s.public_score IS NOT NULL
        ), entries AS (
            SELECT team_id, count(*) AS n FROM submissions
            WHERE competition_id = :cid AND status NOT IN ('rejected', 'failed', 'canceled') GROUP BY team_id
        )
        SELECT row_number() OVER (ORDER BY b.public_score {order}, b.submitted_at ASC, b.team_id ASC) AS rank,
               b.team_id, b.submission_id, b.public_score, b.submitted_at, coalesce(e.n, 0) AS entries
        FROM best b LEFT JOIN entries e ON e.team_id = b.team_id
        WHERE b.rn = 1
        ORDER BY rank
    """)  # noqa: S608 — `order` is a whitelisted literal, all values are bound parameters
    return [dict(r._mapping) for r in db.execute(sql, {"cid": comp.id})]


def _team_details(db: Session, comp: Competition, team_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[str, Any]]:
    if not team_ids:
        return {}
    teams = {t.id: t for t in db.scalars(select(Team).where(Team.id.in_(team_ids)))}
    rows = db.execute(select(TeamMember.team_id, User, Organization)
                      .join(User, User.id == TeamMember.user_id)
                      .outerjoin(Organization, Organization.id == User.university_id)
                      .where(TeamMember.team_id.in_(team_ids)).order_by(TeamMember.joined_at)).all()
    members: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for team_id, user, org in rows:
        show_uni = comp.show_university_on_leaderboard and user.privacy_flag("show_university_on_leaderboards") and org is not None
        members.setdefault(team_id, []).append({
            **user_mini(user).model_dump(mode="json"),  # type: ignore[union-attr]
            "university": org.name if show_uni else None,
        })
    return {tid: {"team_name": teams[tid].name if tid in teams else "Deleted team", "is_solo": teams[tid].is_solo if tid in teams else False,
                  "members": members.get(tid, [])} for tid in team_ids}


def viewer_team_id(actor: Actor, comp: Competition) -> uuid.UUID | None:
    if not actor.is_authenticated:
        return None
    return actor.db.scalar(select(TeamMember.team_id).where(TeamMember.competition_id == comp.id, TeamMember.user_id == actor.id))


def current_snapshot(db: Session, comp: Competition) -> LeaderboardSnapshot | None:
    return db.scalar(select(LeaderboardSnapshot).where(LeaderboardSnapshot.competition_id == comp.id,
                                                       LeaderboardSnapshot.is_current.is_(True)))


def leaderboard(actor: Actor, comp: Competition, *, page: int, page_size: int, around_me: bool) -> dict[str, Any]:
    db = actor.db
    manager = can_manage_competition(actor, comp)
    my_team = viewer_team_id(actor, comp)
    cfg = comp.evaluation or {}
    snapshot = current_snapshot(db, comp) if comp.lifecycle in (Lifecycle.finalized, Lifecycle.archived) else None
    base: dict[str, Any] = {
        "metric": cfg.get("metric") if comp.scoring_mode == ScoringMode.automatic else "judged_score",
        "direction": metric_direction(cfg) if comp.scoring_mode == ScoringMode.automatic else "maximize",
        "rules_version": snapshot.rules_version if snapshot else RULES_VERSION,
        "is_final": snapshot is not None,
        "snapshot_version": snapshot.version if snapshot else None,
        "finalized_at": comp.finalized_at,
        "visibility": comp.leaderboard_visibility,
        "evaluator_version": snapshot.evaluator_version if snapshot else EVALUATORS.get(cfg.get("evaluator", "csv_prediction"),
                                                                                        EVALUATORS["csv_prediction"]).version,
    }
    if snapshot is not None:
        all_rows = [dict(r) for r in snapshot.rows]
        for r in all_rows:
            r["team_id"] = uuid.UUID(r["team_id"])
    else:
        if comp.scoring_mode != ScoringMode.automatic:
            return {**base, "rows": [], "total": 0, "page": page, "page_size": page_size, "viewer_row": None,
                    "hidden_reason": "Results will be published after judging."}
        live = live_ranking(db, comp)
        all_rows = [{"rank": r["rank"], "team_id": r["team_id"], "score": r["public_score"], "submission_id": str(r["submission_id"]),
                     "submitted_at": r["submitted_at"].isoformat(), "entries": r["entries"], "label": None} for r in live]
    total = len(all_rows)
    viewer_row = next((r for r in all_rows if r["team_id"] == my_team), None)
    hidden_reason = None
    visibility = comp.leaderboard_visibility if not manager and snapshot is None else LeaderboardVisibility.visible
    if visibility == LeaderboardVisibility.hidden:
        page_rows: list[dict[str, Any]] = []
        hidden_reason = "The organizers have hidden the leaderboard until results are final."
    else:
        if around_me and viewer_row is not None:
            start = max(0, viewer_row["rank"] - 1 - page_size // 2)
        else:
            start = (page - 1) * page_size
        page_rows = all_rows[start:start + page_size]
    details = _team_details(db, comp, [r["team_id"] for r in page_rows] + ([my_team] if viewer_row and my_team else []))
    out_rows = []
    for r in page_rows:
        row = {**r, **details.get(r["team_id"], {}), "team_id": str(r["team_id"]), "is_viewer": r["team_id"] == my_team}
        if visibility == LeaderboardVisibility.ranks_only:
            row["score"] = None
        out_rows.append(row)
    vrow = None
    if viewer_row is not None:
        vrow = {**viewer_row, **details.get(viewer_row["team_id"], {}), "team_id": str(viewer_row["team_id"]), "is_viewer": True}
        if visibility == LeaderboardVisibility.hidden:
            vrow["rank"] = None
    return {**base, "rows": out_rows, "total": total, "page": page, "page_size": page_size, "viewer_row": vrow,
            "hidden_reason": hidden_reason}


# ----------------------------------------------------------------------------- final results


def result_label(rank: int | None, total: int) -> str:
    if rank is None:
        return "Participant"
    if rank == 1:
        return "Winner"
    if rank <= 3 and total > 3:
        return "Top 3"
    if rank <= 10 and total > 10:
        return "Top 10"
    return "Participant"


def compute_final_rows(db: Session, comp: Competition) -> list[dict[str, Any]]:
    direction = metric_direction(comp.evaluation or {})
    maximize = direction == "maximize"
    subs = db.scalars(select(Submission).where(
        Submission.competition_id == comp.id, Submission.status == SubmissionStatus.scored,
        Submission.invalidated_at.is_(None), Submission.public_score.is_not(None))).all()
    by_team: dict[uuid.UUID, list[Submission]] = {}
    for s in subs:
        by_team.setdefault(s.team_id, []).append(s)

    def public_key(s: Submission) -> tuple[float, Any, str]:
        return (-(s.public_score or 0.0) if maximize else (s.public_score or 0.0), s.submitted_at, str(s.id))

    def private_key(s: Submission) -> tuple[float, Any, str]:
        score = s.private_score if s.private_score is not None else s.public_score
        return (-(score or 0.0) if maximize else (score or 0.0), s.submitted_at, str(s.id))

    finals: list[tuple[uuid.UUID, Submission]] = []
    for team_id, team_subs in by_team.items():
        selected = [s for s in team_subs if s.is_final_selected]
        candidates = selected or sorted(team_subs, key=public_key)[: max(1, comp.final_selection_limit)]
        finals.append((team_id, sorted(candidates, key=private_key)[0]))
    finals.sort(key=lambda ts: (*private_key(ts[1])[:2], str(ts[0])))
    total = len(finals)
    details = _team_details(db, comp, [t for t, _ in finals])
    rows = []
    for idx, (team_id, s) in enumerate(finals, start=1):
        d = details.get(team_id, {})
        rows.append({
            "rank": idx, "team_id": str(team_id), "team_name": d.get("team_name"), "is_solo": d.get("is_solo", False),
            "members": d.get("members", []),
            "score": s.private_score if s.private_score is not None else s.public_score,
            "public_score": s.public_score, "submission_id": str(s.id), "submitted_at": s.submitted_at.isoformat(),
            "label": result_label(idx, total),
        })
    return rows


def _write_results(db: Session, comp: Competition, snapshot: LeaderboardSnapshot) -> None:
    db.execute(delete(CompetitionResult).where(CompetitionResult.competition_id == comp.id))
    ranked_members: set[uuid.UUID] = set()
    total = len(snapshot.rows)
    for row in snapshot.rows:
        for m in row.get("members", []):
            uid = uuid.UUID(m["id"])
            ranked_members.add(uid)
            db.add(CompetitionResult(competition_id=comp.id, user_id=uid, team_id=uuid.UUID(row["team_id"]), rank=row["rank"],
                                     total_ranked=total, label=row["label"], score=row.get("score"),
                                     snapshot_version=snapshot.version))
    # Participants without a valid ranked entry are recorded as participants only if they submitted something valid.
    db.flush()


def _snapshot_rows(db: Session, comp: Competition) -> tuple[list[dict[str, Any]], str]:
    if comp.scoring_mode == ScoringMode.judged:
        from app.modules.judging.service import aggregate_results

        return aggregate_results(db, comp), "judging"
    return compute_final_rows(db, comp), "automatic"


def finalize(actor: Actor, comp: Competition) -> LeaderboardSnapshot:
    db = actor.db
    if not can_manage_competition(actor, comp):
        raise Forbidden()
    if comp.lifecycle != Lifecycle.published:
        raise Conflict("Only published competitions can be finalized.", code="invalid_transition")
    if comp.ends_at and utcnow() < comp.ends_at and not actor.is_admin:
        raise Conflict("Results can be finalized after the competition ends.", code="competition_not_ended")
    pending = db.scalar(select(func.count()).select_from(Submission).where(
        Submission.competition_id == comp.id, Submission.status.in_(PENDING_SUBMISSION_STATUSES))) or 0
    if pending:
        raise Conflict(f"{pending} submission(s) are still being scored. Try again shortly.", code="pending_submissions")
    rows, source = _snapshot_rows(db, comp)
    cfg = comp.evaluation or {}
    snapshot = LeaderboardSnapshot(
        competition_id=comp.id, version=1, kind="final", source=source, rows=rows, rules_version=RULES_VERSION,
        evaluator_version=EVALUATORS[cfg.get("evaluator", "csv_prediction")].version if source == "automatic" else None,
        metric=cfg.get("metric") if source == "automatic" else "judged_score",
        direction=metric_direction(cfg) if source == "automatic" else "maximize",
        is_current=True, created_by=actor.id,
    )
    db.add(snapshot)
    state.transition(comp, Lifecycle.finalized)
    if source == "judging":
        from app.modules.judging.service import get_rubric

        rubric = get_rubric(db, comp)
        if rubric is not None and rubric.finalized_at is None:
            rubric.finalized_at = utcnow()
    comp.finalized_at = utcnow()
    comp.finalized_by = actor.id
    db.flush()
    _write_results(db, comp, snapshot)
    record_audit(db, actor.id, "competition.finalize", target_type="competition", target_id=comp.id, competition_id=comp.id,
                 meta={"teams_ranked": len(rows), "rules_version": RULES_VERSION})
    participants = db.scalars(select(CompetitionParticipant.user_id).where(CompetitionParticipant.competition_id == comp.id)).all()
    for uid in participants:
        notify(db, uid, NotificationKind.results_published, f"Final results are published for {comp.title}",
               link=f"/competitions/{comp.slug}/leaderboard", dedupe_key=f"results:{comp.id}:1",
               email_template="results_published", email_context={"competition": comp.title})
    from app.modules.credentials.badges import evaluate_user_badges

    for row in rows:
        for m in row.get("members", []):
            evaluate_user_badges(db, uuid.UUID(m["id"]), trigger="competition_finalized")
    from app.modules.search.indexer import index_competition

    index_competition(db, comp)
    db.commit()
    return snapshot


def correct_results(actor: Actor, comp: Competition, reason: str) -> LeaderboardSnapshot:
    """Authorized correction flow: recompute from current data (e.g. after invalidating a submission)."""
    db = actor.db
    if not (can_manage_competition(actor, comp) or actor.is_admin):
        raise Forbidden()
    if comp.lifecycle != Lifecycle.finalized:
        raise Conflict("Corrections apply only to finalized results.", code="not_finalized")
    current = current_snapshot(db, comp)
    assert current is not None
    rows, source = _snapshot_rows(db, comp)
    db.execute(update(LeaderboardSnapshot).where(LeaderboardSnapshot.competition_id == comp.id).values(is_current=False))
    snapshot = LeaderboardSnapshot(
        competition_id=comp.id, version=current.version + 1, kind="correction", source=source, rows=rows,
        rules_version=RULES_VERSION, evaluator_version=current.evaluator_version, metric=current.metric,
        direction=current.direction, is_current=True, reason=reason, created_by=actor.id,
    )
    db.add(snapshot)
    db.flush()
    _write_results(db, comp, snapshot)
    record_audit(db, actor.id, "competition.results_corrected", target_type="competition", target_id=comp.id,
                 competition_id=comp.id, reason=reason, meta={"version": snapshot.version})
    for uid in db.scalars(select(CompetitionParticipant.user_id).where(CompetitionParticipant.competition_id == comp.id)):
        notify(db, uid, NotificationKind.results_published, f"Results for {comp.title} were corrected",
               body=reason[:200], link=f"/competitions/{comp.slug}/leaderboard", dedupe_key=f"results:{comp.id}:{snapshot.version}")
    db.commit()
    return snapshot


def snapshot_history(db: Session, comp: Competition) -> list[LeaderboardSnapshot]:
    return list(db.scalars(select(LeaderboardSnapshot).where(LeaderboardSnapshot.competition_id == comp.id)
                           .order_by(LeaderboardSnapshot.version.desc())))


def export_csv(actor: Actor, comp: Competition) -> str:
    db = actor.db
    if not can_manage_competition(actor, comp):
        raise Forbidden()
    snapshot = current_snapshot(db, comp)
    rows = [dict(r) for r in snapshot.rows] if snapshot else compute_final_rows(db, comp)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["rank", "team", "members", "final_score", "public_score", "submission_id", "submitted_at_utc", "label"])
    for r in rows:
        members = "; ".join(f"{m['display_name']} (@{m['handle']})" for m in r.get("members", []))
        writer.writerow([r["rank"], r.get("team_name"), members, r.get("score"), r.get("public_score"),
                         r.get("submission_id"), r.get("submitted_at"), r.get("label")])
    return buf.getvalue()


def team_score_history(db: Session, team_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = db.execute(select(Submission.id, Submission.submitted_at, Submission.public_score)
                      .where(Submission.team_id == team_id, Submission.status == SubmissionStatus.scored,
                             Submission.invalidated_at.is_(None))
                      .order_by(Submission.submitted_at)).all()
    return [{"submission_id": str(r[0]), "submitted_at": r[1].isoformat(), "score": r[2]} for r in rows]


def team_rank(db: Session, comp: Competition, team_id: uuid.UUID) -> tuple[int | None, int]:
    live = live_ranking(db, comp)
    for r in live:
        if r["team_id"] == team_id:
            return r["rank"], len(live)
    return None, len(live)

