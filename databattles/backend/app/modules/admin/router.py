"""Platform administration: health, errors, jobs, feature flags, users & roles, audit log, org
verification, revenue overview and maintenance tools. Every mutating action is audited."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import Field
from sqlalchemy import func, or_, select, text

from app.core.audit import record_audit
from app.core.config import settings
from app.core.deps import Actor, require_admin, require_moderator
from app.core.errors import Conflict, NotFound
from app.core.metrics import metrics
from app.core.pagination import PageParams
from app.core.rate_limit import limiter
from app.core.schemas import AuditEntryOut, Message, Schema, org_mini, user_mini
from app.core.time import utcnow
from app.jobs.queue import queue_stats
from app.models.community import EmailOutbox, Report
from app.models.competition import Competition
from app.models.enums import JobStatus, OrgVerification, PlatformRole, SubmissionStatus
from app.models.org import Organization, OrgSubscription, Plan
from app.models.submission import Submission
from app.models.system import AppErrorLog, AuditLog, FeatureFlag, Job
from app.models.user import AuthEvent, PlatformRoleAssignment, User

router = APIRouter(prefix="/admin", tags=["admin"])


class FlagIn(Schema):
    enabled: bool
    description: str | None = Field(default=None, max_length=300)
    is_public: bool | None = None


class RoleIn(Schema):
    role: str = Field(pattern="^(platform_admin|moderator)$")
    grant: bool
    reason: str = Field(min_length=3, max_length=500)


class PlanIn(Schema):
    plan_key: str = Field(max_length=32)
    reason: str = Field(min_length=3, max_length=500)


def _page(items: list[Any], total: int, params: PageParams) -> dict[str, Any]:
    return {"items": items, "total": total, "page": params.page, "page_size": params.page_size,
            "has_next": params.offset + len(items) < total}


@router.get("/health")
def health(actor: Actor = Depends(require_moderator)) -> dict[str, Any]:
    db = actor.db
    db_ok = True
    try:
        db.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        db_ok = False
    now = utcnow()
    stuck = db.scalar(select(func.count()).select_from(Submission).where(
        Submission.status.in_([SubmissionStatus.queued, SubmissionStatus.validating, SubmissionStatus.scoring]),
        Submission.submitted_at < now - timedelta(minutes=30))) or 0
    failed_24h = db.scalar(select(func.count()).select_from(Submission).where(
        Submission.status == SubmissionStatus.failed, Submission.submitted_at > now - timedelta(hours=24))) or 0
    errors_24h = db.scalar(select(func.count()).select_from(AppErrorLog).where(AppErrorLog.created_at > now - timedelta(hours=24))) or 0
    emails_failed = db.scalar(select(func.count()).select_from(EmailOutbox).where(EmailOutbox.status == "failed",
                                                                                   EmailOutbox.created_at > now - timedelta(hours=24))) or 0
    open_reports = db.scalar(select(func.count()).select_from(Report).where(Report.status == "open")) or 0
    suspicious = db.scalar(select(func.count()).select_from(AuthEvent).where(AuthEvent.suspicious.is_(True),
                                                                              AuthEvent.created_at > now - timedelta(hours=24))) or 0
    last_worker = db.scalar(select(func.max(Job.finished_at)))
    return {
        "database": "ok" if db_ok else "error",
        "api": metrics.snapshot(),
        "jobs": queue_stats(db),
        "worker_last_job_finished_at": last_worker,
        "submissions": {"stuck_over_30m": stuck, "failed_24h": failed_24h},
        "errors_24h": errors_24h, "emails_failed_24h": emails_failed, "open_reports": open_reports,
        "suspicious_auth_events_24h": suspicious,
        "rate_limit_incidents": [{"bucket": k, "count": v, "last_at": datetime.fromtimestamp(limiter.incident_last_at.get(k, 0))}
                                 for k, v in sorted(limiter.incidents.items(), key=lambda kv: -kv[1])],
        "config": {"env": settings.ENV, "storage": settings.STORAGE_BACKEND, "email": settings.EMAIL_BACKEND,
                   "evaluator_sandbox": settings.EVALUATOR_SANDBOX, "embedded_worker": settings.EMBEDDED_WORKER,
                   "github_oauth": settings.github_oauth_enabled, "google_oauth": settings.google_enabled,
                   "github_webhooks": bool(settings.GITHUB_WEBHOOK_SECRET), "demo_mode": settings.DEMO_MODE},
    }


@router.get("/errors")
def errors(params: PageParams = Depends(), source: str | None = Query(None, pattern="^(api|worker)$"),
           actor: Actor = Depends(require_admin)) -> dict[str, Any]:
    stmt = select(AppErrorLog)
    if source:
        stmt = stmt.where(AppErrorLog.source == source)
    total = actor.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = actor.db.scalars(stmt.order_by(AppErrorLog.created_at.desc()).limit(params.page_size).offset(params.offset)).all()
    return _page([{"id": r.id, "created_at": r.created_at, "request_id": r.request_id, "method": r.method, "path": r.path,
                   "error_type": r.error_type, "message": r.message, "source": r.source} for r in rows], total, params)


@router.get("/jobs")
def jobs(params: PageParams = Depends(), status: str | None = Query(None, pattern="^(queued|running|succeeded|failed|dead)$"),
         kind: str | None = Query(None, max_length=48), actor: Actor = Depends(require_admin)) -> dict[str, Any]:
    stmt = select(Job)
    if status:
        stmt = stmt.where(Job.status == status)
    if kind:
        stmt = stmt.where(Job.kind == kind)
    total = actor.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = actor.db.scalars(stmt.order_by(Job.created_at.desc()).limit(params.page_size).offset(params.offset)).all()
    return _page([{"id": j.id, "kind": j.kind, "status": j.status, "attempts": j.attempts, "max_attempts": j.max_attempts,
                   "run_after": j.run_after, "last_error": j.last_error, "created_at": j.created_at, "finished_at": j.finished_at}
                  for j in rows], total, params)


@router.post("/jobs/{job_id}/retry", response_model=Message)
def retry_job(job_id: uuid.UUID, actor: Actor = Depends(require_admin)) -> Message:
    job = actor.db.get(Job, job_id)
    if job is None:
        raise NotFound()
    if job.status not in (JobStatus.dead, JobStatus.failed):
        raise Conflict("Only failed or dead jobs can be retried.")
    job.status, job.attempts, job.run_after, job.last_error, job.finished_at = JobStatus.queued, 0, utcnow(), None, None
    record_audit(actor.db, actor.id, "admin.job_retry", target_type="job", target_id=job.id, meta={"kind": job.kind})
    actor.db.commit()
    return Message(message="Job re-queued.")


@router.get("/flags")
def flags(actor: Actor = Depends(require_admin)) -> list[dict[str, Any]]:
    return [{"key": f.key, "enabled": f.enabled, "description": f.description, "is_public": f.is_public, "updated_at": f.updated_at}
            for f in actor.db.scalars(select(FeatureFlag).order_by(FeatureFlag.key))]


@router.put("/flags/{key}", response_model=Message)
def set_flag(key: str, data: FlagIn, actor: Actor = Depends(require_admin)) -> Message:
    if not key.replace("_", "").replace(".", "").isalnum() or len(key) > 64:
        raise NotFound()
    flag = actor.db.get(FeatureFlag, key)
    if flag is None:
        flag = FeatureFlag(key=key)
        actor.db.add(flag)
    flag.enabled = data.enabled
    if data.description is not None:
        flag.description = data.description
    if data.is_public is not None:
        flag.is_public = data.is_public
    flag.updated_by = actor.id
    record_audit(actor.db, actor.id, "admin.flag_set", target_type="feature_flag", target_id=key, meta={"enabled": data.enabled})
    actor.db.commit()
    return Message(message=f"Flag {key} {'enabled' if data.enabled else 'disabled'}.")


@router.get("/users")
def users(params: PageParams = Depends(), q: str | None = Query(None, max_length=80),
          status: str | None = Query(None, pattern="^(active|suspended|banned|deleted)$"),
          role: str | None = Query(None, pattern="^(platform_admin|moderator)$"), actor: Actor = Depends(require_moderator)) -> dict[str, Any]:
    db = actor.db
    stmt = select(User)
    if q:
        like = f"%{q.strip()}%"
        clauses = [User.handle.ilike(like), User.display_name.ilike(like)]
        if actor.is_admin:
            clauses.append(User.email.ilike(like))  # email lookup is an admin-only support tool
        stmt = stmt.where(or_(*clauses))
    if status:
        stmt = stmt.where(User.status == status)
    if role:
        stmt = stmt.where(User.id.in_(select(PlatformRoleAssignment.user_id).where(PlatformRoleAssignment.role == role)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(User.created_at.desc()).limit(params.page_size).offset(params.offset)).all()
    roles: dict[uuid.UUID, list[str]] = {}
    for r in db.scalars(select(PlatformRoleAssignment).where(PlatformRoleAssignment.user_id.in_([u.id for u in rows]))):
        roles.setdefault(r.user_id, []).append(r.role)
    return _page([{"user": user_mini(u), "email": u.email if actor.is_admin else None, "status": u.status,
                   "status_reason": u.status_reason, "email_verified": u.email_verified_at is not None, "created_at": u.created_at,
                   "last_login_at": u.last_login_at, "roles": roles.get(u.id, []), "is_demo": u.is_demo} for u in rows], total, params)


@router.put("/users/{user_id}/roles", response_model=Message)
def set_role(user_id: uuid.UUID, data: RoleIn, actor: Actor = Depends(require_admin)) -> Message:
    db = actor.db
    user = db.get(User, user_id)
    if user is None:
        raise NotFound()
    existing = db.scalar(select(PlatformRoleAssignment).where(PlatformRoleAssignment.user_id == user_id,
                                                              PlatformRoleAssignment.role == data.role))
    if data.grant and existing is None:
        db.add(PlatformRoleAssignment(user_id=user_id, role=PlatformRole(data.role), granted_by=actor.id))
    elif not data.grant and existing is not None:
        if data.role == PlatformRole.platform_admin:
            admins = db.scalar(select(func.count()).select_from(PlatformRoleAssignment).where(
                PlatformRoleAssignment.role == PlatformRole.platform_admin)) or 0
            if admins <= 1:
                raise Conflict("The platform needs at least one admin.", code="last_admin")
        db.delete(existing)
    record_audit(db, actor.id, "admin.role_grant" if data.grant else "admin.role_revoke", target_type="user", target_id=user_id,
                 reason=data.reason, meta={"role": data.role})
    db.commit()
    return Message(message="Role updated.")


@router.get("/audit")
def audit_log(params: PageParams = Depends(), action: str | None = Query(None, max_length=64), target_type: str | None = Query(None, max_length=32),
              target_id: str | None = Query(None, max_length=64), actor_handle: str | None = Query(None, max_length=31),
              org_id: uuid.UUID | None = None, competition_id: uuid.UUID | None = None,
              actor: Actor = Depends(require_admin)) -> dict[str, Any]:
    db = actor.db
    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action.ilike(f"{action}%"))
    if target_type:
        stmt = stmt.where(AuditLog.target_type == target_type)
    if target_id:
        stmt = stmt.where(AuditLog.target_id == target_id)
    if org_id:
        stmt = stmt.where(AuditLog.org_id == org_id)
    if competition_id:
        stmt = stmt.where(AuditLog.competition_id == competition_id)
    if actor_handle:
        stmt = stmt.where(AuditLog.actor_id.in_(select(User.id).where(User.handle == actor_handle.lower().lstrip("@"))))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(AuditLog.created_at.desc()).limit(params.page_size).offset(params.offset)).all()
    users_by_id = {u.id: u for u in db.scalars(select(User).where(User.id.in_([r.actor_id for r in rows if r.actor_id])))}
    items = [AuditEntryOut(id=r.id, created_at=r.created_at, action=r.action, actor=user_mini(users_by_id.get(r.actor_id)) if r.actor_id else None,
                           target_type=r.target_type, target_id=r.target_id, reason=r.reason, meta=r.meta or {}) for r in rows]
    return _page([i.model_dump() for i in items], total, params)


@router.get("/orgs/verification-queue")
def verification_queue(actor: Actor = Depends(require_admin)) -> list[dict[str, Any]]:
    rows = actor.db.scalars(select(Organization).where(Organization.verification_status == OrgVerification.pending)
                            .order_by(Organization.updated_at)).all()
    out = []
    for o in rows:
        note = actor.db.scalar(select(AuditLog.reason).where(AuditLog.action == "org.verification_requested",
                                                             AuditLog.org_id == o.id).order_by(AuditLog.created_at.desc()).limit(1))
        out.append({"org": org_mini(o), "website_url": o.website_url, "email_domains": list(o.email_domains or []),
                    "requested_note": note, "created_at": o.created_at})
    return out


@router.get("/revenue")
def revenue(actor: Actor = Depends(require_admin)) -> dict[str, Any]:
    db = actor.db
    rows = db.execute(select(Plan.key, Plan.name, Plan.price_cents_monthly, func.count(OrgSubscription.id))
                      .outerjoin(OrgSubscription, (OrgSubscription.plan_key == Plan.key) & (OrgSubscription.status == "active"))
                      .group_by(Plan.key, Plan.name, Plan.price_cents_monthly).order_by(Plan.price_cents_monthly)).all()
    failed = db.scalar(select(func.count()).select_from(OrgSubscription).where(OrgSubscription.last_payment_failed_at.is_not(None))) or 0
    return {
        "plans": [{"key": k, "name": n, "price_cents_monthly": p, "active_subscriptions": c, "mrr_cents": p * c} for k, n, p, c in rows],
        "mrr_cents": sum(p * c for _, _, p, c in rows), "payment_failures": failed, "provider": "manual",
        "note": "Figures reflect subscription records in this database (manual billing provider). No payment processor is connected.",
    }


@router.put("/orgs/{org_id}/plan", response_model=Message)
def set_plan(org_id: uuid.UUID, data: PlanIn, actor: Actor = Depends(require_admin)) -> Message:
    db = actor.db
    org = db.get(Organization, org_id)
    plan = db.get(Plan, data.plan_key)
    if org is None or plan is None:
        raise NotFound()
    sub = db.scalar(select(OrgSubscription).where(OrgSubscription.org_id == org.id))
    if sub is None:
        sub = OrgSubscription(org_id=org.id, plan_key=plan.key, status="active", provider="manual")
        db.add(sub)
    sub.plan_key = plan.key
    org.plan_key = plan.key
    record_audit(db, actor.id, "billing.plan_change", target_type="organization", target_id=org.id, org_id=org.id, reason=data.reason,
                 meta={"plan": plan.key})
    db.commit()
    return Message(message=f"{org.name} is now on {plan.name}.")


@router.post("/search/reindex", response_model=Message)
def reindex(actor: Actor = Depends(require_admin)) -> Message:
    from app.modules.search.indexer import reindex_all

    n = reindex_all(actor.db)
    record_audit(actor.db, actor.id, "admin.reindex", meta={"documents": n})
    actor.db.commit()
    return Message(message=f"Reindexed {n} records.")


@router.post("/badges/recalculate", response_model=Message)
def recalc_badges(actor: Actor = Depends(require_admin)) -> Message:
    from app.modules.credentials.badges import recalculate_all

    return Message(message=f"Awarded {recalculate_all(actor)} new badge(s).")


@router.get("/emails")
def emails(params: PageParams = Depends(), status: str | None = Query(None, pattern="^(queued|sent|failed)$"),
           actor: Actor = Depends(require_admin)) -> dict[str, Any]:
    stmt = select(EmailOutbox)
    if status:
        stmt = stmt.where(EmailOutbox.status == status)
    total = actor.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = actor.db.scalars(stmt.order_by(EmailOutbox.created_at.desc()).limit(params.page_size).offset(params.offset)).all()
    return _page([{"id": e.id, "to": e.to_email, "template": e.template, "template_version": e.template_version, "subject": e.subject,
                   "status": e.status, "attempts": e.attempts, "error": e.error, "created_at": e.created_at, "sent_at": e.sent_at}
                  for e in rows], total, params)


@router.get("/stats")
def platform_stats(actor: Actor = Depends(require_moderator)) -> dict[str, Any]:
    db = actor.db
    now = utcnow()

    def count(model: Any, *where: Any) -> int:
        return db.scalar(select(func.count()).select_from(model).where(*where)) or 0

    signups = db.execute(select(func.date_trunc("day", User.created_at).label("d"), func.count())
                         .where(User.created_at > now - timedelta(days=30)).group_by("d").order_by("d")).all()
    subs = db.execute(select(func.date_trunc("day", Submission.submitted_at).label("d"), func.count())
                      .where(Submission.submitted_at > now - timedelta(days=30)).group_by("d").order_by("d")).all()
    return {
        "users": {"total": count(User, User.status == "active"), "new_7d": count(User, User.created_at > now - timedelta(days=7)),
                  "demo": count(User, User.is_demo.is_(True))},
        "competitions": {"total": count(Competition), "published": count(Competition, Competition.lifecycle == "published")},
        "submissions": {"total": count(Submission), "last_24h": count(Submission, Submission.submitted_at > now - timedelta(hours=24))},
        "organizations": {"total": count(Organization),
                          "verified": count(Organization, Organization.verification_status == OrgVerification.verified)},
        "signups_by_day": [{"day": d.date().isoformat(), "count": n} for d, n in signups],
        "submissions_by_day": [{"day": d.date().isoformat(), "count": n} for d, n in subs],
    }


@router.post("/demo/purge", response_model=Message, summary="Remove all seeded demo data (irreversible)")
def purge_demo(confirm: str = Query(..., pattern="^PURGE-DEMO$"), actor: Actor = Depends(require_admin)) -> Message:
    from app.seed.purge import purge_demo_data

    counts = purge_demo_data(actor.db)
    record_audit(actor.db, actor.id, "admin.demo_purge", meta=counts)
    actor.db.commit()
    return Message(message="Demo data removed: " + ", ".join(f"{k}={v}" for k, v in counts.items()))

