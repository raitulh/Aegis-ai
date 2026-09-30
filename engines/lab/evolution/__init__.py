"""Evolution Engine — pure multi-objective evolution of research strategies.

NSGA-II with constrained domination (``selection``), an ε-Pareto archive (``archive``),
parameter-space diversity/novelty (``diversity``), guardrails that keep evolution from touching
authority (``guardrails``), a statistically grounded promotion gate (``promotion``) and rollback
planning (``rollback``). Everything is deterministic given a seed and free of IO; the strategies
service persists versions, evaluations and archives and applies governance + human approval.
"""

from __future__ import annotations

from engines.lab.evolution.archive import ArchiveEntry, ArchiveManager, InsertResult
from engines.lab.evolution.candidates import CandidateGenerator, GenerationBatch, RejectedChild
from engines.lab.evolution.diversity import DiversityManager, DiversityMetrics
from engines.lab.evolution.engine import (
    CandidateAssessment,
    EvolutionConfig,
    EvolutionEngine,
    GenerationPlan,
    ObjectiveConstraint,
    PopulationAssessment,
    survival_transitions,
)
from engines.lab.evolution.fitness import CANONICAL_OBJECTIVES, FitnessEngine, default_objectives, resolve_objectives
from engines.lab.evolution.guardrails import (
    FORBIDDEN_KEYS,
    GuardrailReport,
    GuardrailViolation,
    StrategyGuardrails,
    Violation,
    ViolationCode,
    forbidden_term,
)
from engines.lab.evolution.hypervolume import hypervolume, reference_point
from engines.lab.evolution.mutation import CROSSOVER_OPERATORS, MUTATION_OPERATORS, MutationEngine, MutationError
from engines.lab.evolution.population import PopulationManager, param_hash
from engines.lab.evolution.promotion import (
    BenchmarkEvidence,
    PromotionCheck,
    PromotionDecision,
    PromotionGate,
    PromotionPolicy,
)
from engines.lab.evolution.rollback import RollbackError, RollbackManager, RollbackPlan, StatusChange
from engines.lab.evolution.selection import (
    Ranking,
    SelectionEngine,
    constrained_dominates,
    crowding_distance,
    fast_non_dominated_sort,
    pareto_dominates,
)
from engines.lab.evolution.types import (
    Candidate,
    ConstraintSpec,
    Direction,
    FitnessVector,
    MutationRecord,
    ObjectiveSpec,
    ParameterSchema,
    ParameterSpec,
    ParamType,
    SchemaError,
    canonical_hash,
)

__all__ = [
    "CANONICAL_OBJECTIVES",
    "CROSSOVER_OPERATORS",
    "FORBIDDEN_KEYS",
    "MUTATION_OPERATORS",
    "ArchiveEntry",
    "ArchiveManager",
    "BenchmarkEvidence",
    "Candidate",
    "CandidateAssessment",
    "CandidateGenerator",
    "ConstraintSpec",
    "Direction",
    "DiversityManager",
    "DiversityMetrics",
    "EvolutionConfig",
    "EvolutionEngine",
    "FitnessEngine",
    "FitnessVector",
    "GenerationBatch",
    "GenerationPlan",
    "GuardrailReport",
    "GuardrailViolation",
    "InsertResult",
    "MutationEngine",
    "MutationError",
    "MutationRecord",
    "ObjectiveConstraint",
    "ObjectiveSpec",
    "ParamType",
    "ParameterSchema",
    "ParameterSpec",
    "PopulationAssessment",
    "PopulationManager",
    "PromotionCheck",
    "PromotionDecision",
    "PromotionGate",
    "PromotionPolicy",
    "Ranking",
    "RejectedChild",
    "RollbackError",
    "RollbackManager",
    "RollbackPlan",
    "SchemaError",
    "SelectionEngine",
    "StatusChange",
    "StrategyGuardrails",
    "Violation",
    "ViolationCode",
    "canonical_hash",
    "constrained_dominates",
    "crowding_distance",
    "default_objectives",
    "fast_non_dominated_sort",
    "forbidden_term",
    "hypervolume",
    "param_hash",
    "pareto_dominates",
    "reference_point",
    "resolve_objectives",
    "survival_transitions",
]
