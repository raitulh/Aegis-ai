"""Billing abstraction.

Usage is metered in the lab ledgers (model/compute/storage usage) and rolled up daily into ``usage_records``
(idempotent per organization/metric/day). A ``BillingProvider`` would report those records to a payment
system; the only provider shipped is ``none`` (``BILLING_PROVIDER=none``), which records usage and plan limits
but issues no invoices — nothing here pretends to charge anyone.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.db.base import utcnow
from aegis_api.models import Plan, Subscription, UsageRecord
from aegis_api.models.lab import ComputeUsage, ModelUsage, StorageUsage

DEFAULT_PLAN = {"key": "self_hosted", "name": "Self-hosted", "limits": {}, "price": {}}


class BillingProvider(ABC):
    name = "base"

    @abstractmethod
    def report_usage(self, records: list[UsageRecord]) -> list[str | None]: ...


class NoBillingProvider(BillingProvider):
    """Records usage only. No invoices are created and nothing is sent to a payment processor."""

    name = "none"

    def report_usage(self, records: list[UsageRecord]) -> list[str | None]:
        return [None for _ in records]


def provider() -> BillingProvider:
    if get_settings().billing_provider == "none":
        return NoBillingProvider()
    raise RuntimeError(f"unsupported billing provider {get_settings().billing_provider}")


def rollup_day(db: Session, organization_id: uuid.UUID, day: datetime) -> dict[str, float]:
    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)

    def window(model: Any) -> Any:
        return (model.organization_id == organization_id, model.created_at >= start, model.created_at < end)

    llm = db.execute(
        select(
            func.coalesce(func.sum(ModelUsage.input_tokens + ModelUsage.output_tokens), 0),
            func.sum(ModelUsage.cost_usd),
            func.count(ModelUsage.id),
        ).where(*window(ModelUsage))
    ).one()
    compute = db.execute(
        select(
            func.coalesce(func.sum(ComputeUsage.runtime_seconds), 0.0),
            func.sum(ComputeUsage.cost_usd),
            func.coalesce(func.sum(ComputeUsage.gpu_seconds), 0.0),
        ).where(*window(ComputeUsage))
    ).one()
    storage = db.scalar(
        select(func.coalesce(func.sum(StorageUsage.bytes), 0)).where(
            StorageUsage.organization_id == organization_id, StorageUsage.created_at < end
        )
    )
    metrics: dict[str, tuple[float, str, float | None]] = {
        "llm_tokens": (float(llm[0] or 0), "tokens", float(llm[1]) if llm[1] is not None else None),
        "llm_requests": (float(llm[2] or 0), "requests", None),
        "compute_seconds": (float(compute[0] or 0.0), "seconds", float(compute[1]) if compute[1] is not None else None),
        "gpu_seconds": (float(compute[2] or 0.0), "seconds", None),
        "storage_bytes": (float(storage or 0), "bytes", None),
    }
    for metric, (quantity, unit, cost) in metrics.items():
        stmt = insert(UsageRecord).values(
            id=uuid.uuid4(),
            organization_id=organization_id,
            metric=metric,
            quantity=quantity,
            unit=unit,
            cost_usd=cost,
            period_start=start,
            period_end=end,
        )
        db.execute(
            stmt.on_conflict_do_update(
                constraint="uq_usage_records_org_metric_period",
                set_={"quantity": stmt.excluded.quantity, "cost_usd": stmt.excluded.cost_usd},
            )
        )
    return {k: v[0] for k, v in metrics.items()}


def subscription(db: Session, organization_id: uuid.UUID) -> dict[str, Any]:
    sub = db.scalar(select(Subscription).where(Subscription.organization_id == organization_id))
    plan = db.scalar(select(Plan).where(Plan.key == sub.plan_key)) if sub else None
    return {
        "provider": provider().name,
        "plan": {"key": plan.key, "name": plan.name, "limits": plan.limits} if plan else DEFAULT_PLAN,
        "status": sub.status if sub else "none",
        "current_period_end": sub.current_period_end.isoformat() if sub and sub.current_period_end else None,
        "note": "Billing provider 'none': usage is metered and reported here; no invoices are issued.",
    }


def usage_records(db: Session, organization_id: uuid.UUID, *, days: int = 30) -> list[UsageRecord]:
    since = utcnow() - timedelta(days=max(1, min(days, 366)))
    return list(
        db.scalars(
            select(UsageRecord)
            .where(UsageRecord.organization_id == organization_id, UsageRecord.period_start >= since)
            .order_by(UsageRecord.period_start, UsageRecord.metric)
        ).all()
    )
