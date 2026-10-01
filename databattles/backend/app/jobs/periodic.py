"""Periodic maintenance run by the worker.

Each task is guarded by a PostgreSQL advisory lock so running several workers never duplicates work,
and every notification it sends carries a dedupe key, so re-running a task is always safe.
"""

from __future__ import annotations

import logging
import zlib
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.time import utcnow
from app.jobs.queue import recover_stale
from app.models.competition import Competition, CompetitionParticipant, CompetitionStaff, TeamInvitation
from app.models.enums import InvitationStatus, Lifecycle, NotificationKind, StaffRole, SubmissionStatus
from app.models.org import OrgMembership
from app.models.project import Upload
from app.models.submission import Submission
from app.modules.notifications.service import notify
from app.storage import get_storage
from app.storage.base import PREFIX_TMP

logger = logging.getLogger("databattles.periodic")


def _organizer_ids(db: Session, comp: Competition) -> set:
    ids = set(db.scalars(select(CompetitionStaff.user_id).where(CompetitionStaff.competition_id == comp.id,
                                                                 CompetitionStaff.role == StaffRole.organizer)))
    if comp.created_by:
        ids.add(comp.created_by)
    if comp.host_org_id:
        ids |= set(db.scalars(select(OrgMembership.user_id).where(OrgMembership.org_id == comp.host_org_id,
                                                                  OrgMembership.status == "active",
                                                                  OrgMembership.role.in_(["owner", "admin"]))))
    return ids


def deadline_reminders(db: Session) -> int:
    now = utcnow()
    sent = 0
    for window, label in ((timedelta(hours=24), "24h"), (timedelta(hours=1), "1h")):
        comps = db.scalars(select(Competition).where(Competition.lifecycle == Lifecycle.published, Competition.ends_at > now,
                                                     Competition.ends_at <= now + window)).all()
        for comp in comps:
            when = "in about 24 hours" if label == "24h" else "in about an hour"
            for uid in db.scalars(select(CompetitionParticipant.user_id).where(CompetitionParticipant.competition_id == comp.id,
                                                                                CompetitionParticipant.status == "active")):
                notify(db, uid, NotificationKind.deadline_reminder, f"{comp.title} closes {when}",
                       link=f"/competitions/{comp.slug}", dedupe_key=f"deadline:{comp.id}:{label}",
                       email_template="competition_deadline" if label == "24h" else None,
                       email_context={"competition": comp.title, "when": when,
                                      "link": f"{settings.WEB_BASE_URL}/competitions/{comp.slug}"})
                sent += 1
    db.commit()
    return sent


def ready_to_finalize(db: Session) -> int:
    now = utcnow()
    comps = db.scalars(select(Competition).where(Competition.lifecycle == Lifecycle.published, Competition.ends_at < now,
                                                 Competition.ends_at > now - timedelta(days=30))).all()
    for comp in comps:
        for uid in _organizer_ids(db, comp):
            notify(db, uid, NotificationKind.judging, f"{comp.title} has ended — review and finalize results",
                   link=f"/competitions/{comp.slug}/manage", dedupe_key=f"finalize-ready:{comp.id}")
    db.commit()
    return len(comps)


def evaluator_failure_alerts(db: Session) -> int:
    since = utcnow() - timedelta(hours=1)
    rows = db.execute(select(Submission.competition_id, func.count()).where(Submission.status == SubmissionStatus.failed,
                                                                             Submission.completed_at > since)
                      .group_by(Submission.competition_id).having(func.count() >= 3)).all()
    for comp_id, n in rows:
        comp = db.get(Competition, comp_id)
        if comp is None:
            continue
        for uid in _organizer_ids(db, comp):
            notify(db, uid, NotificationKind.judging, f"{n} submissions failed to score in {comp.title} in the last hour",
                   body="Check the evaluation configuration and ground truth file.", link=f"/competitions/{comp.slug}/manage",
                   dedupe_key=f"evalfail:{comp.id}:{utcnow():%Y%m%d%H}")
    db.commit()
    return len(rows)


def expire_invitations(db: Session) -> int:
    res = db.execute(update(TeamInvitation).where(TeamInvitation.status == InvitationStatus.pending,
                                                  TeamInvitation.expires_at < utcnow()).values(status=InvitationStatus.expired))
    db.commit()
    return res.rowcount or 0


def cleanup_orphans(db: Session) -> int:
    """Remove unattached uploads older than a day and stale temporary objects."""
    storage = get_storage()
    cutoff = utcnow() - timedelta(days=1)
    removed = 0
    for up in db.scalars(select(Upload).where(Upload.attached.is_(False), Upload.created_at < cutoff).limit(500)):
        try:
            storage.delete(up.storage_key)
        except Exception:  # noqa: BLE001
            logger.warning("orphan_delete_failed", extra={"key": up.storage_key})
            continue
        db.delete(up)
        removed += 1
    try:
        for key, modified in storage.list_keys(PREFIX_TMP + "/"):
            if modified < cutoff:
                storage.delete(key)
                removed += 1
    except Exception:  # noqa: BLE001
        logger.warning("tmp_cleanup_failed")
    db.commit()
    return removed


def github_sync(db: Session) -> int:
    if not (settings.GITHUB_API_TOKEN or settings.github_oauth_enabled):
        return 0  # anonymous API budget is too small for background sync; manual sync still works
    from app.modules.opensource.service import schedule_stale_syncs

    return schedule_stale_syncs(db)


def recover(db: Session) -> int:
    return recover_stale(db)


# (name, interval, function)
TASKS: list[tuple[str, timedelta, Callable[[Session], int]]] = [
    ("recover_stale_jobs", timedelta(minutes=1), recover),
    ("deadline_reminders", timedelta(minutes=10), deadline_reminders),
    ("ready_to_finalize", timedelta(minutes=30), ready_to_finalize),
    ("evaluator_failure_alerts", timedelta(minutes=10), evaluator_failure_alerts),
    ("expire_invitations", timedelta(hours=1), expire_invitations),
    ("cleanup_orphans", timedelta(hours=6), cleanup_orphans),
    ("github_sync", timedelta(hours=1), github_sync),
]


def run_due(db_factory: Callable[[], Session], last_run: dict[str, float], now_ts: float) -> None:
    for name, interval, fn in TASKS:
        if now_ts - last_run.get(name, 0) < interval.total_seconds():
            continue
        last_run[name] = now_ts
        lock_id = zlib.crc32(f"periodic:{name}".encode())
        with db_factory() as db:
            try:
                got = db.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": lock_id})
                if not got:
                    continue
                try:
                    result = fn(db)
                    if result:
                        logger.info("periodic_task", extra={"task": name, "result": result})
                finally:
                    db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": lock_id})
                    db.commit()
            except Exception:  # noqa: BLE001 — a failing periodic task must not kill the worker
                db.rollback()
                logger.exception("periodic_task_failed", extra={"task": name})
