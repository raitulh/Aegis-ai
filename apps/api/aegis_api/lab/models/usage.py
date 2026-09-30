"""Model configuration & usage ledgers, storage usage, and the billing abstraction."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from aegis_api.db.base import Base, CreatedMixin, IdMixin, OrgMixin, TimestampMixin
from aegis_api.lab.models._common import MONEY, money_column


class ModelConfig(IdMixin, TimestampMixin, Base):
    """Organization model routing overrides and prices. Environment configuration provides the defaults;
    rows here refine routing per task/tier. NULL organization = platform-wide row."""

    __tablename__ = "model_configs"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "provider_kind",
            "model",
            "tier",
            name="uq_model_configs_route",
            postgresql_nulls_not_distinct=True,
        ),
    )

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    provider_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("providers.id", ondelete="SET NULL"), nullable=True
    )
    provider_kind: Mapped[str] = mapped_column(String(24))
    model: Mapped[str] = mapped_column(String(160))
    tier: Mapped[str] = mapped_column(String(24))  # fast|default|reasoning|deep_research|embedding
    task_types: Mapped[list[str]] = mapped_column(default=list)  # empty = any task
    priority: Mapped[int] = mapped_column(Integer, default=100)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    max_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    temperature: Mapped[float | None] = mapped_column(nullable=True)
    input_per_mtok_usd: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    output_per_mtok_usd: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    cached_input_per_mtok_usd: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    context_window: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # structured_output, tools, code_execution, search, streaming, background
    capabilities: Mapped[dict[str, Any]] = mapped_column(default=dict)


class ModelUsage(IdMixin, CreatedMixin, OrgMixin, Base):
    """Append-only ledger of every model call routed by the LLM gateway (success or failure)."""

    __tablename__ = "model_usage"
    __table_args__ = (
        Index("ix_model_usage_org_created", "organization_id", "created_at"),
        Index("ix_model_usage_mission", "mission_id"),
        Index("ix_model_usage_agent_run", "agent_run_id"),
    )

    project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    mission_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    research_task_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(160))
    model_version: Mapped[str | None] = mapped_column(String(160))
    request_id: Mapped[str | None] = mapped_column(String(160))
    trace_id: Mapped[str | None] = mapped_column(String(64))
    task_type: Mapped[str] = mapped_column(String(48))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)
    thinking_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[Decimal] = money_column()
    cost_estimated: Mapped[bool] = mapped_column(Boolean, default=True)
    cost_basis: Mapped[str | None] = mapped_column(String(160))
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error_code: Mapped[str | None] = mapped_column(String(64))
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    route_reason: Mapped[str | None] = mapped_column(String(300))


class StorageUsage(IdMixin, CreatedMixin, OrgMixin, Base):
    """Append-only storage ledger (positive on write, negative on purge)."""

    __tablename__ = "storage_usage"
    __table_args__ = (Index("ix_storage_usage_org_created", "organization_id", "created_at"),)

    project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    artifact_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    dataset_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    bytes_delta: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[str] = mapped_column(String(32))  # artifact_upload|dataset_upload|execution_output|purge


class BillingPlan(IdMixin, TimestampMixin, Base):
    """Global plan catalog (not tenant data)."""

    __tablename__ = "billing_plans"

    key: Mapped[str] = mapped_column(String(48), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    quotas: Mapped[dict[str, Any]] = mapped_column(default=dict)
    prices: Mapped[dict[str, Any]] = mapped_column(default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Subscription(IdMixin, TimestampMixin, OrgMixin, Base):
    __tablename__ = "subscriptions"
    __table_args__ = (
        Index(
            "uq_subscriptions_org_active",
            "organization_id",
            unique=True,
            postgresql_where="status in ('active','trialing','past_due')",
        ),
    )

    plan_key: Mapped[str] = mapped_column(String(48))
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|trialing|past_due|cancelled
    provider: Mapped[str] = mapped_column(String(24), default="none")
    external_id: Mapped[str | None] = mapped_column(String(200))
    current_period_start: Mapped[datetime]
    current_period_end: Mapped[datetime]
    quotas_override: Mapped[dict[str, Any]] = mapped_column(default=dict)
    cancelled_at: Mapped[datetime | None] = mapped_column(nullable=True)


class UsageRecord(IdMixin, TimestampMixin, OrgMixin, Base):
    """Billing-grade aggregate per meter and period (rolled up from the ledgers)."""

    __tablename__ = "usage_records"
    __table_args__ = (UniqueConstraint("organization_id", "meter", "period_start", name="uq_usage_records_period"),)

    meter: Mapped[str] = mapped_column(String(48))
    period_start: Mapped[datetime]
    period_end: Mapped[datetime]
    quantity: Mapped[Decimal] = money_column()
    cost_usd: Mapped[Decimal] = money_column()
    source_counts: Mapped[dict[str, Any]] = mapped_column(default=dict)
    finalized: Mapped[bool] = mapped_column(Boolean, default=False)
    external_ref: Mapped[str | None] = mapped_column(String(200))


class InvoiceReference(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "invoice_references"

    subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(24))
    external_invoice_id: Mapped[str] = mapped_column(String(200))
    period_start: Mapped[datetime]
    period_end: Mapped[datetime]
    amount_usd: Mapped[Decimal] = money_column()
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    status: Mapped[str] = mapped_column(String(16), default="open")
    url: Mapped[str | None] = mapped_column(String(1000))
