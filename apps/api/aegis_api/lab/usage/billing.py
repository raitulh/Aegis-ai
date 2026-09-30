"""Billing abstraction: plan catalog, subscriptions, usage roll-ups and invoices.

The platform meters usage into ``usage_records`` (one row per organization × meter × period). A
:class:`BillingProvider` pushes those records to an external billing system and creates invoices. The
built-in :class:`LocalLedgerBillingProvider` (``BILLING_PROVIDER=none``) makes no external calls: the local
ledger is the system of record and invoices are recorded as ``draft`` references. Finalized usage records
are never modified.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.db.base import utcnow
from aegis_api.errors import NotFound, ServiceUnavailable, ValidationFailed
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.audit import audit
from aegis_api.lab.core.locks import advisory_xact_lock
from aegis_api.lab.governance.quotas import ACTIVE_SUBSCRIPTION_STATUSES, active_subscription
from aegis_api.lab.models import (
    BillingPlan,
    ComputeJob,
    ComputeUsage,
    InvoiceReference,
    ModelUsage,
    ResearchTask,
    StorageUsage,
    Subscription,
    UsageRecord,
)
from aegis_api.lab.usage.schemas import BillingPlanOut, InvoiceOut, SubscriptionOut, UsageRecordOut
from aegis_api.lab.usage.service import month_bounds, parse_period
from aegis_api.schemas.common import Page, PageParams

log = structlog.get_logger("aegis.lab.billing")

DEFAULT_PLAN_KEY = "free"
METERS: tuple[str, ...] = (
    "llm_cost_usd",
    "compute_cost_usd",
    "storage_gb_month",
    "experiments",
    "research_tasks",
    "model_tokens",
)
_GIB = Decimal(1024**3)
_MICRO = Decimal("0.000001")
_EXPERIMENT_PURPOSES = ("experiment", "reproduction")

# Built-in plan catalog (seeded into ``billing_plans``; prices are list prices in the billing currency).
# Quota keys match ``aegis_api.lab.governance.quotas.QUOTA_SPECS``; an omitted key means "no plan limit".
BUILTIN_PLANS: tuple[dict[str, Any], ...] = (
    {
        "key": "free",
        "name": "Free",
        "description": "Evaluate the lab: small missions, one concurrent experiment, CPU only.",
        "quotas": {
            "max_agents": 10,
            "max_concurrent_experiments": 1,
            "max_llm_spend_usd": 25,
            "max_compute_spend_usd": 10,
            "max_storage_bytes": 5 * 1024**3,
            "max_research_jobs": 5,
            "max_missions": 10,
        },
        "prices": {
            "base_monthly_usd": 0,
            "llm_markup_pct": 0,
            "compute_markup_pct": 0,
            "storage_gb_month_usd": 0,
            "per_experiment_usd": 0,
            "per_research_task_usd": 0,
        },
    },
    {
        "key": "team",
        "name": "Team",
        "description": "Research teams: parallel experiments, larger budgets and storage.",
        "quotas": {
            "max_agents": 100,
            "max_concurrent_experiments": 8,
            "max_llm_spend_usd": 2000,
            "max_compute_spend_usd": 2000,
            "max_storage_bytes": 200 * 1024**3,
            "max_research_jobs": 50,
            "max_missions": 200,
        },
        "prices": {
            "base_monthly_usd": 499,
            "llm_markup_pct": 0,
            "compute_markup_pct": 0,
            "storage_gb_month_usd": 0.05,
            "per_experiment_usd": 0,
            "per_research_task_usd": 0,
        },
    },
    {
        "key": "enterprise",
        "name": "Enterprise",
        "description": "Contracted limits; organization quotas and subscription overrides apply.",
        "quotas": {},
        "prices": {
            "base_monthly_usd": 0,
            "llm_markup_pct": 0,
            "compute_markup_pct": 0,
            "storage_gb_month_usd": 0,
            "per_experiment_usd": 0,
            "per_research_task_usd": 0,
        },
    },
)


# ---------------------------------------------------------------------------------------------
# Provider protocol
# ---------------------------------------------------------------------------------------------
@runtime_checkable
class BillingProvider(Protocol):
    """Integration point for an external billing system."""

    name: str

    def sync_usage(self, records: Sequence[UsageRecord]) -> int:
        """Push usage records to the billing system; returns how many were accepted."""
        ...

    def create_invoice(
        self, db: Session, organization_id: uuid.UUID, period_start: datetime, period_end: datetime
    ) -> InvoiceReference:
        """Create (or return the existing) invoice for the period and record its reference."""
        ...


class LocalLedgerBillingProvider:
    """``BILLING_PROVIDER=none``: no external calls. The local ledger is authoritative and invoices are
    recorded as ``draft`` references whose amount is computed from ``usage_records`` + the plan's base fee."""

    name = "none"

    def sync_usage(self, records: Sequence[UsageRecord]) -> int:
        return len(records)

    def create_invoice(
        self, db: Session, organization_id: uuid.UUID, period_start: datetime, period_end: datetime
    ) -> InvoiceReference:
        advisory_xact_lock(db, f"billing:invoice:{organization_id}:{period_start.isoformat()}")
        records = rollup_usage_records(db, organization_id, period_start, period_end)
        subscription = active_subscription(db, organization_id)
        plan = _plan(db, subscription.plan_key if subscription else DEFAULT_PLAN_KEY)
        base = _money((plan.prices or {}).get("base_monthly_usd") if plan else 0) * _months(period_start, period_end)
        amount = (sum((r.cost_usd for r in records), Decimal("0")) + base).quantize(_MICRO)
        existing = db.scalar(
            select(InvoiceReference).where(
                InvoiceReference.organization_id == organization_id,
                InvoiceReference.provider == self.name,
                InvoiceReference.period_start == period_start,
                InvoiceReference.period_end == period_end,
                InvoiceReference.status != "void",
            )
        )
        if existing is not None:
            if existing.status == "draft" and existing.amount_usd != amount:
                existing.amount_usd = amount
                db.flush()
            invoice = existing
        else:
            invoice = InvoiceReference(
                organization_id=organization_id,
                subscription_id=subscription.id if subscription else None,
                provider=self.name,
                external_invoice_id=f"local-{period_start:%Y%m}-{uuid.uuid4().hex[:12]}",
                period_start=period_start,
                period_end=period_end,
                amount_usd=amount,
                currency=get_settings().billing_currency,
                status="draft",
            )
            db.add(invoice)
            db.flush()
        if period_end <= utcnow():
            # The period is over: freeze its usage records (they are never modified afterwards).
            db.execute(
                update(UsageRecord)
                .where(
                    UsageRecord.organization_id == organization_id,
                    UsageRecord.period_start == period_start,
                    UsageRecord.finalized.is_(False),
                )
                .values(finalized=True, external_ref=invoice.external_invoice_id, updated_at=utcnow())
                .execution_options(synchronize_session=False)
            )
        self.sync_usage(records)
        return invoice


