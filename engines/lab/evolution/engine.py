"""EvolutionEngine: one deterministic generation step.

    Population → Evaluate → Rank → Select → Mutate → Candidates → (experiments run by the platform)
                → Evaluate → Update archive → Promotion gate → Repeat

The engine never runs experiments or promotes anything itself. It turns *measured* metrics into rankings,
archive updates and new candidate definitions; the application layer turns candidates into experiments,
feeds back measurements and asks the policy engine before any promotion.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from engines.lab.evolution.fitness import DEFAULT_OBJECTIVES, FitnessEngine, Objective
from engines.lab.evolution.mutation import MutationEngine
from engines.lab.evolution.population import (
    CandidateGenerator,
    CandidateProposal,
    DiversityManager,
    Individual,
    NicheArchive,
    ParetoArchive,
    PopulationManager,
    SelectionEngine,
)


class EvolutionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    population_size: int = Field(default=8, ge=2, le=512)
    offspring_per_generation: int = Field(default=4, ge=1, le=512)
    mutation_rate: float = Field(default=0.3, gt=0, le=1)
    crossover_rate: float = Field(default=0.3, ge=0, le=1)
    tournament_size: int = Field(default=2, ge=1, le=16)
    archive_size: int = Field(default=50, ge=1, le=5000)
    max_generations: int = Field(default=5, ge=1, le=1000)
    seed: int = 20260930
    objectives: list[Objective] = Field(default_factory=lambda: list(DEFAULT_OBJECTIVES))
    niche_descriptors: list[tuple[str, int]] = Field(default_factory=list)
    novelty_k: int = Field(default=5, ge=1)


@dataclass
class GenerationOutcome:
    generation: int
    fronts: list[list[str]]
    survivors: list[str]
    parents: list[str]
    candidates: list[CandidateProposal]
    rejected_candidates: list[dict[str, Any]]
    archive_entered: list[str]
    archive_size: int
    niche_coverage: float | None
    diversity: float
    best_by_objective: dict[str, str | None] = field(default_factory=dict)
    scores: dict[str, float] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {
            "generation": self.generation,
            "fronts": self.fronts,
            "survivors": self.survivors,
            "parents": self.parents,
            "candidates": [c.definition.parameter_hash() for c in self.candidates],
            "rejected_candidates": self.rejected_candidates,
            "archive_entered": self.archive_entered,
            "archive_size": self.archive_size,
            "niche_coverage": self.niche_coverage,
            "diversity": round(self.diversity, 6),
            "best_by_objective": self.best_by_objective,
            "scores": {k: round(v, 6) for k, v in self.scores.items()},
        }


class EvolutionEngine:
    def __init__(self, config: EvolutionConfig) -> None:
        self.config = config
        self.fitness = FitnessEngine(config.objectives)
        self.population = PopulationManager(self.fitness)
        self.selection = SelectionEngine(config.tournament_size)
        self.diversity = DiversityManager()
        self.generator = CandidateGenerator(MutationEngine(config.mutation_rate), config.crossover_rate)
        self.archive = ParetoArchive(self.fitness.keys, config.archive_size)
        self.niches = NicheArchive(config.niche_descriptors, self.fitness) if config.niche_descriptors else None

    def rng(self, generation: int) -> np.random.Generator:
        return np.random.default_rng([self.config.seed, generation])

    def step(
        self, individuals: Sequence[Individual], generation: int, *, seen_hashes: set[str] | None = None
    ) -> GenerationOutcome:
        rng = self.rng(generation)
        reference = list(individuals) + list(self.archive.members.values())
        for ind in individuals:
            ind.novelty = self.diversity.novelty(ind, reference, self.config.novelty_k)
            if ind.metrics:
                ind.metrics = {**ind.metrics, "novelty": ind.novelty}
        self.population.evaluate(individuals)
        survivors = self.population.survivors(individuals, self.config.population_size)
        fronts = self.population.rank(survivors)
        entered = self.archive.add(survivors)
        coverage = None
        if self.niches is not None:
            self.niches.add(survivors)
            coverage = self.niches.coverage()
        parents = self.selection.select_parents(survivors, self.config.offspring_per_generation, rng)
        seen = set(seen_hashes or set()) | {i.definition.parameter_hash() for i in individuals}
        candidates, rejected = self.generator.generate(
            parents, self.config.offspring_per_generation, rng, seen_hashes=seen, seed=self.config.seed + generation
        )
        evaluated = [i for i in survivors if i.fitness is not None]
        scores = dict(
            zip(
                [i.id for i in evaluated],
                self.fitness.scalarize([i.fitness for i in evaluated if i.fitness]),
                strict=True,
            )
        )
        best: dict[str, str | None] = {}
        feasible = [i for i in evaluated if i.fitness is not None and i.fitness.feasible]
        for key in self.fitness.keys:
            best[key] = _best_for(feasible, key)
        return GenerationOutcome(
            generation=generation,
            fronts=[[i.id for i in f] for f in fronts],
            survivors=[i.id for i in survivors],
            parents=[p.id for p in parents],
            candidates=candidates,
            rejected_candidates=rejected,
            archive_entered=entered,
            archive_size=len(self.archive.members),
            niche_coverage=coverage,
            diversity=self.diversity.population_diversity(survivors),
            best_by_objective=best,
            scores=scores,
        )


def _best_for(individuals: Sequence[Individual], key: str) -> str | None:
    best: Individual | None = None
    for ind in individuals:
        if ind.fitness is None:
            continue
        if (
            best is None
            or best.fitness is None
            or (ind.fitness.values[key], ind.id) > (best.fitness.values[key], best.id)
        ):
            best = ind
    return best.id if best else None
