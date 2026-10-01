"""Prediction-file submissions: upload validation, limits, idempotency, queuing, organizer tools."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import timedelta
from typing import BinaryIO

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import record_audit
from app.core.deps import Actor
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.core.permissions import can_manage_competition, participant_record
from app.core.rate_limit import enforce
from app.core.time import utc_day_start, utcnow
from app.core.validators import extension, looks_like_text, safe_filename
from app.jobs.queue import enqueue
from app.models.competition import Competition, Team, TeamMember
from app.models.enums import Lifecycle, NotificationKind, SubmissionStatus
from app.models.submission import Submission
from app.modules.competitions.service import submission_blockers
from app.modules.notifications.service import notify
from app.modules.teams.service import member_ids, team_for_user
from app.storage import get_storage
from app.storage.base import PREFIX_SUBMISSIONS, new_key

NOT_COUNTED = (SubmissionStatus.rejected, SubmissionStatus.failed, SubmissionStatus.canceled)


def submissions_today(db: Session, team_id: uuid.UUID) -> int:
    return db.scalar(select(func.count()).select_from(Submission).where(
        Submission.team_id == team_id, Submission.submitted_at >= utc_day_start(),
        Submission.status.not_in(NOT_COUNTED))) or 0


def submissions_total(db: Session, team_id: uuid.UUID) -> int:
    return db.scalar(select(func.count()).select_from(Submission).where(
        Submission.team_id == team_id, Submission.status.not_in(NOT_COUNTED))) or 0


def _quick_header_check(head: bytes, comp: Competition) -> None:
    """Fast, synchronous feedback on obvious format mistakes. Full validation runs in the evaluator."""
    if not looks_like_text(head[:65536]):
        raise ValidationFailed("The file must be a UTF-8 text CSV.", code="invalid_file")
    text = head.decode("utf-8", errors="ignore").lstrip("﻿")
    first_line = text.splitlines()[0] if text.splitlines() else ""
    if not first_line.strip():
        raise ValidationFailed("The file is empty.", code="invalid_submission", details={"errors": [
            {"code": "empty_file", "message": "The file is empty."}]})
    header = [h.strip() for h in next(csv.reader(io.StringIO(first_line)))]
    cfg = comp.evaluation or {}
    required = [cfg.get("id_column", "id"), cfg.get("target_column", "target")]
    missing = [c for c in required if c not in header]
    if missing:
        raise ValidationFailed(
            f"Missing required column(s): {', '.join(missing)}. Expected header: {','.join(required)}",
            code="invalid_submission",
            details={"errors": [{"code": "missing_column", "message": f"Required column '{m}' is missing."} for m in missing]},
        )


def create_submission(actor: Actor, comp: Competition, *, filename: str, stream: BinaryIO, description: str | None,
                      idempotency_key: str | None) -> tuple[Submission, bool]:
    """Returns (submission, created). A retried request with the same Idempotency-Key returns the original."""
    db = actor.db
    if participant_record(actor, comp) is None:
        raise Forbidden("Join the competition before submitting.", code="not_participant")
    team = team_for_user(db, comp.id, actor.id)
    blockers = submission_blockers(actor, comp, team)
    if blockers:
        raise Conflict("You cannot submit right now.", code=blockers[0], details={"blockers": blockers})
    assert team is not None
    if idempotency_key:
        existing = db.scalar(select(Submission).where(Submission.team_id == team.id, Submission.idempotency_key == idempotency_key))
        if existing is not None:
            return existing, False
    enforce("submission_upload", str(actor.id), limit=30, window_seconds=3600)
    # Serialize concurrent submissions from the same team so limits cannot be raced.
    db.execute(select(Team.id).where(Team.id == team.id).with_for_update())
    today = submissions_today(db, team.id)
    if today >= comp.daily_submission_limit:
        resets = utc_day_start() + timedelta(days=1)
        raise Conflict(f"Daily limit of {comp.daily_submission_limit} submissions reached. Resets at 00:00 UTC.",
                       code="submission_limit_reached", details={"limit": comp.daily_submission_limit, "resets_at": resets.isoformat()})
    if comp.total_submission_limit and submissions_total(db, team.id) >= comp.total_submission_limit:
        raise Conflict("Total submission limit reached.", code="submission_limit_reached",
                       details={"limit": comp.total_submission_limit})
    name = safe_filename(filename)
    if extension(name) != ".csv":
        raise ValidationFailed("Submissions must be CSV files (.csv).", code="invalid_file_type")
    key = new_key(PREFIX_SUBMISSIONS, ".csv")
    stored = get_storage().save_stream(key, stream, max_bytes=comp.max_submission_mb * 1024 * 1024, content_type="text/csv")
    try:
        _quick_header_check(stored.head, comp)
    except ValidationFailed:
        get_storage().delete(key)
        raise
    sub = Submission(competition_id=comp.id, team_id=team.id, user_id=actor.id, status=SubmissionStatus.queued,
                     description=(description or "").strip()[:500] or None, filename=name, storage_key=key,
                     size_bytes=stored.size_bytes, sha256=stored.sha256, idempotency_key=idempotency_key)
    db.add(sub)
    db.flush()
    comp.submission_count += 1
    enqueue(db, "score_submission", {"submission_id": str(sub.id)}, idempotency_key=f"score:{sub.id}", max_attempts=3)
    db.commit()
    return sub, True


def get_for_viewer(actor: Actor, submission_id: uuid.UUID) -> tuple[Submission, Competition, bool]:
    db = actor.db
    sub = db.get(Submission, submission_id)
    if sub is None:
        raise NotFound()
    comp = db.get(Competition, sub.competition_id)
    assert comp is not None
    is_member = actor.is_authenticated and bool(db.scalar(select(TeamMember.id).where(
        TeamMember.team_id == sub.team_id, TeamMember.user_id == actor.id)))
    manager = can_manage_competition(actor, comp)
    if not (is_member or manager):
        raise NotFound()
    return sub, comp, manager


def cancel(actor: Actor, submission_id: uuid.UUID) -> Submission:
    db = actor.db
    sub, comp, manager = get_for_viewer(actor, submission_id)
    team = db.get(Team, sub.team_id)
    if not (sub.user_id == actor.id or (team and team.captain_id == actor.id) or manager):
        raise Forbidden()
    if sub.status != SubmissionStatus.queued:
        raise Conflict("Only queued submissions can be canceled.", code="not_cancelable")
    sub.status = SubmissionStatus.canceled
    sub.completed_at = utcnow()
    db.commit()
    return sub


def list_team_submissions(actor: Actor, comp: Competition, team: Team, limit: int, offset: int) -> tuple[list[Submission], int]:
    db = actor.db
    base = select(Submission).where(Submission.team_id == team.id)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = list(db.scalars(base.order_by(Submission.submitted_at.desc(), Submission.id.desc()).limit(limit).offset(offset)))
    return rows, total


def select_final(actor: Actor, comp: Competition, submission_id: uuid.UUID, selected: bool) -> Submission:
    db = actor.db
    sub, _, _ = get_for_viewer(actor, submission_id)
    if sub.competition_id != comp.id:
        raise NotFound()
    if actor.id not in member_ids(db, sub.team_id):
        raise Forbidden("Only team members can choose final submissions.")
    if comp.lifecycle != Lifecycle.published:
        raise Conflict("Final selections are locked.", code="results_final")
    if selected:
        if sub.status != SubmissionStatus.scored or sub.invalidated_at is not None:
            raise Conflict("Only scored submissions can be selected.", code="not_selectable")
        count = db.scalar(select(func.count()).select_from(Submission).where(
            Submission.team_id == sub.team_id, Submission.is_final_selected.is_(True))) or 0
        if not sub.is_final_selected and count >= comp.final_selection_limit:
            raise Conflict(f"You can select at most {comp.final_selection_limit} final submissions.", code="selection_limit")
    sub.is_final_selected = selected
    db.commit()
    return sub


def invalidate(actor: Actor, comp: Competition, submission_id: uuid.UUID, reason: str) -> Submission:
    db = actor.db
    if not can_manage_competition(actor, comp):
        raise Forbidden()
    sub = db.get(Submission, submission_id)
    if sub is None or sub.competition_id != comp.id:
        raise NotFound()
    if sub.invalidated_at is not None:
        return sub
    sub.invalidated_at = utcnow()
    sub.invalidated_by = actor.id
    sub.invalidation_reason = reason
    sub.is_final_selected = False
    record_audit(db, actor.id, "submission.invalidate", target_type="submission", target_id=sub.id, competition_id=comp.id,
                 reason=reason)
    for uid in member_ids(db, sub.team_id):
        notify(db, uid, NotificationKind.submission_rejected, f"A submission to {comp.title} was invalidated by the organizers",
               body=reason[:200], link=f"/competitions/{comp.slug}/submissions", dedupe_key=f"invalidated:{sub.id}")
    db.commit()
    return sub


def organizer_list(actor: Actor, comp: Competition, status: str | None, limit: int, offset: int) -> tuple[list[tuple[Submission, Team]], int]:
    db = actor.db
    if not can_manage_competition(actor, comp):
        raise Forbidden()
    stmt = select(Submission, Team).join(Team, Team.id == Submission.team_id).where(Submission.competition_id == comp.id)
    if status:
        stmt = stmt.where(Submission.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(stmt.order_by(Submission.submitted_at.desc()).limit(limit).offset(offset)).all()
    return [(s, t) for s, t in rows], total
