"""Execution fabric: sandboxed compute jobs and their measured resource usage."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from aegis_api.db.base import Base, CreatedMixin, IdMixin, OrgMixin, TimestampMixin
from aegis_api.lab.models._common import ProjectScoped, money_column, project_scope_fk, user_fk
from engines.lab.states import ExecutionStatus


class ComputeJob(IdMixin, TimestampMixin, OrgMixin, ProjectScoped, Base):
    """ExecutionJob: one sandboxed container run. Never executed inside the API process.

    Security posture is recorded on the row (network/filesystem/secrets policy) so every run is
    auditable and reproducible. ``idempotency_key`` prevents launching the same job twice.
    """

    __tablename__ = "compute_jobs"
    __table_args__ = (
        project_scope_fk(),
        UniqueConstraint("organization_id", "idempotency_key", name="uq_compute_jobs_idempotency"),
        Index("ix_compute_jobs_org_status", "organization_id", "status"),
        Index("ix_compute_jobs_mission", "mission_id"),
        Index("ix_compute_jobs_status_heartbeat", "status", "heartbeat_at"),
    )

    mission_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), nullable=True)
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("experiments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    experiment_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("experiment_runs.id", ondelete="SET NULL"), nullable=True
    )
    purpose: Mapped[str] = mapped_column(String(24), default="experiment")  # experiment|reproduction|tool|evaluation
    backend: Mapped[str] = mapped_column(String(24))
    image: Mapped[str] = mapped_column(String(500))
    image_digest: Mapped[str | None] = mapped_column(String(100))
    command: Mapped[list[str]] = mapped_column(default=list)
    environment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("execution_environments.id", ondelete="SET NULL"), nullable=True
    )
    code_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    # Inputs mounted read-only: [{"kind": "dataset_version"|"artifact_version"|"code", "id": ..., "path": ...}]
    inputs: Mapped[list[Any]] = mapped_column(default=list)
    # cpu, memory_mb, disk_mb, pids, gpu_type, gpu_count, runtime
    resource_request: Mapped[dict[str, Any]] = mapped_column(default=dict)
    timeout_seconds: Mapped[int] = mapped_column(Integer)
    network_policy: Mapped[dict[str, Any]] = mapped_column(default=dict)  # {"mode": "none"|"allowlist", "hosts": []}
    filesystem_policy: Mapped[dict[str, Any]] = mapped_column(default=dict)
    secrets_policy: Mapped[dict[str, Any]] = mapped_column(default=dict)  # {"allowed": []}
    env: Mapped[dict[str, Any]] = mapped_column(default=dict)  # explicit, non-secret environment only
    status: Mapped[str] = mapped_column(String(24), default=ExecutionStatus.QUEUED)
    status_reason: Mapped[str | None] = mapped_column(Text)
    backend_job_id: Mapped[str | None] = mapped_column(String(200))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    queued_at: Mapped[datetime | None] = mapped_column(nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(nullable=True)
    artifact_ids: Mapped[list[str]] = mapped_column(default=list)
    output_manifest: Mapped[dict[str, Any]] = mapped_column(default=dict)
    logs_artifact_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    resource_usage: Mapped[dict[str, Any]] = mapped_column(default=dict)
    estimated_cost_usd: Mapped[Decimal] = money_column()
    cost_usd: Mapped[Decimal] = money_column()
    error: Mapped[str | None] = mapped_column(Text)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    approval_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_by_id: Mapped[uuid.UUID | None] = user_fk()
    created_by_agent_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)


class ComputeUsage(IdMixin, CreatedMixin, OrgMixin, Base):
    """Append-only compute ledger row emitted when a job finishes."""

    __tablename__ = "compute_usage"
    __table_args__ = (
        Index("ix_compute_usage_org_created", "organization_id", "created_at"),
        Index("ix_compute_usage_mission", "mission_id"),
    )

    project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    mission_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    compute_job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("compute_jobs.id", ondelete="CASCADE"), index=True)
    backend: Mapped[str] = mapped_column(String(24))
    cpu_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    gpu_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    gpu_type: Mapped[str | None] = mapped_column(String(48))
    memory_mb_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    wall_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    cost_usd: Mapped[Decimal] = money_column()
    cost_basis: Mapped[str] = mapped_column(String(120), default="configured rates")