def get_billing_provider() -> BillingProvider:
    name = get_settings().billing_provider
    if name == "none":
        return LocalLedgerBillingProvider()
    raise ServiceUnavailable(f"Billing provider '{name}' is not available in this deployment")


# ---------------------------------------------------------------------------------------------
# Catalog & seeding
# ---------------------------------------------------------------------------------------------
def seed_billing_plans(session: Session) -> int:
    """Idempotently upsert the built-in plan catalog (runs at start-up on the owner connection)."""
    for plan in BUILTIN_PLANS:
        stmt = insert(BillingPlan).values(
            id=uuid.uuid4(),
            key=plan["key"],
            name=plan["name"],
            description=plan["description"],
            quotas=plan["quotas"],
            prices=plan["prices"],
            is_active=True,
        )
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=[BillingPlan.key],
                set_={
                    "name": stmt.excluded.name,
                    "description": stmt.excluded.description,
                    "quotas": stmt.excluded.quotas,
                    "prices": stmt.excluded.prices,
                    "updated_at": func.now(),
                },
            )
        )
    session.flush()
    return len(BUILTIN_PLANS)


def _plan(db: Session, key: str) -> BillingPlan | None:
    return db.scalar(select(BillingPlan).where(BillingPlan.key == key))


def list_plans(db: Session) -> list[BillingPlanOut]:
    rows = db.scalars(select(BillingPlan).where(BillingPlan.is_active.is_(True)).order_by(BillingPlan.key)).all()
    return [BillingPlanOut.model_validate(p) for p in rows]


