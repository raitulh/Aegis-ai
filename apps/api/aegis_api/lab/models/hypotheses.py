"""Hypotheses, their evidence links and critiques."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from aegis_api.db.base import Base, CreatedMixin, IdMixin, OrgMixin, TimestampMixin
from aegis_api.lab.models._common import ProjectScoped, money_column, project_scope_fk, user_fk
from engines.lab.states import HypothesisStatus


class Hypothesis(IdMixin, TimestampMixin, OrgMixin, ProjectScoped, Base):
    """A falsifiable, measurable scientific hypothesis. Its status only reaches ``SUPPORTED`` or
    ``REJECTED`` through experiment evaluation — never because a model asserted it."""

    __tablename__ = "hypotheses"
    __table_args__ = (
        project_scope_fk(),
        Index("ix_hypotheses_mission_status", "mission_id", "status"),
        Index("ix_hypotheses_org_status", "organization_id", "status"),
    )

    mission_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), nullable=True)
    statement: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str | None] = mapped_column(Text)
    supporting_evidence: Mapped[list[Any]] = mapped_column(default=list)
    contradicting_evidence: Mapped[list[Any]] = mapped_column(default=list)
    expected_outcome: Mapped[str | None] = mapped_column(Text)
    # {"metric": "accuracy", "comparator": "gt", "threshold": 0.02, "relative_to": "baseline", "direction": "maximize"}
    measurable_prediction: Mapped[dict[str, Any]] = mapped_column(default=dict)
    assumptions: Mapped[list[Any]] = mapped_column(default=list)
    novelty_notes: Mapped[str | None] = mapped_column(Text)
    feasibility: Mapped[float | None] = mapped_column(Float, nullable=True)
    estimated_cost_usd: Mapped[Decimal] = money_column()
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default=HypothesisStatus.GENERATED)
    status_reason: Mapped[str | None] = mapped_column(Text)
    parent_hypothesis_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("hypotheses.id", ondelete="SET NULL"), nullable=True
    )
    generation: Mapped[int] = mapped_column(Integer, default=0)
    scores: Mapped[dict[str, Any]] = mapped_column(default=dict)
    selection_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provenance: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_by_id: Mapped[uuid.UUID | None] = user_fk()
    created_by_agent_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    lock_version: Mapped[int] = mapped_column(Integer, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class HypothesisEvidence(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "hypothesis_evidence"
    __table_args__ = (
        UniqueConstraint("hypothesis_id", "ref_type", "ref_id", "relation", name="uq_hypothesis_evidence_link"),
    )

    hypothesis_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hypotheses.id", ondelete="CASCADE"), index=True)
    relation: Mapped[str] = mapped_column(String(16))  # supports|contradicts|context
    ref_type: Mapped[str] = mapped_column(String(32))
    ref_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    note: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)


class HypothesisCritique(IdMixin, CreatedMixin, OrgMixin, Base):
    __tablename__ = "hypothesis_critiques"

    hypothesis_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hypotheses.id", ondelete="CASCADE"), index=True)
    critic_type: Mapped[str] = mapped_column(String(16))  # agent|human|rule
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    critic_user_id: Mapped[uuid.UUID | None] = user_fk()
    # novelty, feasibility, testability, evidence_strength, risk (0..1)
    scores: Mapped[dict[str, Any]] = mapped_column(default=dict)
    issues: Mapped[list[Any]] = mapped_column(default=list)
    recommendation: Mapped[str] = mapped_column(String(16))  # select|revise|reject
    rationale: Mapped[str | None] = mapped_column(Text)
    provenance: Mapped[dict[str, Any]] = mapped_column(default=dict)
