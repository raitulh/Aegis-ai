"""Human judging for hackathons, demo days and research challenges.

* Rubric criteria have ranges and weights; weighted totals are computed server-side on a 0–100 scale.
* Judges see only assigned entries, never entries they have a conflict with, and never other judges'
  scores unless the organizer enables it.
* Scores lock when judging is finalized; all changes are audited.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import record_audit
from app.core.deps import Actor
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.core.markdown import render_markdown
from app.core.permissions import assert_manage_competition, can_manage_competition, is_judge
from app.core.schemas import user_mini
from app.core.time import utcnow
from app.core.validators import validate_external_url
from app.models.competition import Competition, CompetitionStaff, Team, TeamMember
from app.models.enums import JudgeScoreStatus, NotificationKind, ScoringMode, StaffRole
from app.models.judging import JudgeAssignment, JudgeConflict, JudgeScore, PresentationSlot, Rubric
from app.models.submission import EventSubmission
from app.models.user import User
from app.modules.competitions import state
from app.modules.notifications.service import notify
from app.modules.teams.service import team_for_user

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")


def get_rubric(db: Session, comp: Competition) -> Rubric | None:
    return db.scalar(select(Rubric).where(Rubric.competition_id == comp.id))


def _validate_criteria(criteria: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not 1 <= len(criteria) <= 12:
        raise ValidationFailed(details={"fields": {"criteria": "Use between 1 and 12 criteria."}})
    seen: set[str] = set()
    out = []
    for c in criteria:
        key = str(c.get("key", "")).strip().lower()
        if not _KEY_RE.match(key) or key in seen:
            raise ValidationFailed(details={"fields": {"criteria": f"Invalid or duplicate key '{key}'."}})
        seen.add(key)
        lo, hi, weight = float(c.get("min", 0)), float(c.get("max", 10)), float(c.get("weight", 1))
        if hi <= lo or weight <= 0:
            raise ValidationFailed(details={"fields": {"criteria": f"'{key}': max must exceed min and weight must be positive."}})
        out.append({"key": key, "label": str(c.get("label") or key)[:80], "description": str(c.get("description") or "")[:300],
                    "min": lo, "max": hi, "weight": weight, "allow_decimal": bool(c.get("allow_decimal", False))})
    return out


def upsert_rubric(actor: Actor, comp: Competition, criteria: list[dict[str, Any]] | None, reveal: bool | None,
                  blind: bool | None) -> Rubric:
    db = actor.db
    assert_manage_competition(actor, comp)
    rubric = get_rubric(db, comp)
    if rubric is None:
        rubric = Rubric(competition_id=comp.id, criteria=[], version=0)
        db.add(rubric)
    if rubric.finalized_at:
        raise Conflict("Judging is finalized.", code="judging_finalized")
    if criteria is not None:
        if rubric.locked_at:
            raise Conflict("The rubric is locked because judges have submitted scores.", code="rubric_locked")
        rubric.criteria = _validate_criteria(criteria)
        rubric.version += 1
    if reveal is not None:
        rubric.reveal_scores_to_judges = reveal
    if blind is not None:
        rubric.blind_judging = blind
    record_audit(db, actor.id, "judging.rubric_update", target_type="competition", target_id=comp.id, competition_id=comp.id,
                 meta={"version": rubric.version})
    db.commit()
    return rubric


def assign_judge(actor: Actor, comp: Competition, handle: str, team_id: uuid.UUID | None, panel: str | None) -> JudgeAssignment:
    db = actor.db
    assert_manage_competition(actor, comp)
    user = db.scalar(select(User).where(User.handle == handle.lower().lstrip("@")))
    if user is None:
        raise NotFound("No user with that handle.")
    if team_for_user(db, comp.id, user.id) is not None:
        raise Conflict("Participants cannot judge the same event.", code="conflict_of_interest")
    if team_id is not None:
        team = db.get(Team, team_id)
        if team is None or team.competition_id != comp.id:
            raise ValidationFailed(details={"fields": {"team_id": "Unknown team."}})
    if not db.scalar(select(CompetitionStaff.id).where(CompetitionStaff.competition_id == comp.id,
                                                       CompetitionStaff.user_id == user.id, CompetitionStaff.role == StaffRole.judge)):
        db.add(CompetitionStaff(competition_id=comp.id, user_id=user.id, role=StaffRole.judge, added_by=actor.id))
    existing = db.scalar(select(JudgeAssignment).where(JudgeAssignment.competition_id == comp.id, JudgeAssignment.judge_id == user.id,
                                                       JudgeAssignment.team_id.is_(team_id) if team_id is None else JudgeAssignment.team_id == team_id))
    if existing:
        return existing
    assignment = JudgeAssignment(competition_id=comp.id, judge_id=user.id, team_id=team_id, panel=panel, created_by=actor.id)
    db.add(assignment)
    record_audit(db, actor.id, "judging.assign", target_type="user", target_id=user.id, competition_id=comp.id,
                 meta={"team_id": str(team_id) if team_id else "all", "panel": panel})
    notify(db, user.id, NotificationKind.judging, f"New judging assignment: {comp.title}", link=f"/judge/{comp.slug}",
           dedupe_key=f"judge_assign:{comp.id}:{team_id or 'all'}")
    db.commit()
    return assignment


def remove_assignment(actor: Actor, comp: Competition, assignment_id: uuid.UUID) -> None:
    db = actor.db
    assert_manage_competition(actor, comp)
    a = db.get(JudgeAssignment, assignment_id)
    if a is None or a.competition_id != comp.id:
        raise NotFound()
    db.delete(a)
    record_audit(db, actor.id, "judging.unassign", target_type="user", target_id=a.judge_id, competition_id=comp.id)
    db.commit()


def declare_conflict(actor: Actor, comp: Competition, judge_id: uuid.UUID, team_id: uuid.UUID, reason: str) -> JudgeConflict:
    """Organizers can record conflicts; judges can recuse themselves."""
    db = actor.db
    if not (can_manage_competition(actor, comp) or (judge_id == actor.id and is_judge(actor, comp))):
        raise Forbidden()
    team = db.get(Team, team_id)
    if team is None or team.competition_id != comp.id:
        raise NotFound("Unknown team.")
    existing = db.scalar(select(JudgeConflict).where(JudgeConflict.judge_id == judge_id, JudgeConflict.team_id == team_id))
    if existing:
        return existing
    conflict = JudgeConflict(competition_id=comp.id, judge_id=judge_id, team_id=team_id, reason=reason[:300], declared_by=actor.id)
    db.add(conflict)
    # A draft score from a conflicted judge is discarded; submitted scores are excluded from aggregation.
    for score in db.scalars(select(JudgeScore).where(JudgeScore.judge_id == judge_id, JudgeScore.team_id == team_id)):
        db.delete(score)
    record_audit(db, actor.id, "judging.conflict", target_type="team", target_id=team_id, competition_id=comp.id,
                 reason=reason, meta={"judge_id": str(judge_id), "self": judge_id == actor.id})
    db.commit()
    return conflict


def _assigned_team_ids(db: Session, comp: Competition, judge_id: uuid.UUID) -> set[uuid.UUID]:
    assignments = db.scalars(select(JudgeAssignment).where(JudgeAssignment.competition_id == comp.id,
                                                           JudgeAssignment.judge_id == judge_id)).all()
    if any(a.team_id is None for a in assignments):
        ids = set(db.scalars(select(EventSubmission.team_id).where(EventSubmission.competition_id == comp.id)))
    else:
        ids = {a.team_id for a in assignments if a.team_id}
    conflicts = set(db.scalars(select(JudgeConflict.team_id).where(JudgeConflict.competition_id == comp.id,
                                                                   JudgeConflict.judge_id == judge_id)))
    return ids - conflicts


def _entry_label(team: Team, blind: bool) -> str:
    if not blind:
        return team.name
    return "Entry " + hashlib.sha256(str(team.id).encode()).hexdigest()[:6].upper()


def judge_queue(actor: Actor, comp: Competition) -> dict[str, Any]:
    db = actor.db
    if not is_judge(actor, comp):
        raise Forbidden("You are not a judge for this event.")
    rubric = get_rubric(db, comp)
    team_ids = _assigned_team_ids(db, comp, actor.id)  # type: ignore[arg-type]
    teams = {t.id: t for t in db.scalars(select(Team).where(Team.id.in_(team_ids)))} if team_ids else {}
    subs = {s.team_id: s for s in db.scalars(select(EventSubmission).where(EventSubmission.team_id.in_(team_ids)))} if team_ids else {}
    scores = {s.team_id: s for s in db.scalars(select(JudgeScore).where(JudgeScore.competition_id == comp.id,
                                                                        JudgeScore.judge_id == actor.id))}
    slots = {s.team_id: s for s in db.scalars(select(PresentationSlot).where(PresentationSlot.competition_id == comp.id))}
    blind = bool(rubric and rubric.blind_judging)
    entries = []
    for tid in sorted(team_ids, key=lambda t: teams[t].name if t in teams else ""):
        team = teams.get(tid)
        if team is None:
            continue
        sub = subs.get(tid)
        score = scores.get(tid)
        slot = slots.get(tid)
        entries.append({
            "team_id": str(tid), "label": _entry_label(team, blind),
            "submission": None if sub is None else {
                "title": sub.title, "summary": sub.summary, "description_html": sub.description_html, "repo_url": sub.repo_url,
                "demo_url": sub.demo_url, "video_url": sub.video_url, "submitted_at": sub.submitted_at.isoformat()},
            "my_score": None if score is None else {"scores": score.scores, "feedback": score.feedback, "status": score.status,
                                                    "weighted_total": float(score.weighted_total) if score.weighted_total is not None else None},
            "slot": None if slot is None else {"starts_at": slot.starts_at.isoformat(), "ends_at": slot.ends_at.isoformat(),
                                               "location": slot.location, "meeting_url": slot.meeting_url},
        })
    return {"competition": {"slug": comp.slug, "title": comp.title}, "rubric": rubric.criteria if rubric else [],
            "rubric_version": rubric.version if rubric else 0, "finalized": bool(rubric and rubric.finalized_at),
            "entries": entries}


def weighted_total(criteria: list[dict[str, Any]], scores: dict[str, float]) -> float:
    total_weight = sum(c["weight"] for c in criteria)
    acc = sum(((scores[c["key"]] - c["min"]) / (c["max"] - c["min"])) * c["weight"] for c in criteria)
    return round(acc / total_weight * 100, 4)


def submit_score(actor: Actor, comp: Competition, team_id: uuid.UUID, scores: dict[str, float], feedback: str | None,
                 final: bool) -> JudgeScore:
    db = actor.db
    if not is_judge(actor, comp):
        raise Forbidden("You are not a judge for this event.")
    rubric = get_rubric(db, comp)
    if rubric is None or not rubric.criteria:
        raise Conflict("The rubric is not configured yet.", code="rubric_missing")
    if rubric.finalized_at:
        raise Conflict("Scores are locked because judging is finalized.", code="judging_finalized")
    if team_id not in _assigned_team_ids(db, comp, actor.id):  # type: ignore[arg-type]
        raise Forbidden("This entry is not assigned to you.", code="not_assigned")
    clean: dict[str, float] = {}
    errors: dict[str, str] = {}
    for c in rubric.criteria:
        if c["key"] not in scores:
            if final:
                errors[c["key"]] = "Required."
            continue
        v = float(scores[c["key"]])
        if not c["min"] <= v <= c["max"]:
            errors[c["key"]] = f"Must be between {c['min']:g} and {c['max']:g}."
        elif not c["allow_decimal"] and v != int(v):
            errors[c["key"]] = "Whole numbers only."
        clean[c["key"]] = v
    if errors:
        raise ValidationFailed(details={"fields": errors})
    row = db.scalar(select(JudgeScore).where(JudgeScore.competition_id == comp.id, JudgeScore.judge_id == actor.id,
                                             JudgeScore.team_id == team_id))
    if row is None:
        row = JudgeScore(competition_id=comp.id, judge_id=actor.id, team_id=team_id, rubric_version=rubric.version)
        db.add(row)
    row.scores = clean
    row.feedback = (feedback or "")[:5000] or None
    row.rubric_version = rubric.version
    complete = len(clean) == len(rubric.criteria)
    row.weighted_total = Decimal(str(weighted_total(rubric.criteria, clean))) if complete else None
    if final:
        row.status = JudgeScoreStatus.submitted
        row.submitted_at = utcnow()
        if rubric.locked_at is None:
            rubric.locked_at = utcnow()
    record_audit(db, actor.id, "judging.score_submit" if final else "judging.score_draft", target_type="team", target_id=team_id,
                 competition_id=comp.id, meta={"weighted_total": float(row.weighted_total) if row.weighted_total is not None else None})
    db.commit()
    return row


def aggregate_results(db: Session, comp: Competition) -> list[dict[str, Any]]:
    from app.modules.leaderboards.service import _team_details, result_label

    rubric = get_rubric(db, comp)
    conflicts = {(c.judge_id, c.team_id) for c in db.scalars(select(JudgeConflict).where(JudgeConflict.competition_id == comp.id))}
    scores = [s for s in db.scalars(select(JudgeScore).where(JudgeScore.competition_id == comp.id,
                                                             JudgeScore.status == JudgeScoreStatus.submitted))
              if (s.judge_id, s.team_id) not in conflicts and s.weighted_total is not None]
    first_key = rubric.criteria[0]["key"] if rubric and rubric.criteria else None
    by_team: dict[uuid.UUID, list[JudgeScore]] = {}
    for s in scores:
        by_team.setdefault(s.team_id, []).append(s)
    sub_times = {s.team_id: s.submitted_at for s in db.scalars(select(EventSubmission).where(EventSubmission.competition_id == comp.id))}
    agg = []
    for team_id, rows in by_team.items():
        avg = sum(float(r.weighted_total) for r in rows) / len(rows)  # type: ignore[arg-type]
        tb = sum(float(r.scores.get(first_key, 0)) for r in rows) / len(rows) if first_key else 0.0
        agg.append((team_id, round(avg, 4), tb, len(rows)))
    agg.sort(key=lambda a: (-a[1], -a[2], sub_times.get(a[0]) or utcnow(), str(a[0])))
    details = _team_details(db, comp, [a[0] for a in agg])
    total = len(agg)
    return [{"rank": i, "team_id": str(tid), "team_name": details.get(tid, {}).get("team_name"),
             "is_solo": details.get(tid, {}).get("is_solo", False), "members": details.get(tid, {}).get("members", []),
             "score": avg, "public_score": None, "judge_count": n, "submission_id": None,
             "submitted_at": sub_times[tid].isoformat() if tid in sub_times else None, "label": result_label(i, total)}
            for i, (tid, avg, _, n) in enumerate(agg, start=1)]


def scores_overview(actor: Actor, comp: Competition) -> dict[str, Any]:
    db = actor.db
    manager = can_manage_competition(actor, comp)
    rubric = get_rubric(db, comp)
    if not manager:
        if not (is_judge(actor, comp) and rubric and rubric.reveal_scores_to_judges):
            raise Forbidden("Scores from other judges are hidden.")
    scores = db.execute(select(JudgeScore, User, Team).join(User, User.id == JudgeScore.judge_id)
                        .join(Team, Team.id == JudgeScore.team_id).where(JudgeScore.competition_id == comp.id)).all()
    assignments = db.execute(select(JudgeAssignment, User).join(User, User.id == JudgeAssignment.judge_id)
                             .where(JudgeAssignment.competition_id == comp.id)).all() if manager else []
    conflicts = db.scalars(select(JudgeConflict).where(JudgeConflict.competition_id == comp.id)).all() if manager else []
    return {
        "rubric": rubric.criteria if rubric else [], "rubric_version": rubric.version if rubric else 0,
        "reveal_scores_to_judges": bool(rubric and rubric.reveal_scores_to_judges),
        "blind_judging": bool(rubric and rubric.blind_judging),
        "locked": bool(rubric and rubric.locked_at), "finalized": bool(rubric and rubric.finalized_at),
        "scores": [{"judge": user_mini(u).model_dump(mode="json") if manager else None, "team_id": str(t.id), "team_name": t.name,  # type: ignore[union-attr]
                    "scores": s.scores if manager else None, "feedback": s.feedback if manager else None,
                    "weighted_total": float(s.weighted_total) if s.weighted_total is not None else None,
                    "status": s.status, "submitted_at": s.submitted_at.isoformat() if s.submitted_at else None}
                   for s, u, t in scores],
        "assignments": [{"id": str(a.id), "judge": user_mini(u).model_dump(mode="json"), "team_id": str(a.team_id) if a.team_id else None,  # type: ignore[union-attr]
                         "panel": a.panel} for a, u in assignments],
        "conflicts": [{"judge_id": str(c.judge_id), "team_id": str(c.team_id), "reason": c.reason} for c in conflicts],
        "preview": aggregate_results(db, comp) if manager else [],
    }


def finalize_judging(actor: Actor, comp: Competition) -> Rubric:
    db = actor.db
    assert_manage_competition(actor, comp)
    rubric = get_rubric(db, comp)
    if rubric is None:
        raise Conflict("No rubric configured.", code="rubric_missing")
    if rubric.finalized_at is None:
        rubric.finalized_at = utcnow()
        rubric.finalized_by = actor.id
        record_audit(db, actor.id, "judging.finalize", target_type="competition", target_id=comp.id, competition_id=comp.id)
        db.commit()
    return rubric


def export_scores_csv(actor: Actor, comp: Competition) -> str:
    db = actor.db
    assert_manage_competition(actor, comp)
    rubric = get_rubric(db, comp)
    keys = [c["key"] for c in rubric.criteria] if rubric else []
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["team", "judge", "status", "weighted_total", *keys, "submitted_at_utc"])
    for s, u, t in db.execute(select(JudgeScore, User, Team).join(User, User.id == JudgeScore.judge_id)
                              .join(Team, Team.id == JudgeScore.team_id).where(JudgeScore.competition_id == comp.id)
                              .order_by(Team.name, User.handle)):
        w.writerow([t.name, u.handle, s.status, s.weighted_total, *[s.scores.get(k) for k in keys],
                    s.submitted_at.isoformat() if s.submitted_at else ""])
    return buf.getvalue()


# ----------------------------------------------------------------------------- event (project) submissions


def upsert_event_submission(actor: Actor, comp: Competition, data: dict[str, Any]) -> EventSubmission:
    db = actor.db
    if comp.scoring_mode != ScoringMode.judged:
        raise Conflict("This competition is scored automatically; upload predictions instead.", code="not_judged")
    team = team_for_user(db, comp.id, actor.id)
    if team is None:
        raise Forbidden("Join the event and form a team first.", code="team_required")
    if not state.submissions_open(comp):
        raise Conflict("The submission window is closed.", code="submissions_closed")
    size = db.query(TeamMember).filter(TeamMember.team_id == team.id).count()
    if size < comp.team_min_size:
        raise Conflict(f"Teams need at least {comp.team_min_size} members.", code="team_too_small")
    sub = db.scalar(select(EventSubmission).where(EventSubmission.competition_id == comp.id, EventSubmission.team_id == team.id))
    if sub is None:
        sub = EventSubmission(competition_id=comp.id, team_id=team.id, title=data["title"], submitted_by=actor.id)
        db.add(sub)
    sub.title = data["title"][:140]
    sub.summary = (data.get("summary") or "")[:300]
    sub.description_md = data.get("description_md") or ""
    sub.description_html = render_markdown(sub.description_md)
    sub.repo_url = validate_external_url(data.get("repo_url"), "repo_url")
    sub.demo_url = validate_external_url(data.get("demo_url"), "demo_url")
    sub.video_url = validate_external_url(data.get("video_url"), "video_url")
    sub.submitted_by = actor.id
    record_audit(db, actor.id, "event_submission.upsert", target_type="team", target_id=team.id, competition_id=comp.id)
    db.commit()
    return sub


def event_submissions(actor: Actor, comp: Competition) -> list[dict[str, Any]]:
    db = actor.db
    manager = can_manage_competition(actor, comp)
    public_gallery = comp.lifecycle in ("finalized", "archived")
    stmt = select(EventSubmission, Team).join(Team, Team.id == EventSubmission.team_id).where(EventSubmission.competition_id == comp.id)
    if not (manager or public_gallery):
        team = team_for_user(db, comp.id, actor.id)
        if team is None:
            return []
        stmt = stmt.where(EventSubmission.team_id == team.id)
    return [{"team_id": str(t.id), "team_name": t.name, "title": s.title, "summary": s.summary, "description_html": s.description_html,
             "repo_url": s.repo_url, "demo_url": s.demo_url, "video_url": s.video_url,
             "submitted_at": s.submitted_at.isoformat(), "updated_at": s.updated_at.isoformat()}
            for s, t in db.execute(stmt.order_by(EventSubmission.submitted_at))]


def set_slot(actor: Actor, comp: Competition, data: dict[str, Any]) -> PresentationSlot:
    db = actor.db
    assert_manage_competition(actor, comp)
    if data["ends_at"] <= data["starts_at"]:
        raise ValidationFailed(details={"fields": {"ends_at": "End must be after start."}})
    slot = PresentationSlot(competition_id=comp.id, team_id=data.get("team_id"), starts_at=data["starts_at"], ends_at=data["ends_at"],
                            location=data.get("location"), meeting_url=validate_external_url(data.get("meeting_url"), "meeting_url"),
                            notes=data.get("notes"))
    db.add(slot)
    db.commit()
    return slot


def list_slots(actor: Actor, comp: Competition) -> list[dict[str, Any]]:
    db = actor.db
    rows = db.execute(select(PresentationSlot, Team).outerjoin(Team, Team.id == PresentationSlot.team_id)
                      .where(PresentationSlot.competition_id == comp.id).order_by(PresentationSlot.starts_at)).all()
    staff = can_manage_competition(actor, comp) or is_judge(actor, comp)
    mine = team_for_user(db, comp.id, actor.id) if actor.is_authenticated else None
    out = []
    for slot, team in rows:
        visible_url = staff or (mine is not None and team is not None and team.id == mine.id)
        out.append({"id": str(slot.id), "team_id": str(team.id) if team else None, "team_name": team.name if team else None,
                    "starts_at": slot.starts_at.isoformat(), "ends_at": slot.ends_at.isoformat(), "location": slot.location,
                    "meeting_url": slot.meeting_url if visible_url else None, "notes": slot.notes if staff else None})
    return out


def delete_slot(actor: Actor, comp: Competition, slot_id: uuid.UUID) -> None:
    assert_manage_competition(actor, comp)
    slot = actor.db.get(PresentationSlot, slot_id)
    if slot is None or slot.competition_id != comp.id:
        raise NotFound()
    actor.db.delete(slot)
    actor.db.commit()


def judge_events(actor: Actor) -> list[dict[str, Any]]:
    db = actor.db
    rows = db.execute(select(Competition).join(CompetitionStaff, CompetitionStaff.competition_id == Competition.id)
                      .where(CompetitionStaff.user_id == actor.id, CompetitionStaff.role == StaffRole.judge)
                      .order_by(Competition.ends_at.desc().nulls_last())).scalars().all()
    out = []
    for comp in rows:
        team_ids = _assigned_team_ids(db, comp, actor.id)  # type: ignore[arg-type]
        done = db.query(JudgeScore).filter(JudgeScore.competition_id == comp.id, JudgeScore.judge_id == actor.id,
                                           JudgeScore.status == JudgeScoreStatus.submitted).count()
        rubric = get_rubric(db, comp)
        out.append({"slug": comp.slug, "title": comp.title, "status": state.effective_status(comp), "assigned": len(team_ids),
                    "submitted": done, "finalized": bool(rubric and rubric.finalized_at), "ends_at": comp.ends_at})
    return out
