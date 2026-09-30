"""Organization quotas: limits from organization settings, the subscription's plan and per-subscription
overrides (the most restrictive wins), checked against live usage.

Quota keys: ``max_agents``, ``max_concurrent_experiments`` (compute jobs QUEUED/PROVISIONING/RUNNING),
``max_llm_spend_usd`` and ``max_compute_spend_usd`` (current calendar month, UTC), ``max_storage_bytes``
(net stored bytes), ``max_research_jobs`` (non-terminal research tasks) and ``max_missions``
(non-archived missions).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.errors import ValidationFailed
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.audit import AuditAction, audit
from aegis_api.lab.core.errors import QuotaExceeded
from aegis_api.lab.core.locks import advisory_xact_lock
from aegis_api.lab.core.org_settings import DEFAULT_QUOTAS, get_org_settings
from aegis_api.lab.governance.schemas import QuotaOut, QuotasOut
from aegis_api.lab.models import (
    Agent,
    BillingPlan,
    ComputeJob,
    ComputeUsage,
    Mission,
    ModelUsage,
    ResearchTask,
    StorageUsage,
    Subscription,
)
from engines.lab.states import ExecutionStatus, MissionStatus, ResearchTaskStatus

Unit = Literal["count", "usd", "bytes"]
Window = Literal["current", "calendar_month", "total"]


@dataclass(frozen=True)
class QuotaSpec:
    key: str
    unit: Unit
    window: Window
    description: str


QUOTA_SPECS: dict[str, QuotaSpec] = {
    spec.key: spec
    for spec in (
        QuotaSpec("max_agents", "count", "current", "Configured (non-archived) agents"),
        QuotaSpec("max_concurrent_experiments", "count", "current", "Compute jobs queued, provisioning or running"),
        QuotaSpec("max_llm_spend_usd", "usd", "calendar_month", "Model spend this calendar month (UTC)"),
        QuotaSpec("max_compute_spend_usd", "usd", "calendar_month", "Compute spend this calendar month (UTC)"),
        QuotaSpec("max_storage_bytes", "bytes", "total", "Net stored bytes (artifacts, datasets, outputs)"),
        QuotaSpec("max_research_jobs", "count", "current", "Research tasks that are not finished"),
        QuotaSpec("max_missions", "count", "current", "Missions that are not archived"),
    )
}
ACTIVE_SUBSCRIPTION_STATUSES = ("active", "trialing", "past_due")
_ACTIVE_JOB_STATUSES = (ExecutionStatus.QUEUED, ExecutionStatus.PROVISIONING, ExecutionStatus.RUNNING)
_TERMINAL_RESEARCH = (ResearchTaskStatus.COMPLETED, ResearchTaskStatus.FAILED, ResearchTaskStatus.CANCELLED)


@dataclass(frozen=True)
class QuotaStatus:
    key: str
    limit: Decimal | None
    current: Decimal
    remaining: Decimal | None
    sources: dict[str, Decimal | None]

    @property
    def utilization(self) -> float | None:
        if self.limit is None:
            return None
        if self.limit == 0:
            return 1.0
        return float(self.current / self.limit)


def month_start(moment: datetime | None = None) -> datetime:
    now = (moment or utcnow()).astimezone(UTC)
    return datetime(now.year, now.month, 1, tzinfo=UTC)


def _number(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except ArithmeticError:
        return None
    return number if number.is_finite() else None


def _spec(key: str) -> QuotaSpec:
    try:
        return QUOTA_SPECS[key]
    except KeyError as exc:
        raise ValidationFailed(f"Unknown quota '{key}'. Known quotas: {', '.join(QUOTA_SPECS)}") from exc


def active_subscription(db: Session, organization_id: uuid.UUID) -> Subscription | None:
    return db.scalar(
        select(Subscription)
        .where(
            Subscription.organization_id == organization_id,
            Subscription.status.in_(ACTIVE_SUBSCRIPTION_STATUSES),
        )
        .order_by(Subscription.created_at.desc())
        .limit(1)
    )


def _limit_sources(db: Session, organization_id: uuid.UUID) -> tuple[dict[str, dict[str, Decimal | None]], str | None]:
    """Per key: {organization, plan, override} limits (None = no limit from that source)."""
    org_quotas = get_org_settings(db, organization_id).quotas or {}
    subscription = active_subscription(db, organization_id)
    plan_quotas: dict[str, Any] = {}
    override: dict[str, Any] = {}
    plan_key: str | None = None
    if subscription is not None:
        plan_key = subscription.plan_key
        plan = db.scalar(select(BillingPlan).where(BillingPlan.key == subscription.plan_key))
        plan_quotas = (plan.quotas or {}) if plan is not None else {}
        override = subscription.quotas_override or {}
    sources: dict[str, dict[str, Decimal | None]] = {}
    for key in QUOTA_SPECS:
        org_value = org_quotas[key] if key in org_quotas else DEFAULT_QUOTAS.get(key)
        per_key: dict[str, Decimal | None] = {"organization": _number(org_value)}
        if subscription is not None:
            per_key["plan"] = _number(plan_quotas.get(key))
            per_key["override"] = _number(override.get(key))
        sources[key] = per_key
    return sources, plan_key


def _effective(sources: dict[str, Decimal | None]) -> Decimal | None:
    values = [v for v in sources.values() if v is not None]
    return min(values) if values else None


def current_usage(db: Session, organization_id: uuid.UUID, key: str, *, now: datetime | None = None) -> Decimal:
    """Live usage for one quota key (RLS-scoped; organization filter kept for defense in depth)."""
    _spec(key)
    org = organization_id
    value: Any
    if key == "max_agents":
        value = db.scalar(
            select(func.count()).select_from(Agent).where(Agent.organization_id == org, Agent.status != "archived")
        )
    elif key == "max_concurrent_experiments":
        value = db.scalar(
            select(func.count())
            .select_from(ComputeJob)
            .where(ComputeJob.organization_id == org, ComputeJob.status.in_(_ACTIVE_JOB_STATUSES))
        )
    elif key == "max_llm_spend_usd":
        value = db.scalar(
            select(func.coalesce(func.sum(ModelUsage.cost_usd), 0)).where(
                ModelUsage.organization_id == org, ModelUsage.created_at >= month_start(now)
            )
        )
    elif key == "max_compute_spend_usd":
        value = db.scalar(
            select(func.coalesce(func.sum(ComputeUsage.cost_usd), 0)).where(
                ComputeUsage.organization_id == org, ComputeUsage.created_at >= month_start(now)
            )
        )
    elif key == "max_storage_bytes":
        value = db.scalar(
            select(func.coalesce(func.sum(StorageUsage.bytes_delta), 0)).where(StorageUsage.organization_id == org)
        )
        value = max(int(value or 0), 0)
    elif key == "max_research_jobs":
        value = db.scalar(
            select(func.count())
            .select_from(ResearchTask)
            .where(ResearchTask.organization_id == org, ResearchTask.status.not_in(_TERMINAL_RESEARCH))
        )
    else:  # max_missions
        value = db.scalar(
            select(func.count())
            .select_from(Mission)
            .where(Mission.organization_id == org, Mission.status != MissionStatus.ARCHIVED)
        )
    return Decimal(str(value or 0))


def quota_status(db: Session, organization_id: uuid.UUID, key: str) -> QuotaStatus:
    sources, _plan = _limit_sources(db, organization_id)
    limit = _effective(sources[_spec(key).key])
    current = current_usage(db, organization_id, key)
    remaining = None if limit is None else max(limit - current, Decimal(0))
    return QuotaStatus(key=key, limit=limit, current=current, remaining=remaining, sources=sources[key])


def check_quota(
    db: Session, organization_id: uuid.UUID, key: str, *, increment: Decimal | float | int = 1
) -> QuotaStatus:
    """Raise :class:`QuotaExceeded` (details ``{key, limit, current}``) when ``current + increment > limit``.

    ``increment`` is in the quota's unit: a count, USD (estimated spend) or bytes. Concurrent checks for the
    same key are serialized with a transaction-scoped advisory lock, so check-then-insert in the caller's
    transaction cannot overshoot the limit.
    """
    spec = _spec(key)
    amount = _number(increment)
    if amount is None or amount < 0:
        raise ValidationFailed("increment must be a non-negative number")
    advisory_xact_lock(db, f"quota:{organization_id}:{spec.key}")
    status = quota_status(db, organization_id, spec.key)
    if status.limit is not None and status.current + amount > status.limit:
        raise QuotaExceeded(
            f"Organization quota '{spec.key}' reached ({_fmt(status.current, spec.unit)} of "
            f"{_fmt(status.limit, spec.unit)})",
            details={
                "key": spec.key,
                "limit": float(status.limit),
                "current": float(status.current),
                "requested": float(amount),
                "unit": spec.unit,
            },
        )
    return status


def _fmt(value: Decimal, unit: Unit) -> str:
    if unit == "usd":
        return f"USD {value:.2f}"
    if unit == "bytes":
        return f"{int(value)} bytes"
    return str(int(value))


# ---------------------------------------------------------------------------------------------
# API views
# ---------------------------------------------------------------------------------------------
def quotas_view(db: Session, actor: Actor) -> QuotasOut:
    sources, plan_key = _limit_sources(db, actor.organization_id)
    items: list[QuotaOut] = []
    for key, spec in QUOTA_SPECS.items():
        limit = _effective(sources[key])
        current = current_usage(db, actor.organization_id, key)
        remaining = None if limit is None else max(limit - current, Decimal(0))
        utilization = None if limit is None else (1.0 if limit == 0 else float(current / limit))
        items.append(
            QuotaOut(
                key=key,
                unit=spec.unit,
                window=spec.window,
                limit=None if limit is None else float(limit),
                current=float(current),
                remaining=None if remaining is None else float(remaining),
                utilization=utilization,
                sources={k: (None if v is None else float(v)) for k, v in sources[key].items()},
            )
        )
    return QuotasOut(plan_key=plan_key, items=items)


def _validated_quota_value(key: str, value: Any) -> int | float | None:
    spec = _spec(key)
    if value is None:
        return None
    number = _number(value)
    if number is None:
        raise ValidationFailed(f"Quota '{key}' must be a number or null")
    if number < 0:
        raise ValidationFailed(f"Quota '{key}' must be non-negative")
    if spec.unit in ("count", "bytes"):
        if number != number.to_integral_value():
            raise ValidationFailed(f"Quota '{key}' must be a whole number")
        return int(number)
    return float(number)


def update_quotas(db: Session, actor: Actor, quotas: dict[str, Any]) -> QuotasOut:
    """Update organization quota limits (``org:manage``, human). ``None`` restores the default."""
    actor.require("org:manage")
    actor.require_human("changing organization quotas")
    if not quotas:
        raise ValidationFailed("Provide at least one quota to update")
    cleaned = {key: _validated_quota_value(key, value) for key, value in quotas.items()}
    settings_row = get_org_settings(db, actor.organization_id)
    before = dict(settings_row.quotas or {})
    after = dict(before)
    for key, value in cleaned.items():
        if value is None:
            after[key] = DEFAULT_QUOTAS.get(key)
        else:
            after[key] = value
    if after != before:
        settings_row.quotas = after
        db.flush()
        audit(
            db,
            actor,
            AuditAction.QUOTA_CHANGED,
            "organization_settings",
            settings_row.id,
            before={k: before.get(k) for k in cleaned},
            after={k: after.get(k) for k in cleaned},
        )
    return quotas_view(db, actor)
