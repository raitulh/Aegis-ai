"""Governance workflow activities (idempotent; short tenant transactions only)."""

from __future__ import annotations

from typing import Any

from aegis_api.lab.core.deps import tenant_uow
from aegis_api.lab.governance.approvals import expire_due_approvals
from aegis_api.lab.usage.billing import rollup_usage_records
from aegis_api.lab.usage.service import parse_period
from aegis_api.lab.workflows.registry import ActivityContext, activity


@activity("governance.expire_approvals", timeout_seconds=120, max_attempts=5)
def expire_approvals(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Mark the organization's overdue PENDING approvals EXPIRED (re-running is a no-op)."""
    batch = int(payload.get("limit") or 500)
    total = 0
    while True:
        with tenant_uow(ctx.actor) as db:
            expired = expire_due_approvals(db, limit=batch)
        total += expired
        ctx.heartbeat({"expired": total})
        if expired < batch or ctx.is_cancelled():
            break
    return {"expired": total}


@activity("governance.rollup_usage", timeout_seconds=300, max_attempts=5)
def rollup_usage(ctx: ActivityContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Upsert the organization's usage records for ``payload["period"]`` ('YYYY-MM'; default: current month)."""
    start, end = parse_period(payload.get("period"))
    with tenant_uow(ctx.actor) as db:
        records = rollup_usage_records(db, ctx.organization_id, start, end)
        meters = {
            r.meter: {"quantity": str(r.quantity), "cost_usd": str(r.cost_usd), "finalized": r.finalized}
            for r in records
        }
    return {"period_start": start.isoformat(), "period_end": end.isoformat(), "records": len(meters), "meters": meters}
