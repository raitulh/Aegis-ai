"""Billing domain (provider-agnostic): plans, subscriptions, rolled-up usage records and invoice references.

Business logic depends on these tables and the ``BillingProvider`` interface only — never on a specific
payment processor. A Stripe (or other) adapter can later sync ``usage_records`` and write ``invoice_references``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Float, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from aegis_api.db.base import Base, CreatedMixin, IdMixin, OrgMixin, TimestampMixin


class Plan(IdMixin, TimestampMixin, Base):
    """Global plan catalogue (reference data)."""

    __tablename__ = "plans"

    key: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    limits: Mapped[dict[str, Any]] = mapped_column(default=dict)
    price: Mapped[dict[str, Any]] = mapped_column(default=dict)


class Subscription(IdMixin, TimestampMixin, OrgMixin, Base):
    __tablename__ = "subscriptions"
    __table_args__ = (UniqueConstraint("organization_id", name="uq_subscriptions_org"),)

    plan_key: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), default="active")  # trialing | active | past_due | cancelled
    provider: Mapped[str] = mapped_column(String(24), default="none")
    provider_ref: Mapped[str | None] = mapped_column(String(255))
    current_period_start: Mapped[datetime | None] = mapped_column(nullable=True)
    current_period_end: Mapped[datetime | None] = mapped_column(nullable=True)
    cancel_at: Mapped[datetime | None] = mapped_column(nullable=True)


class UsageRecord(IdMixin, CreatedMixin, OrgMixin, Base):
    """Daily roll-up of metered usage per metric (llm_tokens, llm_cost_usd, compute_seconds, storage_bytes...)."""

    __tablename__ = "usage_records"
    __table_args__ = (
        UniqueConstraint("organization_id", "metric", "period_start", name="uq_usage_records_org_metric_period"),
        Index("ix_usage_records_org_period", "organization_id", "period_start"),
    )

    metric: Mapped[str] = mapped_column(String(48))
    quantity: Mapped[float] = mapped_column(Float, default=0.0)
    unit: Mapped[str] = mapped_column(String(24))
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    period_start: Mapped[datetime]
    period_end: Mapped[datetime]
    reported_at: Mapped[datetime | None] = mapped_column(nullable=True)
    provider_ref: Mapped[str | None] = mapped_column(String(255))


class InvoiceReference(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "invoice_references"
    __table_args__ = (UniqueConstraint("provider", "provider_invoice_id", name="uq_invoice_references_provider_id"),)

    provider: Mapped[str] = mapped_column(String(24))
    provider_invoice_id: Mapped[str] = mapped_column(String(255))
    amount: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(8), default="usd")
    status: Mapped[str] = mapped_column(String(24))
    period_start: Mapped[datetime | None] = mapped_column(nullable=True)
    period_end: Mapped[datetime | None] = mapped_column(nullable=True)
    url: Mapped[str | None] = mapped_column(String(1000))
