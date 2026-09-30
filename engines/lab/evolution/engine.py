"""The Evolution Engine — one pure, deterministic NSGA-II generation at a time.

The strategies service drives the loop and owns persistence; the engine only decides::

    engine = EvolutionEngine(EvolutionConfig(...), schema)
    population = engine.initialize(base_params, n=config.population_size)   # generation 0 drafts
    # service: persist drafts as CANDIDATE versions, evaluate them, attach raw objective values
    plan = engine.step(evaluated_population, generation=1, archive=run.archive)
    # service: SURVIVING/RETIRED per plan.survivors/eliminated (see survival_transitions),
    #          store plan.archive, persist plan.children, evaluate, repeat with survivors + children

:meth:`EvolutionEngine.step` is (μ+λ) NSGA-II with constrained domination:

1. evaluate raw objectives into oriented fitness vectors (novelty is computed in parameter space
   when ``novelty`` is an objective without a measured value);
2. fast non-dominated sort + crowding distance;
3. environmental selection of ``population_size`` survivors (crowding + optional novelty bonus);
4. ε-Pareto archive update with every feasible candidate;
5. binary crowded tournament over the survivors → mating pool;
6. crossover/mutation under guardrails with de-duplication against population + archive.

Same inputs + same seed ⇒ identical plan. Nothing here reads a clock, the network or global RNG.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping, Sequence
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from engines.lab.evolution.archive import ArchiveEntry, ArchiveManager
from engines.lab.evolution.candidates import CandidateGenerator, RejectedChild
from engines.lab.evolution.diversity import DiversityManager, DiversityMetrics
from engines.lab.evolution.fitness import FitnessEngine, default_objectives, resolve_objectives
from engines.lab.evolution.guardrails import DEFAULT_GUARDRAILS, GuardrailViolation, StrategyGuardrails
from engines.lab.evolution.hypervolume import hypervolume
from engines.lab.evolution.mutation import MutationEngine, MutationError
from engines.lab.evolution.population import PopulationManager, param_hash
from engines.lab.evolution.selection import Ranking, SelectionEngine
from engines.lab.evolution.types import (
    Candidate,
    ConstraintSpec,
    FitnessVector,
    MutationRecord,
    ObjectiveSpec,
    ParameterSchema,
    SchemaError,
)
from engines.lab.states import StrategyStatus, assert_transition


class ObjectiveConstraint(BaseModel):
    """A run-level hard constraint (``evolution_runs.config.constraints``) on a named objective."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    objective: str
    op: Literal[">=", "<=", ">", "<", "=="]
    threshold: float
    tolerance: float = Field(default=1e-9, ge=0)


