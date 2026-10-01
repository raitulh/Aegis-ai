"""Usage ledger, subscriptions and billing provider events."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from aegis_api.db.base import Base, CreatedMixin, IdMixin, OrgMixin, TimestampMixin, utcnow


class UsageEvent(IdMixin, CreatedMixin, OrgMixin, Base):
    """Immutable, auditable usage record (UPDATE/DELETE blocked by trigger).

    Recording is idempotent: one row per (organization, metric, source_type, source_id), so a retried job
    can never double-bill."""

    __tablename__ = "usage_events"
    __table_args__ = (
        UniqueConstraint("organization_id", "metric", "source_type", "source_id", name="uq_usage_events_source"),
        Index("ix_usage_events_org_metric_time", "organization_id", "metric", "occurred_at"),
    )

    metric: Mapped[str] = mapped_column(String(48))
    quantity: Mapped[int] = mapped_column(BigInteger, default=1)
    source_type: Mapped[str] = mapped_column(String(32))
    source_id: Mapped[str] = mapped_column(String(80))
    occurred_at: Mapped[datetime] = mapped_column(default=utcnow)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", default=dict)


class Subscription(IdMixin, TimestampMixin, OrgMixin, Base):
    """The workspace's commercial plan. One active row per organization."""

    __tablename__ = "subscriptions"
    __table_args__ = (UniqueConstraint("organization_id", name="uq_subscriptions_org"),)

    plan: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24), default="active")  # active | trialing | past_due | canceled
    provider: Mapped[str] = mapped_column(String(24), default="none")
    provider_customer_id: Mapped[str | None] = mapped_column(String(120))
    provider_subscription_id: Mapped[str | None] = mapped_column(String(120))
    current_period_start: Mapped[datetime | None] = mapped_column(nullable=True)
    current_period_end: Mapped[datetime | None] = mapped_column(nullable=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(default=False)
    # Contracted overrides (enterprise): {"limits": {...}, "features": {...}, "addons": [...]}
    overrides: Mapped[dict[str, Any]] = mapped_column(default=dict)


class BillingEvent(IdMixin, CreatedMixin, Base):
    """Inbound billing-provider webhook events, stored once per provider event id (idempotent processing)."""

    __tablename__ = "billing_events"
    __table_args__ = (UniqueConstraint("provider", "provider_event_id", name="uq_billing_events_provider_event"),)

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(24))
    provider_event_id: Mapped[str] = mapped_column(String(120))
    type: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    processed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    error: Mapped[str | None] = mapped_column(String(500))
