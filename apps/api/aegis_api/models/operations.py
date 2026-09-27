"""Agent traces, monitoring, alerts, notifications, integrations and webhooks."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aegis_api.db.base import Base, CreatedMixin, IdMixin, OrgMixin, TimestampMixin
from aegis_api.models.enums import AlertStatus, ConnectionStatus, DeliveryStatus


class AgentTrace(IdMixin, CreatedMixin, OrgMixin, Base):
    """Observable agent execution. Chain-of-thought is never stored — only event metadata and I/O."""

    __tablename__ = "agent_traces"
    __table_args__ = (
        UniqueConstraint("organization_id", "trace_id", name="uq_agent_traces_org_trace"),
        Index("ix_agent_traces_system_started", "system_id", "started_at"),
    )

    system_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_systems.id", ondelete="CASCADE"), index=True)
    audit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("audits.id", ondelete="SET NULL"), nullable=True)
    trace_id: Mapped[str] = mapped_column(String(80))
    session_id: Mapped[str | None] = mapped_column(String(80))
    name: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(16), default="ok")
    model: Mapped[str | None] = mapped_column(String(160))
    source: Mapped[str] = mapped_column(String(16), default="sdk")  # sdk | audit | demo
    started_at: Mapped[datetime]
    ended_at: Mapped[datetime | None] = mapped_column(nullable=True)
    span_count: Mapped[int] = mapped_column(Integer, default=0)
    tool_call_count: Mapped[int] = mapped_column(Integer, default=0)
    violation_count: Mapped[int] = mapped_column(Integer, default=0)
    risk_level: Mapped[str | None] = mapped_column(String(16))
    input_summary: Mapped[str | None] = mapped_column(Text)
    output_summary: Mapped[str | None] = mapped_column(Text)
    finding_ids: Mapped[list[str]] = mapped_column(default=list)

    events: Mapped[list[TraceEvent]] = relationship(
        back_populates="trace", order_by="TraceEvent.seq", cascade="all, delete-orphan"
    )


class TraceEvent(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "trace_events"

    trace_pk: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_traces.id", ondelete="CASCADE"), index=True)
    span_id: Mapped[str] = mapped_column(String(64))
    parent_span_id: Mapped[str | None] = mapped_column(String(64))
    seq: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(24))
    name: Mapped[str] = mapped_column(String(200))
    timestamp: Mapped[datetime]
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    model: Mapped[str | None] = mapped_column(String(160))
    prompt_metadata: Mapped[dict[str, Any]] = mapped_column(default=dict)
    attributes: Mapped[dict[str, Any]] = mapped_column(default=dict)
    input_preview: Mapped[str | None] = mapped_column(Text)
    output_preview: Mapped[str | None] = mapped_column(Text)
    policy_checks: Mapped[list[dict[str, Any]]] = mapped_column(default=list)
    finding_ids: Mapped[list[str]] = mapped_column(default=list)
    status: Mapped[str] = mapped_column(String(16), default="ok")

    trace: Mapped[AgentTrace] = relationship(back_populates="events")


class ToolCall(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "tool_calls"

    trace_pk: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_traces.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("trace_events.id", ondelete="CASCADE"), nullable=True)
    tool_name: Mapped[str] = mapped_column(String(120), index=True)
    arguments: Mapped[dict[str, Any]] = mapped_column(default=dict)  # redacted
    result_metadata: Mapped[dict[str, Any]] = mapped_column(default=dict)
    authorization: Mapped[str] = mapped_column(String(16))  # passed | failed | not_required
    authorized_by: Mapped[str | None] = mapped_column(String(64))
    requires_human_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    human_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    sensitive_data_detected: Mapped[bool] = mapped_column(Boolean, default=False)
    sensitive_types: Mapped[list[str]] = mapped_column(default=list)
    allowed: Mapped[bool] = mapped_column(Boolean, default=True)
    risk_level: Mapped[str | None] = mapped_column(String(16))
    control_ref: Mapped[str | None] = mapped_column(String(32))
    violations: Mapped[list[dict[str, Any]]] = mapped_column(default=list)


class Monitor(IdMixin, TimestampMixin, OrgMixin, Base):
    __tablename__ = "monitors"

    system_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_systems.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sampling_rate: Mapped[float] = mapped_column(Float, default=0.1)
    evaluators: Mapped[list[str]] = mapped_column(default=list)
    # [{"metric": "pii_incidents", "window_minutes": 60, "operator": ">", "threshold": 0, "severity": "critical"}]
    alert_conditions: Mapped[list[dict[str, Any]]] = mapped_column(default=list)
    webhook_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("webhooks.id", ondelete="SET NULL"), nullable=True)


class MonitoringEvent(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "monitoring_events"
    __table_args__ = (Index("ix_monitoring_events_system_received", "system_id", "received_at"),)

    system_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_systems.id", ondelete="CASCADE"), index=True)
    monitor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("monitors.id", ondelete="SET NULL"), nullable=True)
    external_request_id: Mapped[str | None] = mapped_column(String(120))
    received_at: Mapped[datetime]
    sampled: Mapped[bool] = mapped_column(Boolean, default=False)
    evaluated: Mapped[bool] = mapped_column(Boolean, default=False)
    model_version: Mapped[str | None] = mapped_column(String(160))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    input_redacted: Mapped[str | None] = mapped_column(Text)
    output_redacted: Mapped[str | None] = mapped_column(Text)
    results: Mapped[dict[str, Any]] = mapped_column(default=dict)
    policy_violation: Mapped[bool] = mapped_column(Boolean, default=False)
    hallucination_alert: Mapped[bool] = mapped_column(Boolean, default=False)
    safety_alert: Mapped[bool] = mapped_column(Boolean, default=False)
    pii_incident: Mapped[bool] = mapped_column(Boolean, default=False)


class Alert(IdMixin, TimestampMixin, OrgMixin, Base):
    __tablename__ = "alerts"
    __table_args__ = (Index("ix_alerts_org_status", "organization_id", "status"),)

    system_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_systems.id", ondelete="CASCADE"), nullable=True, index=True
    )
    monitor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("monitors.id", ondelete="SET NULL"), nullable=True)
    severity: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(300))
    event_type: Mapped[str] = mapped_column(String(48))
    control_ref: Mapped[str | None] = mapped_column(String(32))
    metric: Mapped[str | None] = mapped_column(String(48))
    observed: Mapped[float | None] = mapped_column(Float)
    threshold: Mapped[float | None] = mapped_column(Float)
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    action: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16), default=AlertStatus.OPEN)
    details: Mapped[dict[str, Any]] = mapped_column(default=dict)
    triggered_at: Mapped[datetime]
    acknowledged_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(nullable=True)


class Notification(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_user_read", "user_id", "read_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(48))
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str | None] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(String(500))
    severity: Mapped[str | None] = mapped_column(String(16))
    read_at: Mapped[datetime | None] = mapped_column(nullable=True)


class Integration(IdMixin, TimestampMixin, OrgMixin, Base):
    __tablename__ = "integrations"
    __table_args__ = (UniqueConstraint("organization_id", "kind", name="uq_integrations_org_kind"),)

    kind: Mapped[str] = mapped_column(String(24))
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(24), default=ConnectionStatus.NOT_CONFIGURED)
    config: Mapped[dict[str, Any]] = mapped_column(default=dict)  # non-secret configuration only
    secret_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("secrets.id", ondelete="SET NULL"), nullable=True)
    provider_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("providers.id", ondelete="SET NULL"), nullable=True
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text)
    connected_at: Mapped[datetime | None] = mapped_column(nullable=True)


class Webhook(IdMixin, TimestampMixin, OrgMixin, Base):
    __tablename__ = "webhooks"

    url: Mapped[str] = mapped_column(String(1000))
    description: Mapped[str | None] = mapped_column(String(300))
    events: Mapped[list[str]] = mapped_column(default=list)
    secret_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("secrets.id", ondelete="RESTRICT"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    last_delivery_at: Mapped[datetime | None] = mapped_column(nullable=True)


class WebhookDelivery(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (Index("ix_webhook_deliveries_status_next", "status", "next_attempt_at"),)

    webhook_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("webhooks.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(48))
    event_id: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(16), default=DeliveryStatus.PENDING)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(nullable=True)
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_excerpt: Mapped[str | None] = mapped_column(String(500))
    last_error: Mapped[str | None] = mapped_column(Text)
    delivered_at: Mapped[datetime | None] = mapped_column(nullable=True)