def _plan_out(db: Session, key: str) -> BillingPlanOut | None:
    plan = _plan(db, key)
    return BillingPlanOut.model_validate(plan) if plan is not None else None


# ---------------------------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------------------------
def subscription_view(db: Session, actor: Actor) -> SubscriptionOut:
    subscription = active_subscription(db, actor.organization_id)
    if subscription is None:
        start, end = month_bounds()
        return SubscriptionOut(
            plan_key=DEFAULT_PLAN_KEY,
            plan=_plan_out(db, DEFAULT_PLAN_KEY),
            status="active",
            provider=get_settings().billing_provider,
            current_period_start=start,
            current_period_end=end,
            is_default=True,
        )
    return _subscription_out(db, subscription)


def _subscription_out(db: Session, subscription: Subscription) -> SubscriptionOut:
    return SubscriptionOut(
        id=str(subscription.id),
        plan_key=subscription.plan_key,
        plan=_plan_out(db, subscription.plan_key),
        status=subscription.status,
        provider=subscription.provider,
        external_id=subscription.external_id,
        current_period_start=subscription.current_period_start,
        current_period_end=subscription.current_period_end,
        quotas_override=subscription.quotas_override or {},
        cancelled_at=subscription.cancelled_at,
        is_default=False,
    )


def change_subscription(db: Session, actor: Actor, plan_key: str) -> SubscriptionOut:
    """Switch the organization to ``plan_key`` (``billing:manage``, human). The previous subscription is
    cancelled and a new one starts for the current calendar month; quota overrides carry over."""
    actor.require("billing:manage")
    actor.require_human("changing the subscription")
    plan = _plan(db, plan_key)
    if plan is None or not plan.is_active:
        raise NotFound(f"Billing plan '{plan_key}' not found")
    advisory_xact_lock(db, f"billing:subscription:{actor.organization_id}")
    current = active_subscription(db, actor.organization_id)
    if current is not None and current.plan_key == plan_key:
        return _subscription_out(db, current)
    now = utcnow()
    overrides: dict[str, Any] = {}
    previous_plan = current.plan_key if current is not None else None
    if current is not None:
        overrides = dict(current.quotas_override or {})
        db.execute(
            update(Subscription)
            .where(
                Subscription.organization_id == actor.organization_id,
                Subscription.status.in_(ACTIVE_SUBSCRIPTION_STATUSES),
            )
            .values(status="cancelled", cancelled_at=now, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        db.expire(current)
    start, end = month_bounds(now)
    subscription = Subscription(
        organization_id=actor.organization_id,
        plan_key=plan_key,
        status="active",
        provider=get_billing_provider().name,
        current_period_start=start,
        current_period_end=end,
        quotas_override=overrides,
    )
    db.add(subscription)
    db.flush()
    audit(
        db,
        actor,
        "SUBSCRIPTION_CHANGED",
        "subscription",
        subscription.id,
        before={"plan_key": previous_plan},
        after={"plan_key": plan_key},
    )
    return _subscription_out(db, subscription)


# ---------------------------------------------------------------------------------------------
# Usage roll-up
# ---------------------------------------------------------------------------------------------
def _money(value: Any) -> Decimal:
    if value is None or isinstance(value, bool):
        return Decimal("0")
    try:
        amount = Decimal(str(value))
    except ArithmeticError:
        return Decimal("0")
    return amount if amount.is_finite() and amount >= 0 else Decimal("0")


def _months(start: datetime, end: datetime) -> Decimal:
    """Length of ``[start, end)`` in months: exactly 1 for a calendar month, else days / 30."""
    month_start, month_end = month_bounds(start)
    if start == month_start and end == month_end:
        return Decimal("1")
    return (Decimal(str((end - start).total_seconds())) / Decimal(30 * 86400)).quantize(_MICRO)


def _normalize_period(period_start: datetime, period_end: datetime) -> tuple[datetime, datetime]:
    start = period_start if period_start.tzinfo else period_start.replace(tzinfo=UTC)
    end = period_end if period_end.tzinfo else period_end.replace(tzinfo=UTC)
    if start >= end:
        raise ValidationFailed("period_start must be earlier than period_end")
    if end - start > timedelta(days=366):
        raise ValidationFailed("A billing period may span at most one year")
    return start, end


def compute_meters(
    db: Session, organization_id: uuid.UUID, period_start: datetime, period_end: datetime, prices: dict[str, Any]
) -> dict[str, tuple[Decimal, Decimal, dict[str, Any]]]:
    """meter → (quantity, cost_usd, source_counts) for ``[period_start, period_end)``."""
    org = organization_id
    llm_cost, llm_calls, tokens = db.execute(
        select(
            func.coalesce(func.sum(ModelUsage.cost_usd), 0),
            func.count(ModelUsage.id),
            func.coalesce(func.sum(ModelUsage.input_tokens + ModelUsage.output_tokens + ModelUsage.thinking_tokens), 0),
        ).where(
            ModelUsage.organization_id == org,
            ModelUsage.created_at >= period_start,
            ModelUsage.created_at < period_end,
        )
    ).one()
    compute_cost, compute_rows = db.execute(
        select(func.coalesce(func.sum(ComputeUsage.cost_usd), 0), func.count(ComputeUsage.id)).where(
            ComputeUsage.organization_id == org,
            ComputeUsage.created_at >= period_start,
            ComputeUsage.created_at < period_end,
        )
    ).one()
    stored_before = db.scalar(
        select(func.coalesce(func.sum(StorageUsage.bytes_delta), 0)).where(
            StorageUsage.organization_id == org, StorageUsage.created_at < period_start
        )
    )
    stored_after, storage_rows = db.execute(
        select(func.coalesce(func.sum(StorageUsage.bytes_delta), 0), func.count(StorageUsage.id)).where(
            StorageUsage.organization_id == org, StorageUsage.created_at < period_end
        )
    ).one()
    experiments = db.scalar(
        select(func.count(ComputeJob.id)).where(
            ComputeJob.organization_id == org,
            ComputeJob.purpose.in_(_EXPERIMENT_PURPOSES),
            ComputeJob.created_at >= period_start,
            ComputeJob.created_at < period_end,
        )
    )
    research = db.scalar(
        select(func.count(ResearchTask.id)).where(
            ResearchTask.organization_id == org,
            ResearchTask.created_at >= period_start,
            ResearchTask.created_at < period_end,
        )
    )
    llm_total = Decimal(llm_cost or 0)
    compute_total = Decimal(compute_cost or 0)
    avg_bytes = (Decimal(max(int(stored_before or 0), 0)) + Decimal(max(int(stored_after or 0), 0))) / 2
    gb_months = (avg_bytes / _GIB * _months(period_start, period_end)).quantize(_MICRO)
    llm_markup = 1 + _money(prices.get("llm_markup_pct")) / 100
    compute_markup = 1 + _money(prices.get("compute_markup_pct")) / 100
    return {
        "llm_cost_usd": (llm_total, (llm_total * llm_markup).quantize(_MICRO), {"model_usage": int(llm_calls)}),
        "compute_cost_usd": (
            compute_total,
            (compute_total * compute_markup).quantize(_MICRO),
            {"compute_usage": int(compute_rows)},
        ),
        "storage_gb_month": (
            gb_months,
            (gb_months * _money(prices.get("storage_gb_month_usd"))).quantize(_MICRO),
            {
                "storage_usage": int(storage_rows),
                "bytes_at_start": int(stored_before or 0),
                "bytes_at_end": int(stored_after or 0),
            },
        ),
        "experiments": (
            Decimal(int(experiments or 0)),
            (Decimal(int(experiments or 0)) * _money(prices.get("per_experiment_usd"))).quantize(_MICRO),
            {"compute_jobs": int(experiments or 0)},
        ),
        "research_tasks": (
            Decimal(int(research or 0)),
            (Decimal(int(research or 0)) * _money(prices.get("per_research_task_usd"))).quantize(_MICRO),
            {"research_tasks": int(research or 0)},
        ),
        "model_tokens": (Decimal(int(tokens or 0)), Decimal("0"), {"model_usage": int(llm_calls)}),
    }


def rollup_usage_records(
    db: Session, organization_id: uuid.UUID, period_start: datetime, period_end: datetime
) -> list[UsageRecord]:
    """Idempotently upsert one ``usage_records`` row per meter for the period. Finalized rows are left
    untouched. Returns the period's records (all meters)."""
    start, end = _normalize_period(period_start, period_end)
    subscription = active_subscription(db, organization_id)
    plan = _plan(db, subscription.plan_key if subscription else DEFAULT_PLAN_KEY)
    prices = dict(plan.prices or {}) if plan is not None else {}
    meters = compute_meters(db, organization_id, start, end, prices)
    now = utcnow()
    for meter, (quantity, cost, sources) in meters.items():
        stmt = insert(UsageRecord).values(
            id=uuid.uuid4(),
            organization_id=organization_id,
            meter=meter,
            period_start=start,
            period_end=end,
            quantity=quantity,
            cost_usd=cost,
            source_counts=sources,
            finalized=False,
            created_at=now,
            updated_at=now,
        )
        db.execute(
            stmt.on_conflict_do_update(
                constraint="uq_usage_records_period",
                set_={
                    "period_end": stmt.excluded.period_end,
                    "quantity": stmt.excluded.quantity,
                    "cost_usd": stmt.excluded.cost_usd,
                    "source_counts": stmt.excluded.source_counts,
                    "updated_at": now,
                },
                where=UsageRecord.finalized.is_(False),
            )
        )
    db.flush()
    records = db.scalars(
        select(UsageRecord)
        .where(UsageRecord.organization_id == organization_id, UsageRecord.period_start == start)
        .order_by(UsageRecord.meter)
        .execution_options(populate_existing=True)
    ).all()
    return list(records)


def rollup_period(db: Session, actor: Actor, period: str | None) -> tuple[datetime, datetime, list[UsageRecord]]:
    actor.require("billing:manage")
    start, end = parse_period(period)
    return start, end, rollup_usage_records(db, actor.organization_id, start, end)


# ---------------------------------------------------------------------------------------------
# Listings & invoices
# ---------------------------------------------------------------------------------------------
def list_usage_records(
    db: Session, actor: Actor, params: PageParams, *, meter: str | None = None, period: str | None = None
) -> Page[UsageRecordOut]:
    stmt = select(UsageRecord).where(UsageRecord.organization_id == actor.organization_id)
    if meter:
        if meter not in METERS:
            raise ValidationFailed(f"meter must be one of {', '.join(METERS)}")
        stmt = stmt.where(UsageRecord.meter == meter)
    if period:
        start, _end = parse_period(period)
        stmt = stmt.where(UsageRecord.period_start == start)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(UsageRecord.period_start.desc(), UsageRecord.meter).limit(params.page_size).offset(params.offset)
    ).all()
    return Page.build([UsageRecordOut.model_validate(r) for r in rows], int(total), params)


def list_invoices(db: Session, actor: Actor, params: PageParams) -> Page[InvoiceOut]:
    stmt = select(InvoiceReference).where(InvoiceReference.organization_id == actor.organization_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(InvoiceReference.period_start.desc(), InvoiceReference.created_at.desc())
        .limit(params.page_size)
        .offset(params.offset)
    ).all()
    return Page.build([InvoiceOut.model_validate(r) for r in rows], int(total), params)


def create_invoice(db: Session, actor: Actor, period: str | None) -> InvoiceReference:
    """Create (or refresh the draft) invoice for a calendar month (``billing:manage``, human)."""
    actor.require("billing:manage")
    actor.require_human("creating invoices")
    start, end = parse_period(period)
    if start > utcnow():
        raise ValidationFailed("Cannot invoice a period that has not started")
    invoice = get_billing_provider().create_invoice(db, actor.organization_id, start, end)
    audit(
        db,
        actor,
        "INVOICE_CREATED",
        "invoice",
        invoice.id,
        after={
            "provider": invoice.provider,
            "period_start": invoice.period_start,
            "amount_usd": invoice.amount_usd,
            "status": invoice.status,
        },
    )
    return invoice
