"""Pareto dominance (constraint-dominance), non-dominated sorting, crowding distance and hypervolume."""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

from engines.lab.evolution.fitness import FitnessVector


def dominates(a: FitnessVector, b: FitnessVector, keys: Sequence[str]) -> bool:
    """Constraint-dominance (Deb 2000): feasible beats infeasible; infeasible compare by violation;
    otherwise ``a`` is no worse in every objective and strictly better in at least one."""
    if a.feasible and not b.feasible:
        return True
    if not a.feasible and b.feasible:
        return False
    if not a.feasible and not b.feasible:
        return a.violations < b.violations
    better = False
    for key in keys:
        av, bv = a.values[key], b.values[key]
        if av < bv:
            return False
        if av > bv:
            better = True
    return better


def non_dominated_sort(vectors: Sequence[FitnessVector], keys: Sequence[str]) -> list[list[int]]:
    """Fast non-dominated sort (NSGA-II). Returns fronts as lists of indices, best front first."""
    n = len(vectors)
    dominated_by: list[list[int]] = [[] for _ in range(n)]
    domination_count = [0] * n
    fronts: list[list[int]] = [[]]
    for p in range(n):
        for q in range(n):
            if p == q:
                continue
            if dominates(vectors[p], vectors[q], keys):
                dominated_by[p].append(q)
            elif dominates(vectors[q], vectors[p], keys):
                domination_count[p] += 1
        if domination_count[p] == 0:
            fronts[0].append(p)
    i = 0
    while i < len(fronts) and fronts[i]:
        nxt: list[int] = []
        for p in fronts[i]:
            for q in dominated_by[p]:
                domination_count[q] -= 1
                if domination_count[q] == 0:
                    nxt.append(q)
        i += 1
        if nxt:
            fronts.append(sorted(nxt))
    return [sorted(f) for f in fronts if f]


def crowding_distance(vectors: Sequence[FitnessVector], front: Sequence[int], keys: Sequence[str]) -> dict[int, float]:
    distance = dict.fromkeys(front, 0.0)
    if len(front) <= 2:
        return dict.fromkeys(front, math.inf)
    for key in keys:
        ordered = sorted(front, key=lambda i: (vectors[i].values[key], i))
        lo, hi = vectors[ordered[0]].values[key], vectors[ordered[-1]].values[key]
        distance[ordered[0]] = distance[ordered[-1]] = math.inf
        span = hi - lo
        if span == 0 or math.isinf(span) or math.isnan(span):
            continue
        for j in range(1, len(ordered) - 1):
            prev_v, next_v = vectors[ordered[j - 1]].values[key], vectors[ordered[j + 1]].values[key]
            if math.isinf(prev_v) or math.isinf(next_v):
                continue
            distance[ordered[j]] += (next_v - prev_v) / span
    return distance


def hypervolume(
    points: Sequence[Sequence[float]], reference: Sequence[float], *, samples: int = 20_000, seed: int = 7
) -> float:
    """Hypervolume of a set of *maximization* points w.r.t. a reference point dominated by all of them.

    Exact for two objectives; seeded Monte Carlo estimate for three or more.
    """
    pts = [tuple(float(x) for x in p) for p in points if all(pi > ri for pi, ri in zip(p, reference, strict=True))]
    if not pts:
        return 0.0
    dims = len(reference)
    if dims == 2:
        pts.sort(key=lambda p: (-p[0], -p[1]))
        volume, best_y = 0.0, reference[1]
        for x, y in pts:
            if y > best_y:
                volume += (x - reference[0]) * (y - best_y)
                best_y = y
        return volume
    arr = np.asarray(pts)
    upper = arr.max(axis=0)
    ref = np.asarray(reference, dtype=float)
    rng = np.random.default_rng(seed)
    samples_arr = rng.uniform(ref, upper, size=(samples, dims))
    dominated = np.zeros(samples, dtype=bool)
    for p in arr:
        dominated |= np.all(samples_arr <= p, axis=1)
    return float(dominated.mean() * np.prod(upper - ref))
