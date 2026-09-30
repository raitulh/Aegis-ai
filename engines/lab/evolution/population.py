"""Population state, selection, archives, diversity and candidate generation."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from engines.lab.evolution.fitness import FitnessEngine, FitnessVector
from engines.lab.evolution.genome import GuardrailViolation, StrategyDefinition
from engines.lab.evolution.mutation import MutationEngine, MutationRecord
from engines.lab.evolution.pareto import crowding_distance, dominates, non_dominated_sort


@dataclass
class Individual:
    id: str
    definition: StrategyDefinition
    generation: int = 0
    parent_ids: list[str] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    fitness: FitnessVector | None = None
    rank: int | None = None
    crowding: float = 0.0
    novelty: float = 0.0
    evaluations: int = 0

    @property
    def evaluated(self) -> bool:
        return self.fitness is not None


@dataclass
class CandidateProposal:
    definition: StrategyDefinition
    parent_ids: list[str]
    record: MutationRecord


class PopulationManager:
    """Holds a population, computes fitness, ranks (NSGA-II) and truncates to a target size."""

    def __init__(self, fitness: FitnessEngine) -> None:
        self.fitness = fitness

    def evaluate(self, individuals: Sequence[Individual]) -> None:
        for ind in individuals:
            if ind.metrics:
                ind.fitness = self.fitness.compute(ind.metrics)

    def rank(self, individuals: Sequence[Individual]) -> list[list[Individual]]:
        evaluated = [i for i in individuals if i.fitness is not None]
        vectors = [i.fitness for i in evaluated if i.fitness is not None]
        fronts_idx = non_dominated_sort(vectors, self.fitness.keys)
        fronts: list[list[Individual]] = []
        for r, front in enumerate(fronts_idx):
            distances = crowding_distance(vectors, front, self.fitness.keys)
            members = []
            for idx in front:
                evaluated[idx].rank = r
                evaluated[idx].crowding = distances[idx]
                members.append(evaluated[idx])
            fronts.append(members)
        return fronts

    def survivors(self, individuals: Sequence[Individual], size: int) -> list[Individual]:
        """NSGA-II environmental selection: fill by front, break the last front by crowding distance."""
        chosen: list[Individual] = []
        for front in self.rank(individuals):
            if len(chosen) + len(front) <= size:
                chosen.extend(front)
            else:
                remaining = size - len(chosen)
                chosen.extend(sorted(front, key=lambda i: (-i.crowding, -i.novelty, i.id))[:remaining])
                break
        return chosen


class SelectionEngine:
    def __init__(self, tournament_size: int = 2) -> None:
        if tournament_size < 1:
            raise ValueError("tournament_size must be >= 1")
        self.tournament_size = tournament_size

    @staticmethod
    def _key(ind: Individual) -> tuple[float, float, float]:
        return (ind.rank if ind.rank is not None else math.inf, -ind.crowding, -ind.novelty)

    def select_parents(self, ranked: Sequence[Individual], count: int, rng: np.random.Generator) -> list[Individual]:
        pool = [i for i in ranked if i.rank is not None]
        if not pool:
            return []
        parents: list[Individual] = []
        for _ in range(count):
            contenders = [pool[int(rng.integers(len(pool)))] for _ in range(self.tournament_size)]
            parents.append(min(contenders, key=lambda i: (*self._key(i), i.id)))
        return parents


class DiversityManager:
    @staticmethod
    def distance(a: StrategyDefinition, b: StrategyDefinition) -> float:
        va, vb = a.normalized_vector(), b.normalized_vector()
        if len(va) != len(vb) or not va:
            return 1.0
        return math.sqrt(sum((x - y) ** 2 for x, y in zip(va, vb, strict=True)) / len(va))

    def novelty(self, ind: Individual, reference: Sequence[Individual], k: int = 5) -> float:
        dists = sorted(self.distance(ind.definition, r.definition) for r in reference if r.id != ind.id)
        if not dists:
            return 1.0
        nearest = dists[:k]
        return sum(nearest) / len(nearest)

    def population_diversity(self, individuals: Sequence[Individual]) -> float:
        n = len(individuals)
        if n < 2:
            return 0.0
        total = sum(
            self.distance(individuals[i].definition, individuals[j].definition)
            for i in range(n)
            for j in range(i + 1, n)
        )
        return total / (n * (n - 1) / 2)


class ParetoArchive:
    """Bounded archive of non-dominated individuals across all generations."""

    def __init__(self, keys: Sequence[str], max_size: int = 50) -> None:
        self.keys = list(keys)
        self.max_size = max_size
        self.members: dict[str, Individual] = {}

    def add(self, individuals: Sequence[Individual]) -> list[str]:
        """Insert individuals; returns ids that entered the archive."""
        entered: list[str] = []
        for ind in individuals:
            if ind.fitness is None or not ind.fitness.feasible:
                continue
            fit = ind.fitness
            if any(m.fitness is not None and dominates(m.fitness, fit, self.keys) for m in self.members.values()):
                continue
            self.members = {
                mid: m
                for mid, m in self.members.items()
                if m.fitness is None or not dominates(fit, m.fitness, self.keys)
            }
            self.members[ind.id] = ind
            entered.append(ind.id)
        if len(self.members) > self.max_size:
            ids = sorted(self.members)
            vectors = [self.members[i].fitness for i in ids]
            distances = crowding_distance([v for v in vectors if v is not None], list(range(len(ids))), self.keys)
            keep = sorted(range(len(ids)), key=lambda i: (-distances[i], ids[i]))[: self.max_size]
            self.members = {ids[i]: self.members[ids[i]] for i in keep}
            entered = [e for e in entered if e in self.members]
        return entered


class NicheArchive:
    """MAP-Elites style archive: best individual per behavioural niche (binned parameter descriptors)."""

    def __init__(self, descriptors: Sequence[tuple[str, int]], fitness: FitnessEngine) -> None:
        self.descriptors = list(descriptors)
        self.fitness = fitness
        self.cells: dict[tuple[int, ...], Individual] = {}

    def cell_of(self, ind: Individual) -> tuple[int, ...]:
        space = ind.definition.space()
        cell: list[int] = []
        for name, bins in self.descriptors:
            value = space[name].normalize(ind.definition.parameters[name])
            cell.append(min(int(value * bins), bins - 1))
        return tuple(cell)

    def add(self, individuals: Sequence[Individual]) -> int:
        improved = 0
        for ind in individuals:
            if ind.fitness is None or not ind.fitness.feasible:
                continue
            cell = self.cell_of(ind)
            incumbent = self.cells.get(cell)
            if incumbent is None or incumbent.fitness is None:
                self.cells[cell] = ind
                improved += 1
                continue
            scores = self.fitness.scalarize([incumbent.fitness, ind.fitness])
            if scores[1] > scores[0]:
                self.cells[cell] = ind
                improved += 1
        return improved

    def coverage(self) -> float:
        total = 1
        for _, bins in self.descriptors:
            total *= bins
        return len(self.cells) / total if total else 0.0


class CandidateGenerator:
    def __init__(self, mutation: MutationEngine, crossover_rate: float = 0.3) -> None:
        self.mutation = mutation
        self.crossover_rate = crossover_rate

    def generate(
        self,
        parents: Sequence[Individual],
        count: int,
        rng: np.random.Generator,
        *,
        seen_hashes: set[str] | None = None,
        seed: int | None = None,
        max_attempts_factor: int = 8,
    ) -> tuple[list[CandidateProposal], list[dict[str, Any]]]:
        """Produce ``count`` unique, guardrail-checked candidates. Returns (candidates, rejections)."""
        seen = set(seen_hashes or set())
        out: list[CandidateProposal] = []
        rejected: list[dict[str, Any]] = []
        if not parents:
            return out, rejected
        attempts = 0
        while len(out) < count and attempts < count * max_attempts_factor:
            attempts += 1
            a = parents[int(rng.integers(len(parents)))]
            try:
                if len(parents) > 1 and rng.random() < self.crossover_rate:
                    b = parents[int(rng.integers(len(parents)))]
                    child, record = self.mutation.crossover(a.definition, b.definition, rng, seed=seed)
                    child, extra = self.mutation.mutate(child, rng, seed=seed)
                    record.changes.extend(extra.changes)
                    record.operator = "crossover+mutation"
                    parent_ids = sorted({a.id, b.id})
                else:
                    child, record = self.mutation.mutate(a.definition, rng, seed=seed)
                    parent_ids = [a.id]
            except GuardrailViolation as exc:
                rejected.append({"parent": a.id, "reason": str(exc)})
                continue
            h = child.parameter_hash()
            if h in seen:
                continue
            seen.add(h)
            out.append(CandidateProposal(definition=child, parent_ids=parent_ids, record=record))
        return out, rejected
