"""Data retention (maintenance task).

* Runtime events: deleted after the workspace's retention period — the workspace setting
  ``settings.retention.runtime_events_days`` when the plan allows custom retention, otherwise the plan's
  retention, otherwise ``RETENTION_RUNTIME_EVENTS_DAYS``.
* Audit progress events of finished audits: deleted after ``RETENTION_AUDIT_EVENTS_DAYS``.
* Evidence, findings, audit logs and the usage ledger are never deleted by retention: they are the
  workspace's record. (Guest sandboxes are purged whole when they expire.)

Deletes run in bounded batches so a large backlog never produces one long-running transaction.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete, select

from aegis_api.billing import plans
from aegis_api.config import get_settings
from aegis_api.db.base import utcnow
from aegis_api.db.session import admin_session_scope, rowcount
from aegis_api.models import Audit, AuditEvent, Organization, RuntimeEvent

BATCH = 5_000


def runtime_retention_days(org: Organization) -> int:
    settings = get_settings()
    plan = plans.get_plan(org.plan)
    configured = ((org.settings or {}).get("retention") or {}).get("runtime_events_days")
    if configured and plan.features.get("custom_retention"):
        return max(1, int(configured))
    if plan.retention_days is not None:
        return plan.retention_days
    return settings.retention_runtime_events_days


def apply_all() -> dict[str, int]:
    removed_runtime = 0
    removed_events = 0
    with admin_session_scope() as session:
        orgs = session.scalars(select(Organization)).all()
        for org in orgs:
            cutoff = utcnow() - timedelta(days=runtime_retention_days(org))
            ids = session.scalars(
                select(RuntimeEvent.id)
                .where(RuntimeEvent.organization_id == org.id, RuntimeEvent.occurred_at < cutoff)
                .limit(BATCH)
            ).all()
            if ids:
                removed_runtime += rowcount(session.execute(delete(RuntimeEvent).where(RuntimeEvent.id.in_(ids))))
        cutoff = utcnow() - timedelta(days=get_settings().retention_audit_events_days)
        old_audits = select(Audit.id).where(
            Audit.status.in_(["completed", "partially_completed", "failed", "cancelled"]), Audit.completed_at < cutoff
        )
        ids = session.scalars(select(AuditEvent.id).where(AuditEvent.audit_id.in_(old_audits)).limit(BATCH)).all()
        if ids:
            removed_events = rowcount(session.execute(delete(AuditEvent).where(AuditEvent.id.in_(ids))))
    return {"runtime_events": removed_runtime, "audit_events": removed_events}
