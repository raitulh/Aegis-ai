"""Platform plumbing: the event outbox, consumer offsets, idempotency keys and durable workflow state."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from aegis_api.db.base import Base, IdMixin, OrgMixin, TimestampMixin, utcnow
from engines.lab.states import WorkflowStatus


class LabEvent(OrgMixin, Base):
    """Append-only event log + transactional outbox.

    Events are inserted in the same transaction as the state change they describe, so they are never
    lost or emitted for rolled-back work. The monotonically increasing ``id`` doubles as the SSE event id
    (``Last-Event-ID`` reconnects resume exactly where the client left off).
    """

    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_mission_id", "mission_id", "id"),
        Index("ix_events_org_id", "organization_id", "id"),
        Index("ix_events_org_type", "organization_id", "type"),
        Index("ix_events_subject", "subject_type", "subject_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    event_uuid: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    mission_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    subject_type: Mapped[str | None] = mapped_column(String(32))
    subject_id: Mapped[str | None] = mapped_column(String(64))
    type: Mapped[str] = mapped_column(String(48))
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    actor: Mapped[dict[str, Any]] = mapped_column(default=dict)
    trace_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False, index=True
    )


class EventConsumerOffset(Base):
    """Per-(consumer, organization) cursor into the event outbox (RLS-scoped by organization)."""

    __tablename__ = "event_consumer_offsets"

    consumer: Mapped[str] = mapped_column(String(64), primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    last_event_id: Mapped[int] = mapped_column(BigInteger, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now()
    )


class IdempotencyKey(IdMixin, OrgMixin, Base):
    """Stored response for an ``Idempotency-Key`` so retried requests never launch work twice.

    The row is inserted in the *same transaction* as the work it guards, so a concurrent duplicate blocks
    on the unique index until the first request commits and then replays its response.
    """

    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("organization_id", "principal_key", "key", name="uq_idempotency_keys_scope"),)

    principal_key: Mapped[str] = mapped_column(String(100))
    key: Mapped[str] = mapped_column(String(255))
    method: Mapped[str] = mapped_column(String(8))
    path: Mapped[str] = mapped_column(String(500))
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="completed")
    response_status: Mapped[int] = mapped_column(Integer, default=200)
    response_body: Mapped[Any] = mapped_column(JSONB, default=dict)
    resource_type: Mapped[str | None] = mapped_column(String(48))
    resource_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class WorkflowRun(IdMixin, TimestampMixin, OrgMixin, Base):
    """Durable record of a workflow (Temporal or the local engine). Workflow ids are deterministic so
    duplicate launches are rejected by the engine and by ``uq_workflow_runs_external``."""

    __tablename__ = "workflow_runs"
    __table_args__ = (
        UniqueConstraint("organization_id", "external_id", name="uq_workflow_runs_external"),
        Index("ix_workflow_runs_org_status", "organization_id", "status"),
        Index("ix_workflow_runs_subject", "subject_type", "subject_id"),
        Index("ix_workflow_runs_status_heartbeat", "status", "heartbeat_at"),
    )

    project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    mission_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(48))  # MissionWorkflow, ExperimentWorkflow, …
    subject_type: Mapped[str] = mapped_column(String(32))
    subject_id: Mapped[str] = mapped_column(String(64))
    engine: Mapped[str] = mapped_column(String(16))  # temporal|local
    external_id: Mapped[str] = mapped_column(String(255))
    external_run_id: Mapped[str | None] = mapped_column(String(255))
    task_queue: Mapped[str | None] = mapped_column(String(120))
    parent_workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), default=WorkflowStatus.PENDING)
    input: Mapped[dict[str, Any]] = mapped_column(default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)
    error: Mapped[str | None] = mapped_column(Text)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    # Local engine signal inbox: [{"name": ..., "payload": ..., "at": ...}]
    signals: Mapped[list[Any]] = mapped_column(default=list)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class WorkflowStep(IdMixin, TimestampMixin, OrgMixin, Base):
    """Memoized activity result for the local durable engine (replay-based resume, like Temporal)."""

    __tablename__ = "workflow_steps"
    __table_args__ = (UniqueConstraint("workflow_run_id", "step_key", name="uq_workflow_steps_run_key"),)

    workflow_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflow_runs.id", ondelete="CASCADE"), index=True)
    step_key: Mapped[str] = mapped_column(String(300))
    activity: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|completed|failed
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    result: Mapped[Any] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
