"""API contract of the hypotheses context (create/update/critique/select/evidence/conclude)."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator, model_validator

from aegis_api.schemas.common import ORMModel
from engines.lab.experiment_spec import (
    BASELINE_ONLY_COMPARATORS,
    DELTA_COMPARATORS,
    Comparator,
    SuccessCriterion,
    criterion_contradicts_direction,
)

Money = Annotated[Decimal, PlainSerializer(lambda v: float(v), return_type=float, when_used="json")]

METRIC_PATTERN = r"^[A-Za-z_][A-Za-z0-9_.:/@-]{0,119}$"
MAX_TEXT = 20_000
EvidenceRefType = Literal[
    "research_source",
    "source_document",
    "memory",
    "experiment",
    "experiment_run",
    "experiment_comparison",
    "evaluation_run",
    "dataset_version",
    "artifact_version",
    "claim",
    "hypothesis",
]
EvidenceRelation = Literal["supports", "contradicts", "context"]
Recommendation = Literal["select", "revise", "reject"]
REF_TYPE_ALIASES = {"comparison": "experiment_comparison", "source": "research_source", "paper": "research_source"}

PREDICTION_GUIDANCE = (
    "A hypothesis must be falsifiable: give a measurable_prediction with the metric that will be measured, a "
    "comparator (gt, gte, lt, lte, delta_gt, delta_gte, delta_lt, delta_lte, relative_improvement_gte), a numeric "
    "threshold, relative_to ('baseline' or 'absolute') and the metric direction ('maximize' or 'minimize'). "
    "Example: {'metric': 'accuracy', 'comparator': 'delta_gte', 'threshold': 0.02, 'relative_to': 'baseline', "
    "'direction': 'maximize'} predicts at least +0.02 accuracy over the baseline."
)
PREDICTION_EXAMPLE: dict[str, Any] = {
    "metric": "accuracy",
    "comparator": "delta_gte",
    "threshold": 0.02,
    "relative_to": "baseline",
    "direction": "maximize",
}


def _normalise_ref_type(value: Any) -> Any:
    if isinstance(value, str):
        key = value.strip().lower()
        return REF_TYPE_ALIASES.get(key, key)
    return value


class MeasurablePrediction(BaseModel):
    """The falsifiable, pre-registered prediction of a hypothesis (same semantics as a success criterion)."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    metric: str = Field(pattern=METRIC_PATTERN, description="The metric that will be measured")
    comparator: Comparator
    threshold: float
    relative_to: Literal["baseline", "absolute"] | None = Field(
        default=None, description="Defaults to 'baseline' for delta/relative comparators, else 'absolute'"
    )
    direction: Literal["maximize", "minimize"]

    @model_validator(mode="after")
    def _consistent(self) -> MeasurablePrediction:
        if self.relative_to is None:
            self.relative_to = "baseline" if self.comparator in BASELINE_ONLY_COMPARATORS else "absolute"
        if self.comparator in DELTA_COMPARATORS and self.relative_to == "absolute":
            raise ValueError(f"{self.comparator} is a difference to the baseline; relative_to must be 'baseline'")
        if self.comparator == "relative_improvement_gte" and self.relative_to != "baseline":
            raise ValueError("relative_improvement_gte is relative to the baseline; relative_to must be 'baseline'")
        if criterion_contradicts_direction(self.to_criterion(), self.direction):
            raise ValueError(
                f"comparator {self.comparator} rewards moving {self.metric!r} against its direction ({self.direction})"
            )
        return self

    def to_criterion(self) -> SuccessCriterion:
        return SuccessCriterion(
            metric=self.metric,
            comparator=self.comparator,
            threshold=self.threshold,
            relative_to=self.relative_to or "absolute",
        )


def parse_prediction(value: Mapping[str, Any] | None) -> MeasurablePrediction | None:
    """A stored prediction as a model (``None`` when absent or invalid)."""
    if not value:
        return None
    try:
        return MeasurablePrediction.model_validate(dict(value))
    except ValueError:
        return None


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref_type: EvidenceRefType
    ref_id: uuid.UUID
    note: str | None = Field(default=None, max_length=2000)

    normalise_ref_type = field_validator("ref_type", mode="before")(_normalise_ref_type)


class HypothesisCreate(BaseModel):
    """A new hypothesis. ``measurable_prediction`` is required (a vague hypothesis is rejected with guidance)."""

    model_config = ConfigDict(extra="forbid")

    project_id: uuid.UUID
    mission_id: uuid.UUID | None = None
    statement: str = Field(min_length=1, max_length=4000)
    rationale: str | None = Field(default=None, max_length=MAX_TEXT)
    expected_outcome: str | None = Field(default=None, max_length=4000)
    measurable_prediction: MeasurablePrediction | None = None
    assumptions: list[Annotated[str, Field(min_length=1, max_length=1000)]] = Field(default_factory=list, max_length=50)
    novelty_notes: str | None = Field(default=None, max_length=4000)
    feasibility: float | None = Field(default=None, ge=0.0, le=1.0)
    estimated_cost_usd: Decimal = Field(default=Decimal("0"), ge=0, le=Decimal("1000000000"))
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    parent_hypothesis_id: uuid.UUID | None = None
    supporting_evidence: list[EvidenceRef] = Field(default_factory=list, max_length=50)
    contradicting_evidence: list[EvidenceRef] = Field(default_factory=list, max_length=50)


