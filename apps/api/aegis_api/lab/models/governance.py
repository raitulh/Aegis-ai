"""Versioned governance policies and human approval requests."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from aegis_api.db.base import Base, CreatedMixin, IdMixin, OrgMixin, TimestampMixin
from aegis_api.lab.models._common import OptionalProjectScoped, money_column, project_scope_fk, user_fk
from engines.lab.states import ApprovalStatus, RiskLevel


class GovernancePolicy(IdMixin, TimestampMixin, OrgMixin, Base):
    """An organization (or project) policy. Rules live in immutable ``governance_policy_versions``.

    Organization policies can only *tighten* the platform baseline: baseline ``deny`` and
    ``require_approval`` rules are evaluated first and cannot be overridden by an ``allow``.
    """

    __tablename__ = "governance_policies"
    __table_args__ = (UniqueConstraint("organization_id", "key", name="uq_governance_policies_org_key"),)

    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    key: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|disabled
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("governance_policy_versions.id", ondelete="SET NULL", use_alter=True), nullable=True
    )
    created_by_id: Mapped[uuid.UUID | None] = user_fk()


class GovernancePolicyVersion(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "governance_policy_versions"
    __table_args__ = (UniqueConstraint("policy_id", "version", name="uq_governance_policy_versions_version"),)

    policy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("governance_policies.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    rules: Mapped[list[Any]] = mapped_column(default=list)
    content_hash: Mapped[str] = mapped_column(String(64))
    change_note: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[uuid.UUID | None] = user_fk()


class Approval(IdMixin, TimestampMixin, OrgMixin, OptionalProjectScoped, Base):
    """A human approval gate. Agents can request approvals but can never decide them, and the requester
    can never approve their own request (separation of duties)."""

    __tablename__ = "approvals"
    __table_args__ = (
        project_scope_fk(),
        Index("ix_approvals_org_status", "organization_id", "status"),
        Index("ix_approvals_mission", "mission_id"),
        Index("ix_approvals_subject", "subject_type", "subject_id"),
    )

    mission_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), nullable=True)
    # e.g. execution.expensive_compute, execution.network_egress, tool.high_risk, discovery.approve,
    # discovery.publish, strategy.promote, integration.production, mission.plan, research.deep_research
    action: Mapped[str] = mapped_column(String(64))
    subject_type: Mapped[str] = mapped_column(String(32))
    subject_id: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(300))
    request_payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    risk_level: Mapped[str] = mapped_column(String(16), default=RiskLevel.MEDIUM)
    estimated_cost_usd: Mapped[Decimal] = money_column()
    requested_by_type: Mapped[str] = mapped_column(String(16))  # user|agent|workflow|system|api_key
    requested_by_id: Mapped[uuid.UUID | None] = user_fk()
    requested_by_agent_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    requested_by_label: Mapped[str | None] = mapped_column(String(320))
    policy_decision: Mapped[dict[str, Any]] = mapped_column(default=dict)
    required_permission: Mapped[str] = mapped_column(String(64), default="approval:decide")
    status: Mapped[str] = mapped_column(String(16), default=ApprovalStatus.PENDING)
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True, index=True)
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    signal_name: Mapped[str | None] = mapped_column(String(64))
    lock_version: Mapped[int] = mapped_column(Integer, default=1)

    __mapper_args__ = {"version_id_col": lock_version}
