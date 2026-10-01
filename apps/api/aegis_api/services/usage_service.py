"""Usage metering: an immutable, idempotent usage ledger and period aggregation.

Usage is recorded by the backend at the point the work is committed (never from frontend counters). Every
record names its source (``source_type`` + ``source_id``) and is unique per metric, so retries and
duplicate deliveries cannot double-count.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from aegis_api.models import UsageEvent

METRICS = {
    "audit_run": "Audit runs",
    "test_execution": "Test executions",
    "redteam_run": "Red-team runs",
    "runtime_event": "Runtime events",
    "evidence_export": "Evidence exports",
    "report_export": "Report exports",
}


def record(
    session: Session,
    organization_id: uuid.UUID,
    metric: str,
    *,
    quantity: int = 1,
    source_type: str,
    source_id: uuid.UUID | str,
    metadata: dict[str, Any] | None = None,
) -> None:
    if metric not in METRICS:
        raise ValueError(f"Unknown usage metric '{metric}'")
    session.execute(
        insert(UsageEvent)
        .values(
            organization_id=organization_id,
            metric=metric,
            quantity=int(quantity),
            source_type=source_type,
            source_id=str(source_id),
            meta=metadata or {},
        )
        .on_conflict_do_nothing(constraint="uq_usage_events_source")
    )


def period_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def totals(session: Session, organization_id: uuid.UUID, since: datetime) -> dict[str, int]:
    rows = session.execute(
        select(UsageEvent.metric, func.coalesce(func.sum(UsageEvent.quantity), 0))
        .where(UsageEvent.organization_id == organization_id, UsageEvent.occurred_at >= since)
        .group_by(UsageEvent.metric)
    ).all()
    return {metric: int(total) for metric, total in rows}


def total(session: Session, organization_id: uuid.UUID, metric: str, since: datetime) -> int:
    return int(
        session.scalar(
            select(func.coalesce(func.sum(UsageEvent.quantity), 0)).where(
                UsageEvent.organization_id == organization_id,
                UsageEvent.metric == metric,
                UsageEvent.occurred_at >= since,
            )
        )
        or 0
    )
