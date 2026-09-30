"""API contract of the strategies context: strategies, versions, evolution runs, promotion, benchmarks."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aegis_api.schemas.common import ORMModel
from engines.lab.evolution.fitness import CANONICAL_OBJECTIVES
from engines.lab.states import StrategyKind

_KINDS = tuple(k.value for k in StrategyKind)
MAX_JSON_KEYS = 200


def _check_kind(value: str) -> str:
    if value not in _KINDS:
        raise ValueError(f"kind must be one of {', '.join(_KINDS)}")
    return value


# ---------------------------------------------------------------------------------------------
# Strategies & versions
# ---------------------------------------------------------------------------------------------
class StrategyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(description=f"Strategy kind: {', '.join(_KINDS)}")
    name: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9 _.:-]*$")
    description: str | None = Field(default=None, max_length=5000)
    project_id: str | None = Field(default=None, description="Scope the strategy to a project (default: organization)")
    parameter_schema: dict[str, Any] = Field(
        description="Guardrail: {'parameters': {name: {type, min, max, step, choices, mutable, …}}, 'definition_keys': […]}"
    )
    definition: dict[str, Any] = Field(default_factory=dict, description="Behaviour definition (declared keys only)")
    parameters: dict[str, Any] = Field(default_factory=dict, description="Parameter values of version 1")

    @field_validator("kind")
    @classmethod
    def _kind(cls, value: str) -> str:
        return _check_kind(value)


class StrategyVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parameters: dict[str, Any] = Field(description="Complete parameter values (missing ones take schema defaults)")
    definition: dict[str, Any] | None = Field(default=None, description="Definition (default: the parent's)")
    parent_version_id: str | None = Field(
        default=None, description="Parent version (default: the promoted version, else the latest)"
    )
    rationale: str | None = Field(default=None, max_length=4000)


class VersionTransitionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["EXPERIMENTAL", "SURVIVING", "RETIRED"]
    reason: str = Field(min_length=3, max_length=2000)


class StrategyVersionOut(ORMModel):
    id: str
    strategy_id: str
    version: int
    parent_version_id: str | None
    definition: dict[str, Any]
    parameters: dict[str, Any]
    content_hash: str
    status: str
    evolution_run_id: str | None
    generation: int | None
    fitness: dict[str, Any]
    pareto_rank: int | None
    crowding: float | None
    mutation_history: list[Any]
    created_by: str
    created_by_agent_run_id: str | None
    promoted_at: datetime | None
    promoted_by_id: str | None
    retired_at: datetime | None
    created_at: datetime
    updated_at: datetime


class StrategyOut(ORMModel):
    id: str
    kind: str
    name: str
    description: str | None
    project_id: str | None
    workspace_id: str | None
    parameter_schema: dict[str, Any]
    current_version_id: str | None
    status: str
    created_by_id: str | None
    created_at: datetime
    updated_at: datetime


class StrategyDetailOut(StrategyOut):
    current_version: StrategyVersionOut | None = None
    version_count: int = 0


class StrategyMutationOut(ORMModel):
    id: str
    strategy_id: str
    parent_version_id: str | None
    second_parent_version_id: str | None
    child_version_id: str
    operator: str
    diff: dict[str, Any]
    rationale: str | None
    seed: int | None
    generated_by: str
    agent_run_id: str | None
    evolution_run_id: str | None
    created_at: datetime


class StrategyEvaluationOut(ORMModel):
    id: str
    strategy_version_id: str
    evolution_run_id: str | None
    benchmark_run_id: str | None
    mission_id: str | None
    objectives: dict[str, Any]
    raw_metrics: dict[str, Any]
    n_samples: int
    seeds: list[Any]
    evaluator_version: str
    constraints_satisfied: bool
    evidence: list[Any]
    created_at: datetime


class LineageNodeOut(BaseModel):
    id: str
    version: int
    status: str
    parent_version_id: str | None
    created_by: str
    generation: int | None
    evolution_run_id: str | None


class StrategyLineageOut(BaseModel):
    version_id: str
    ancestors: list[LineageNodeOut] = Field(description="Parent chain, nearest first")
    children: list[LineageNodeOut]
    mutations: list[StrategyMutationOut] = Field(description="Mutations that produced this version")


class ActiveStrategyOut(BaseModel):
    kind: str
    version: StrategyVersionOut | None
    strategy_id: str | None
    scope: Literal["mission", "project", "organization", "none"]


# ---------------------------------------------------------------------------------------------
# Evaluation / promotion / rollback
# ---------------------------------------------------------------------------------------------
class EvaluateVersionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    benchmark_keys: list[str] | None = Field(default=None, min_length=1, max_length=8)
    seeds: list[int] | None = Field(default=None, min_length=1, max_length=10)

    @field_validator("seeds")
    @classmethod
    def _seeds(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return value
        if any(s < 0 or s > 0x7FFFFFFF for s in value) or len(set(value)) != len(value):
            raise ValueError("seeds must be distinct integers in [0, 2^31-1]")
        return value


class EvaluationAcceptedOut(BaseModel):
    strategy_version_id: str
    status: Literal["queued"]
    request_key: str
    benchmark_keys: list[str]
    seeds: list[int]


class PromoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=3, max_length=2000)


class PromotionCheckOut(BaseModel):
    name: str
    passed: bool
    detail: str


class PromotionDecisionOut(BaseModel):
    eligible: bool
    reasons: list[str]
    checks: list[PromotionCheckOut]
    statistics: dict[str, Any]
    incumbent_version_id: str | None
    benchmark_suites: list[str]
    requires_human_approval: bool = True


class PromotionOut(BaseModel):
    status: Literal["promoted", "pending_approval"]
    version: StrategyVersionOut
    approval_id: str | None = None
    retired_version_ids: list[str] = Field(default_factory=list)
    decision: PromotionDecisionOut


class RollbackIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=3, max_length=2000)
    target_version_id: str | None = Field(default=None, description="Explicit target (default: previous promoted)")


class RollbackOut(BaseModel):
    strategy_id: str
    rolled_back_version_id: str
    restored_version_id: str
    reason: str


# ---------------------------------------------------------------------------------------------
# Evolution runs
# ---------------------------------------------------------------------------------------------
class EvolutionConfigIn(BaseModel):
    """Evolution run configuration (validated again by the pure engine's ``EvolutionConfig``)."""

    model_config = ConfigDict(extra="forbid")

    population_size: int = Field(default=8, ge=4, le=64)
    generations: int = Field(default=5, ge=1, le=50)
    objectives: list[str] | None = Field(
        default=None,
        description="Canonical objective names (default: all eight; 'safety' is always a hard constraint)",
        max_length=8,
    )
    mutation_rate: float | None = Field(default=None, gt=0, le=1)
    crossover_rate: float = Field(default=0.9, ge=0, le=1)
    seed: int = Field(default=0, ge=0, le=0x7FFFFFFF)
    benchmark_suites: list[str] | None = Field(default=None, min_length=1, max_length=8)
    evaluation_seeds: list[int] = Field(default_factory=lambda: [0, 1, 2], min_length=1, max_length=10)
    archive_size: int = Field(default=50, ge=1, le=500)
    epsilon: float = Field(default=0.01, gt=0, le=1)
    novelty_weight: float = Field(default=0.0, ge=0, le=10)

    @field_validator("objectives")
    @classmethod
    def _objectives(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        unknown = sorted(set(value) - set(CANONICAL_OBJECTIVES))
        if unknown:
            raise ValueError(f"unknown objectives {unknown}; allowed: {', '.join(CANONICAL_OBJECTIVES)}")
        if len(set(value)) != len(value):
            raise ValueError("objectives must be distinct")
        for required in ("scientific_performance", "safety"):
            if required not in value:
                raise ValueError(f"objectives must include {required!r}")
        return value

    @field_validator("evaluation_seeds")
    @classmethod
    def _seeds(cls, value: list[int]) -> list[int]:
        if any(s < 0 or s > 0x7FFFFFFF for s in value) or len(set(value)) != len(value):
            raise ValueError("evaluation_seeds must be distinct integers in [0, 2^31-1]")
        return value


class EvolutionRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_id: str
    mission_id: str | None = None
    config: EvolutionConfigIn = Field(default_factory=EvolutionConfigIn)


class EvolutionRunOut(ORMModel):
    id: str
    strategy_id: str
    mission_id: str | None
    project_id: str | None
    status: str
    config: dict[str, Any]
    current_generation: int
    best_version_ids: list[str]
    summary: dict[str, Any]
    started_at: datetime | None
    completed_at: datetime | None
    error: str | None
    workflow_run_id: str | None
    created_by_id: str | None
    created_at: datetime
    updated_at: datetime


class EvolutionRunDetailOut(EvolutionRunOut):
    archive: dict[str, Any]
    front: list[StrategyVersionOut] = Field(default_factory=list, description="Rank-0 front of the latest generation")


class EvolutionCancelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=2000)


# ---------------------------------------------------------------------------------------------
# Benchmarks
# ---------------------------------------------------------------------------------------------
class BenchmarkSuiteOut(ORMModel):
    id: str
    key: str
    version: str
    description: str | None
    component: str
    case_count: int
    content_hash: str
    config: dict[str, Any]
    organization_id: str | None
    strategy_kind: str | None = None
    subject_types: list[str] = Field(default_factory=list)
    created_at: datetime


class BenchmarkRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_type: Literal["strategy_version", "component", "experiment"]
    subject_id: str = Field(min_length=1, max_length=64)
    seed: int = Field(default=0, ge=0, le=0x7FFFFFFF)
    params: dict[str, Any] | None = Field(
        default=None, description="Parameter overrides for component subjects (validated by the strategy guardrails)"
    )

    @model_validator(mode="after")
    def _params(self) -> BenchmarkRunCreate:
        if self.params is not None and len(self.params) > MAX_JSON_KEYS:
            raise ValueError("too many parameters")
        return self


class BenchmarkRunOut(ORMModel):
    id: str
    suite_id: str
    suite_key: str
    suite_version: str
    subject_type: str
    subject_id: str
    status: str
    score: float | None
    metrics: dict[str, Any]
    seed: int | None
    baseline_run_id: str | None
    comparison: dict[str, Any]
    started_at: datetime | None
    completed_at: datetime | None
    error: str | None
    created_by_id: str | None
    created_at: datetime
    updated_at: datetime


class BenchmarkRunDetailOut(BenchmarkRunOut):
    case_results: list[Any]