class EvolutionConfig(BaseModel):
    """Configuration of an evolution run (the ``evolution_runs.config`` document).

    ``generations``, ``max_candidates`` and ``benchmark_suite`` drive the service loop and are carried
    here only so the stored document validates as a whole; the pure engine does not use them.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    population_size: int = Field(default=20, ge=2, le=10_000)
    offspring_size: int | None = Field(default=None, ge=1, le=10_000)
    mutation_rate: float | None = Field(default=None, gt=0, le=1)
    crossover_rate: float = Field(default=0.9, ge=0, le=1)
    blend_probability: float = Field(default=0.5, ge=0, le=1)
    objectives: tuple[ObjectiveSpec, ...] = Field(default_factory=lambda: tuple(default_objectives()))
    constraints: tuple[ObjectiveConstraint, ...] = ()
    archive_size: int = Field(default=50, ge=1, le=10_000)
    epsilon: float | dict[str, float] = 0.01
    seed: int = Field(default=0, ge=0, le=0x7FFFFFFF)
    novelty_weight: float = Field(default=0.0, ge=0)
    novelty_k: int = Field(default=5, ge=1, le=1000)
    max_attempts_factor: int = Field(default=10, ge=1, le=100)
    generations: int | None = Field(default=None, ge=0, le=100_000)
    max_candidates: int | None = Field(default=None, ge=1)
    benchmark_suite: str | None = Field(default=None, max_length=64)

    @field_validator("objectives", mode="before")
    @classmethod
    def _resolve_objectives(cls, value: Any) -> Any:
        if isinstance(value, list | tuple):
            return tuple(resolve_objectives(value))
        return value

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not self.objectives:
            raise ValueError("at least one objective is required")
        names = [o.name for o in self.objectives]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate objective names: {names}")
        if isinstance(self.epsilon, dict):
            missing = [n for n in names if n not in self.epsilon]
            if missing:
                raise ValueError(f"epsilon missing for objectives {missing}")
            if any(e <= 0 for e in self.epsilon.values()):
                raise ValueError("epsilon values must be > 0")
        elif self.epsilon <= 0:
            raise ValueError("epsilon must be > 0")
        for constraint in self.constraints:
            if constraint.objective not in names:
                raise ValueError(f"constraint on unknown objective {constraint.objective!r}")
        if len({c.objective for c in self.constraints}) != len(self.constraints):
            raise ValueError("at most one run-level constraint per objective")
        return self

    @property
    def effective_offspring_size(self) -> int:
        return self.offspring_size if self.offspring_size is not None else self.population_size

    def resolved_objectives(self) -> tuple[ObjectiveSpec, ...]:
        """Objectives with run-level ``constraints`` applied (they override objective defaults)."""
        overrides = {c.objective: c for c in self.constraints}
        resolved: list[ObjectiveSpec] = []
        for objective in self.objectives:
            c = overrides.get(objective.name)
            if c is None:
                resolved.append(objective)
            else:
                constraint = ConstraintSpec(op=c.op, threshold=c.threshold, tolerance=c.tolerance)
                resolved.append(objective.model_copy(update={"constraint": constraint}))
        return tuple(resolved)


class CandidateAssessment(BaseModel):
    """Per-candidate outcome of a generation (maps onto ``strategy_versions`` fitness columns)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    rank: int
    crowding: float
    feasible: bool
    constraint_violation: float
    violations: tuple[str, ...]
    novelty: float | None
    summary: float
    objectives: dict[str, float | None]
    oriented: tuple[float, ...]


