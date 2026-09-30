"""NSGA-II selection with constrained domination (Deb, Pratap, Agarwal & Meyarivan, 2002).

* :func:`pareto_dominates` — plain Pareto domination on oriented (all-maximised) vectors.
* :func:`constrained_dominates` — Deb's constrained domination: a feasible solution dominates any
  infeasible one; between infeasible solutions the smaller total violation dominates; between
  feasible solutions Pareto domination applies.
* :func:`fast_non_dominated_sort` — the O(M·N²) front-peeling algorithm.
* :func:`crowding_distance` — per-front crowding; boundary solutions of every objective with a
  non-zero spread get ``inf``.
* :meth:`SelectionEngine.environmental_selection` — (μ+λ) truncation: whole fronts by rank, then the
  last partially-fitting front by descending crowding distance.
* :meth:`SelectionEngine.tournament` — binary crowded tournament (rank, then crowding), seeded.

Every tie is broken deterministically (index order or a seeded draw), so the same inputs and seed
always yield the same selection.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from engines.lab.evolution.rng import make_rng
from engines.lab.evolution.types import FitnessVector

INF = math.inf


def pareto_dominates(a: Sequence[float], b: Sequence[float]) -> bool:
    """``a`` Pareto-dominates ``b`` (maximisation): no worse everywhere and strictly better somewhere."""
    if len(a) != len(b):
        raise ValueError("vectors must have the same number of objectives")
    strictly_better = False
    for x, y in zip(a, b, strict=True):
        if x < y:
            return False
        if x > y:
            strictly_better = True
    return strictly_better


def constrained_dominates(a: FitnessVector, b: FitnessVector) -> bool:
    """Deb's constrained-domination relation."""
    if a.feasible and not b.feasible:
        return True
    if not a.feasible and b.feasible:
        return False
    if not a.feasible and not b.feasible:
        return a.constraint_violation < b.constraint_violation
    return pareto_dominates(a.oriented, b.oriented)


def fast_non_dominated_sort(vectors: Sequence[FitnessVector]) -> list[list[int]]:
    """Partition indices of ``vectors`` into fronts F0, F1, … (each sorted ascending)."""
    n = len(vectors)
    dominated_by_me: list[list[int]] = [[] for _ in range(n)]
    domination_count = [0] * n
    for p in range(n):
        for q in range(p + 1, n):
            if constrained_dominates(vectors[p], vectors[q]):
                dominated_by_me[p].append(q)
                domination_count[q] += 1
            elif constrained_dominates(vectors[q], vectors[p]):
                dominated_by_me[q].append(p)
                domination_count[p] += 1
    fronts: list[list[int]] = []
    current = [i for i in range(n) if domination_count[i] == 0]
    while current:
        fronts.append(sorted(current))
        following: list[int] = []
        for p in current:
            for q in dominated_by_me[p]:
                domination_count[q] -= 1
                if domination_count[q] == 0:
                    following.append(q)
        current = following
    return fronts


def crowding_distance(points: Sequence[Sequence[float]]) -> list[float]:
    """Crowding distance of each point within one front (same order as ``points``).

    For each objective the points are sorted (ties by index); the two extremes get ``inf`` and inner
    points accumulate the normalised gap between their neighbours. Objectives with zero spread add
    nothing. Fronts of one or two points are all boundary points.
    """
    m = len(points)
    if m == 0:
        return []
    if m <= 2:
        return [INF] * m
    n_obj = len(points[0])
    distance = [0.0] * m
    for k in range(n_obj):
        order = sorted(range(m), key=lambda i: (points[i][k], i))
        lo, hi = points[order[0]][k], points[order[-1]][k]
        spread = hi - lo
        if spread <= 0:
            continue
        distance[order[0]] = INF
        distance[order[-1]] = INF
        for j in range(1, m - 1):
            idx = order[j]
            if distance[idx] == INF:
                continue
            distance[idx] += (points[order[j + 1]][k] - points[order[j - 1]][k]) / spread
    return distance


@dataclass(frozen=True)
class Ranking:
    """Result of non-dominated sorting + crowding over a population (indices refer to the input)."""

    fronts: tuple[tuple[int, ...], ...]
    rank: tuple[int, ...]
    crowding: tuple[float, ...]

    @property
    def first_front(self) -> tuple[int, ...]:
        return self.fronts[0] if self.fronts else ()


class SelectionEngine:
    """Seeded NSGA-II ranking, environmental selection and mating selection."""

    def __init__(self, seed: int = 0) -> None:
        self.seed = seed

    @staticmethod
    def rank(vectors: Sequence[FitnessVector]) -> Ranking:
        """Non-dominated sort and per-front crowding distance."""
        fronts = fast_non_dominated_sort(vectors)
        rank = [0] * len(vectors)
        crowding = [0.0] * len(vectors)
        for r, front in enumerate(fronts):
            distances = crowding_distance([vectors[i].oriented for i in front])
            for i, d in zip(front, distances, strict=True):
                rank[i] = r
                crowding[i] = d
        return Ranking(fronts=tuple(tuple(f) for f in fronts), rank=tuple(rank), crowding=tuple(crowding))

    @staticmethod
    def environmental_selection(ranking: Ranking, size: int, *, secondary: Sequence[float] | None = None) -> list[int]:
        """Pick ``size`` survivors: whole fronts in rank order, then the last front by crowding.

        ``secondary`` (e.g. a novelty bonus) is added to the crowding distance of the last front and
        breaks ties between equally crowded solutions; remaining ties go to the lower index.
        """
        if size < 0:
            raise ValueError("size must be >= 0")
        chosen: list[int] = []
        for front in ranking.fronts:
            if len(chosen) + len(front) <= size:
                chosen.extend(front)
                continue
            remaining = size - len(chosen)
            if remaining > 0:

                def key(i: int) -> tuple[float, float, int]:
                    bonus = secondary[i] if secondary is not None else 0.0
                    return (-(ranking.crowding[i] + bonus), -bonus, i)

                chosen.extend(sorted(front, key=key)[:remaining])
            break
        return chosen

    def tournament(
        self,
        pool: Sequence[int],
        ranking: Ranking,
        n: int,
        *,
        generation: int = 0,
        secondary: Sequence[float] | None = None,
    ) -> list[int]:
        """Binary crowded tournament: ``n`` winners drawn from ``pool`` (indices into ``ranking``).

        Lower rank wins; equal ranks → larger crowding (+ ``secondary``) wins; full ties → the first
        drawn contestant (the draw order itself is random, so this is a fair seeded coin).
        """
        if not pool:
            raise ValueError("tournament pool is empty")
        rng = make_rng(self.seed, "tournament", generation)
        winners: list[int] = []
        members = list(pool)
        for _ in range(n):
            if len(members) == 1:
                winners.append(members[0])
                continue
            a, b = rng.sample(members, 2)
            winners.append(self._crowded_winner(a, b, ranking, secondary))
        return winners

    @staticmethod
    def _crowded_winner(a: int, b: int, ranking: Ranking, secondary: Sequence[float] | None) -> int:
        if ranking.rank[a] != ranking.rank[b]:
            return a if ranking.rank[a] < ranking.rank[b] else b
        bonus_a = secondary[a] if secondary is not None else 0.0
        bonus_b = secondary[b] if secondary is not None else 0.0
        ca, cb = ranking.crowding[a] + bonus_a, ranking.crowding[b] + bonus_b
        if ca != cb:
            return a if ca > cb else b
        return a