class HypothesisUpdate(BaseModel):
    """Editable fields (only while the hypothesis is GENERATED or CRITIQUED)."""

    model_config = ConfigDict(extra="forbid")

    statement: str | None = Field(default=None, min_length=1, max_length=4000)
    rationale: str | None = Field(default=None, max_length=MAX_TEXT)
    expected_outcome: str | None = Field(default=None, max_length=4000)
    measurable_prediction: MeasurablePrediction | None = None
    assumptions: list[Annotated[str, Field(min_length=1, max_length=1000)]] | None = Field(default=None, max_length=50)
    novelty_notes: str | None = Field(default=None, max_length=4000)
    feasibility: float | None = Field(default=None, ge=0.0, le=1.0)
    estimated_cost_usd: Decimal | None = Field(default=None, ge=0, le=Decimal("1000000000"))
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    lock_version: int | None = Field(default=None, ge=1, description="Optimistic concurrency check")


class CritiqueScores(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    novelty: float = Field(ge=0.0, le=1.0)
    feasibility: float = Field(ge=0.0, le=1.0)
    testability: float = Field(ge=0.0, le=1.0)
    evidence_strength: float = Field(ge=0.0, le=1.0)
    risk: float = Field(ge=0.0, le=1.0)


class CritiqueInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scores: CritiqueScores
    issues: list[Annotated[str, Field(min_length=1, max_length=2000)]] = Field(default_factory=list, max_length=50)
    recommendation: Recommendation
    rationale: str | None = Field(default=None, max_length=8000)


class TransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(min_length=1, max_length=24)
    reason: str = Field(min_length=1, max_length=2000)


class SelectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    top_k: int = Field(default=3, ge=1, le=50)


class EvidenceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relation: EvidenceRelation = "supports"
    ref_type: EvidenceRefType
    ref_id: uuid.UUID
    note: str | None = Field(default=None, max_length=2000)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    normalise_ref_type = field_validator("ref_type", mode="before")(_normalise_ref_type)


class ConcludeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comparison_ids: list[uuid.UUID] = Field(min_length=1, max_length=20)


# ---------------------------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------------------------
class HypothesisOut(ORMModel):
    id: str
    workspace_id: str
    project_id: str
    mission_id: str | None = None
    statement: str
    rationale: str | None = None
    expected_outcome: str | None = None
    measurable_prediction: dict[str, Any]
    assumptions: list[Any]
    novelty_notes: str | None = None
    feasibility: float | None = None
    estimated_cost_usd: Money
    confidence: float | None = None
    status: str
    status_reason: str | None = None
    parent_hypothesis_id: str | None = None
    generation: int
    scores: dict[str, Any]
    selection_rank: int | None = None
    provenance: dict[str, Any]
    supporting_evidence: list[Any]
    contradicting_evidence: list[Any]
    created_by_id: str | None = None
    created_by_agent_run_id: str | None = None
    lock_version: int
    created_at: datetime
    updated_at: datetime


class HypothesisEvidenceOut(ORMModel):
    id: str
    hypothesis_id: str
    relation: str
    ref_type: str
    ref_id: str
    note: str | None = None
    confidence: float
    created_at: datetime


class HypothesisCritiqueOut(ORMModel):
    id: str
    hypothesis_id: str
    critic_type: str
    agent_run_id: str | None = None
    critic_user_id: str | None = None
    scores: dict[str, Any]
    issues: list[Any]
    recommendation: str
    rationale: str | None = None
    provenance: dict[str, Any]
    created_at: datetime


class HypothesisDetailOut(HypothesisOut):
    evidence: list[HypothesisEvidenceOut] = Field(default_factory=list)
    critiques: list[HypothesisCritiqueOut] = Field(default_factory=list)


class RankingEntryOut(BaseModel):
    id: str
    score: float
    rank: int | None = None
    eligible: bool
    reason: str | None = None
    selected: bool
    newly_selected: bool
    components: dict[str, float]


class SelectionOut(BaseModel):
    mission_id: str
    top_k: int
    selected_ids: list[str]
    newly_selected_ids: list[str]
    ranking: list[RankingEntryOut]
    weights: dict[str, Any]
    weight_notes: list[str] = Field(default_factory=list)
    strategy_version_id: str | None = None
    scoring_version: str


class ConclusionOut(BaseModel):
    hypothesis: HypothesisOut
    status: str
    rationale: str
    comparison_ids: list[str]
    assessments: list[dict[str, Any]]