class PopulationAssessment(BaseModel):
    """Ranking + archive update for a population, without producing children."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    ids: tuple[str, ...]
    vectors: tuple[FitnessVector, ...]
    ranking: Ranking
    novelty: tuple[float | None, ...]
    assessments: dict[str, CandidateAssessment]
    archive: dict[str, Any]
    front: list[str]
    hypervolume: float | None
    notes: list[str] = Field(default_factory=list)


class GenerationPlan(BaseModel):
    """Everything the service needs to persist after one generation.

    ``crowding`` of boundary solutions is ``inf``; use :meth:`to_json_dict` for JSON storage (maps it
    to ``null``).
    """

    model_config = ConfigDict(extra="forbid")

    generation: int
    survivors: list[str]
    eliminated: list[str]
    children: list[Candidate]
    archive: dict[str, Any]
    front: list[str]
    assessments: dict[str, CandidateAssessment]
    diversity: DiversityMetrics
    hypervolume: float | None
    rejected_children: list[RejectedChild] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    def to_json_dict(self) -> dict[str, Any]:
        loaded: dict[str, Any] = json.loads(self.model_dump_json())
        return loaded


class EvolutionEngine:
    """Pure multi-objective evolution of strategy parameter vectors."""

    def __init__(
        self,
        config: EvolutionConfig | Mapping[str, Any],
        schema: ParameterSchema | Mapping[str, Any] | None = None,
        *,
        guardrails: StrategyGuardrails | None = None,
    ) -> None:
        self.config = config if isinstance(config, EvolutionConfig) else EvolutionConfig.model_validate(dict(config))
        self.schema = ParameterSchema.from_dict(schema) if schema is not None else None
        self.guardrails = guardrails or DEFAULT_GUARDRAILS
        self.fitness = FitnessEngine(self.config.resolved_objectives())
        self.selection = SelectionEngine(self.config.seed)
        self.mutation = MutationEngine(self.config.seed)

    # -- helpers ----------------------------------------------------------------------------------
    def _schema(self, schema: ParameterSchema | Mapping[str, Any] | None = None) -> ParameterSchema:
        if schema is not None:
            return ParameterSchema.from_dict(schema)
        if self.schema is None:
            raise SchemaError("a parameter schema is required (pass it to EvolutionEngine or the call)")
        return self.schema

    def new_archive(self) -> ArchiveManager:
        return ArchiveManager.create(self.fitness.names, self.config.epsilon, self.config.archive_size)

    def load_archive(self, archive: ArchiveManager | Mapping[str, Any] | None) -> ArchiveManager:
        """A private copy of ``archive`` (never mutates the caller's object)."""
        if archive is None or (isinstance(archive, Mapping) and not archive):
            return self.new_archive()
        data = archive.to_dict() if isinstance(archive, ArchiveManager) else archive
        loaded = ArchiveManager.from_dict(data)
        if loaded.objectives != self.fitness.names:
            raise ValueError(
                f"archive objectives {list(loaded.objectives)} do not match the run objectives {list(self.fitness.names)}"
            )
        loaded.max_size = self.config.archive_size
        return loaded

    def _generator(self, schema: ParameterSchema) -> CandidateGenerator:
        return CandidateGenerator(
            schema,
            seed=self.config.seed,
            mutation_rate=self.config.mutation_rate,
            crossover_rate=self.config.crossover_rate,
            blend_probability=self.config.blend_probability,
            max_attempts_factor=self.config.max_attempts_factor,
            guardrails=self.guardrails,
            mutation_engine=self.mutation,
        )

    # -- generation 0 -------------------------------------------------------------------------------
    def initialize(
        self,
        base_params: Mapping[str, Any],
        schema: ParameterSchema | Mapping[str, Any] | None = None,
        n: int | None = None,
        *,
        definition: Mapping[str, Any] | None = None,
        base_id: str = "base",
    ) -> list[Candidate]:
        """``n`` distinct, guardrail-valid seeded mutations of ``base_params`` (1–3 operators each).

        The base itself is not included (it already exists as the incumbent version). Raises
        :class:`GuardrailViolation` if the base violates its own schema.
        """
        parsed = self._schema(schema)
        count = self.config.population_size if n is None else n
        base = parsed.with_defaults(base_params)
        definition_dict = copy.deepcopy(dict(definition)) if definition is not None else None
        self.guardrails.assert_safe(definition_dict, base, parsed)
        if not parsed.mutable_names:
            raise MutationError("the schema declares no mutable parameters")
        parent = {"definition": definition_dict or {}, "parameters": base}
        known = {param_hash(base)}
        drafts: list[Candidate] = []
        attempt = 0
        budget = max(count, 1) * self.config.max_attempts_factor
        while len(drafts) < count and attempt < budget:
            params = dict(base)
            records: list[MutationRecord] = []
            for s in range(1 + attempt % 3):
                try:
                    params, record = self.mutation.mutate(params, parsed, generation=0, index=attempt, step=s)
                except MutationError:
                    break
                records.append(record.model_copy(update={"parents": (base_id,)}))
            attempt += 1
            digest = param_hash(params)
            if not records or digest in known:
                continue
            if not self.guardrails.validate(definition_dict, params, parsed, parent=parent).ok:
                continue
            known.add(digest)
            drafts.append(
                Candidate(
                    id=f"g0-{len(drafts):03d}-{digest[:10]}",
                    params=params,
                    parents=(base_id,),
                    generation=0,
                    definition=definition_dict,
                    mutations=tuple(records),
                )
            )
        return drafts

    def sample_population(
        self,
        n: int | None = None,
        *,
        schema: ParameterSchema | Mapping[str, Any] | None = None,
        definition: Mapping[str, Any] | None = None,
    ) -> list[Candidate]:
        """``n`` distinct uniformly random valid vectors (bounded schemas only; used by benchmarks)."""
        parsed = self._schema(schema)
        count = self.config.population_size if n is None else n
        definition_dict = copy.deepcopy(dict(definition)) if definition is not None else None
        known: set[str] = set()
        drafts: list[Candidate] = []
        budget = max(count, 1) * self.config.max_attempts_factor
        for index in range(budget):
            if len(drafts) >= count:
                break
            params = self.mutation.sample(parsed, index=index)
            digest = param_hash(params)
            if digest in known or not self.guardrails.validate(definition_dict, params, parsed).ok:
                continue
            known.add(digest)
            drafts.append(
                Candidate(
                    id=f"g0-{len(drafts):03d}-{digest[:10]}", params=params, generation=0, definition=definition_dict
                )
            )
        return drafts

    # -- assessment ---------------------------------------------------------------------------------
    def assess(
        self,
        population: Sequence[Candidate],
        *,
        archive: ArchiveManager | Mapping[str, Any] | None = None,
    ) -> PopulationAssessment:
        """Rank ``population`` and update the ε-archive (no children)."""
        ids = [c.id for c in population]
        if len(set(ids)) != len(ids):
            raise ValueError("population contains duplicate candidate ids")
        notes: list[str] = []
        archive_mgr = self.load_archive(archive)
        schema = self.schema
        names = self.fitness.names
        need_novelty = self.config.novelty_weight > 0 or (
            "novelty" in names and any(c.fitness is not None and c.fitness.get("novelty") is None for c in population)
        )
        novelty: list[float | None] = [None] * len(population)
        if need_novelty:
            if schema is None:
                raise SchemaError("novelty requires a parameter schema")
            in_population = set(ids)
            archived = [e.params for e in archive_mgr if e.id not in in_population]
            scores = DiversityManager(schema).novelty_all(
                [c.params for c in population], archived, k=self.config.novelty_k
            )
            novelty = [round(v, 12) for v in scores]

        vectors: list[FitnessVector] = []
        for i, candidate in enumerate(population):
            if candidate.fitness is None:
                vectors.append(self.fitness.evaluate(None))
                continue
            raw: dict[str, Any] = dict(candidate.fitness)
            if "novelty" in names and raw.get("novelty") is None and novelty[i] is not None:
                raw["novelty"] = novelty[i]
            vectors.append(self.fitness.evaluate(raw))

        ranking = self.selection.rank(vectors)
        order = sorted(range(len(population)), key=lambda i: (ranking.rank[i], i))
        for i in order:
            if vectors[i].feasible:
                candidate = population[i]
                archive_mgr.insert(
                    ArchiveEntry(
                        id=candidate.id,
                        oriented=vectors[i].oriented,
                        objectives=dict(vectors[i].objectives),
                        params=dict(candidate.params),
                        generation=candidate.generation,
                    )
                )

        assessments = {
            population[i].id: CandidateAssessment(
                id=population[i].id,
                rank=ranking.rank[i],
                crowding=ranking.crowding[i],
                feasible=vectors[i].feasible,
                constraint_violation=vectors[i].constraint_violation,
                violations=vectors[i].violations,
                novelty=novelty[i],
                summary=vectors[i].summary,
                objectives=dict(vectors[i].objectives),
                oriented=vectors[i].oriented,
            )
            for i in range(len(population))
        }
        infeasible = sum(1 for v in vectors if not v.feasible)
        unevaluated = sum(1 for c in population if c.fitness is None)
        if unevaluated:
            notes.append(f"{unevaluated} unevaluated candidate(s) treated as infeasible")
        if infeasible:
            notes.append(f"{infeasible} infeasible candidate(s) (constraint violations or missing objectives)")
        duplicates = PopulationManager(population).duplicates()
        if duplicates:
            notes.append(f"{len(duplicates)} group(s) of candidates share identical parameters")
        front = [population[i].id for i in ranking.first_front]
        return PopulationAssessment(
            ids=tuple(ids),
            vectors=tuple(vectors),
            ranking=ranking,
            novelty=tuple(novelty),
            assessments=assessments,
            archive=archive_mgr.to_dict(),
            front=front,
            hypervolume=self._archive_hypervolume(archive_mgr),
            notes=notes,
        )

    def _archive_hypervolume(self, archive: ArchiveManager) -> float | None:
        """Hypervolume of the archive in normalised oriented space (reference = worst bounds)."""
        if not self.fitness.all_bounded or len(archive) == 0:
            return None
        points = [e.oriented for e in archive]
        return round(
            hypervolume(points, [0.0] * len(self.fitness.names), maximize=True, seed=self.config.seed, samples=50_000),
            12,
        )

    # -- one generation -----------------------------------------------------------------------------
    def step(
        self,
        population: Sequence[Candidate],
        generation: int,
        *,
        archive: ArchiveManager | Mapping[str, Any] | None = None,
    ) -> GenerationPlan:
        """Plan generation ``generation``: survivors of ``population`` and ``offspring_size`` children.

        ``population`` is every live candidate (parents plus the evaluated offspring of the previous
        generation — the NSGA-II (μ+λ) pool). Children are labelled with ``generation``.
        """
        if generation < 1:
            raise ValueError("generation must be >= 1 (generation 0 comes from initialize())")
        schema = self._schema()
        if not population:
            raise ValueError("population is empty")
        assessment = self.assess(population, archive=archive)
        ranking = assessment.ranking
        secondary: list[float] | None = None
        if self.config.novelty_weight > 0:
            secondary = [self.config.novelty_weight * (n or 0.0) for n in assessment.novelty]
        survivors_idx = self.selection.environmental_selection(
            ranking, self.config.population_size, secondary=secondary
        )
        survivor_set = set(survivors_idx)
        survivors_ordered = sorted(survivors_idx)
        offspring = self.config.effective_offspring_size
        pool_idx = self.selection.tournament(
            survivors_ordered, ranking, 2 * offspring, generation=generation, secondary=secondary
        )
        mating_pool = [population[i] for i in pool_idx]
        known = {param_hash(c.params) for c in population}
        known.update(param_hash(e["params"]) for e in assessment.archive.get("entries", []))
        batch = self._generator(schema).generate(mating_pool, offspring, generation=generation, existing_hashes=known)
        notes = list(assessment.notes)
        if batch.exhausted:
            notes.append(
                f"retry budget exhausted: produced {len(batch.children)} of {offspring} children "
                f"({len(batch.rejected)} rejected)"
            )
        guardrail_rejections = sum(1 for r in batch.rejected if r.reason == "guardrail")
        if guardrail_rejections:
            notes.append(f"{guardrail_rejections} child(ren) rejected by strategy guardrails")
        diversity = DiversityManager(schema).metrics([population[i].params for i in survivors_ordered])
        return GenerationPlan(
            generation=generation,
            survivors=[population[i].id for i in survivors_ordered],
            eliminated=[c.id for i, c in enumerate(population) if i not in survivor_set],
            children=batch.children,
            archive=assessment.archive,
            front=assessment.front,
            assessments=assessment.assessments,
            diversity=diversity,
            hypervolume=assessment.hypervolume,
            rejected_children=batch.rejected,
            notes=notes,
        )


def survival_transitions(plan: GenerationPlan, statuses: Mapping[str, str]) -> list[tuple[str, str, str]]:
    """Status changes implied by ``plan`` for the given current statuses, as ``(id, from, to)``.

    * survivors: ``EXPERIMENTAL → SURVIVING`` (already SURVIVING: unchanged; CANDIDATE: unchanged
      until evaluated);
    * eliminated: ``CANDIDATE|EXPERIMENTAL|SURVIVING → RETIRED``;
    * ``PROMOTED`` versions are never changed by evolution (only promotion/rollback moves them);
      ``RETIRED``/``ROLLED_BACK`` versions are left untouched.

    Every change is checked against ``engines.lab.states.STRATEGY_TRANSITIONS``.
    """
    changes: list[tuple[str, str, str]] = []
    survivors = set(plan.survivors)
    for candidate_id in [*plan.survivors, *plan.eliminated]:
        current = statuses.get(candidate_id)
        if current is None or current in (StrategyStatus.PROMOTED, StrategyStatus.RETIRED, StrategyStatus.ROLLED_BACK):
            continue
        if candidate_id in survivors:
            target = StrategyStatus.SURVIVING if current == StrategyStatus.EXPERIMENTAL else None
        else:
            target = StrategyStatus.RETIRED
        if target is None or target == current:
            continue
        assert_transition("strategy", current, target)
        changes.append((candidate_id, str(current), str(target)))
    return changes


__all__ = [
    "CandidateAssessment",
    "EvolutionConfig",
    "EvolutionEngine",
    "GenerationPlan",
    "GuardrailViolation",
    "ObjectiveConstraint",
    "PopulationAssessment",
    "survival_transitions",
]
