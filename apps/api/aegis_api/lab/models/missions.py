"""Research missions and their immutable definition history."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from aegis_api.db.base import Base, CreatedMixin, IdMixin, OrgMixin, TimestampMixin
from aegis_api.lab.models._common import ProjectScoped, money_column, project_scope_fk, user_fk
from engines.lab.states import AutonomyLevel, MissionStatus, RiskLevel


class Mission(IdMixin, TimestampMixin, OrgMixin, ProjectScoped, Base):
    """A human-defined research objective executed by the lab under explicit constraints and budgets.

    Budget counters (``spent_*``, ``experiment_count``) are incremented atomically in SQL by the usage
    recorder; the per-call ledgers (``model_usage``, ``compute_usage``, ``tool_invocations``) remain the
    source of truth.
    """

    __tablename__ = "missions"
    __table_args__ = (
        project_scope_fk(),
        Index("ix_missions_org_status", "organization_id", "status"),
        Index("ix_missions_project_status", "project_id", "status"),
        Index("ix_missions_updated_at", "updated_at"),
    )

    title: Mapped[str] = mapped_column(String(300))
    objective: Mapped[str] = mapped_column(Text)
    domain: Mapped[str] = mapped_column(String(64), default="general")
    constraints: Mapped[list[Any]] = mapped_column(default=list)
    success_criteria: Mapped[list[Any]] = mapped_column(default=list)
    # max_total_cost_usd, max_llm_cost_usd, max_compute_cost_usd, max_tool_cost_usd, max_experiment_count,
    # max_research_tasks
    budget: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # max_cpu_hours, max_gpu_hours, max_concurrent_jobs
    compute_budget: Mapped[dict[str, Any]] = mapped_column(default=dict)
    time_budget_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deadline: Mapped[datetime | None] = mapped_column(nullable=True)
    allowed_tools: Mapped[list[str]] = mapped_column(default=list)
    risk_level: Mapped[str] = mapped_column(String(16), default=RiskLevel.MEDIUM)
    autonomy_level: Mapped[str] = mapped_column(String(40), default=AutonomyLevel.L1_RESEARCH_AUTOMATION)
    # Which operations require human approval beyond the policy baseline, e.g.
    # {"require_approval_for": ["experiment.execute", "strategy.promote"], "approver_permission": "..."}
    approval_policy: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(16), default=MissionStatus.DRAFT, index=True)
    status_reason: Mapped[str | None] = mapped_column(Text)
    current_phase: Mapped[str | None] = mapped_column(String(32))
    plan: Mapped[dict[str, Any]] = mapped_column(default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    cycle: Mapped[int] = mapped_column(Integer, default=0)
    # kind → strategy_version_id pinned for this mission (else the promoted version is used)
    strategy_pins: Mapped[dict[str, Any]] = mapped_column(default=dict)
    spent_llm_usd: Mapped[Decimal] = money_column()
    spent_compute_usd: Mapped[Decimal] = money_column()
    spent_tool_usd: Mapped[Decimal] = money_column()
    experiment_count: Mapped[int] = mapped_column(Integer, default=0)
    research_task_count: Mapped[int] = mapped_column(Integer, default=0)
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_by_id: Mapped[uuid.UUID | None] = user_fk()
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    lock_version: Mapped[int] = mapped_column(Integer, default=1)

    __mapper_args__ = {"version_id_col": lock_version}

    @property
    def spent_total_usd(self) -> Decimal:
        return (
            (self.spent_llm_usd or Decimal(0))
            + (self.spent_compute_usd or Decimal(0))
            + (self.spent_tool_usd or Decimal(0))
        )


class MissionVersion(IdMixin, CreatedMixin, OrgMixin, Base):
    """Immutable snapshot of a mission definition (UPDATE/DELETE blocked by trigger)."""

    __tablename__ = "mission_versions"
    __table_args__ = (UniqueConstraint("mission_id", "version", name="uq_mission_versions_mission_version"),)

    mission_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict[str, Any]] = mapped_column(default=dict)
    change_summary: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_by_id: Mapped[uuid.UUID | None] = user_fk()
