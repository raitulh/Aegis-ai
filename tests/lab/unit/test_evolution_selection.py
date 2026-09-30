"""NSGA-II: non-dominated sorting, crowding distance, constrained domination, selection, tournament."""

from __future__ import annotations

import math

import pytest

from engines.lab.evolution.selection import (
    SelectionEngine,
    constrained_dominates,
    crowding_distance,
    fast_non_dominated_sort,
    pareto_dominates,
)
from engines.lab.evolution.types import FitnessVector


def fv(*values: float, cv: float = 0.0) -> FitnessVector:
    return FitnessVector(objectives={}, oriented=tuple(values), feasible=cv == 0, constraint_violation=cv, summary=0.0)


def test_pareto_domination_requires_weakly_better_everywhere_and_strictly_somewhere() -> None:
    assert pareto_dominates((2, 2), (1, 1))
    assert pareto_dominates((2, 1), (1, 1))
    assert not pareto_dominates((1, 1), (1, 1))
    assert not pareto_dominates((2, 0), (1, 1))
    with pytest.raises(ValueError):
        pareto_dominates((1, 2), (1, 2, 3))


def test_fast_non_dominated_sort_on_hand_made_fronts() -> None:
    # maximisation: F0 = {(3,1), (2,2), (1,3)}; F1 = {(2,1), (1,2)}; F2 = {(1,1)}; F3 = {(0,0)}
    points = [(1, 1), (3, 1), (0, 0), (2, 2), (2, 1), (1, 3), (1, 2)]
    assert fast_non_dominated_sort([fv(*p) for p in points]) == [[1, 3, 5], [4, 6], [0], [2]]


def test_identical_points_share_a_front() -> None:
    assert fast_non_dominated_sort([fv(1, 1), fv(1, 1), fv(0, 0)]) == [[0, 1], [2]]


def test_infeasible_never_dominates_feasible_however_good() -> None:
    brilliant_but_unsafe = fv(100, 100, cv=0.001)
    poor_but_feasible = fv(0, 0)
    assert constrained_dominates(poor_but_feasible, brilliant_but_unsafe)
    assert not constrained_dominates(brilliant_but_unsafe, poor_but_feasible)
    assert fast_non_dominated_sort([brilliant_but_unsafe, poor_but_feasible]) == [[1], [0]]


def test_infeasible_solutions_are_ordered_by_total_violation() -> None:
    slight, severe = fv(0, 0, cv=0.1), fv(9, 9, cv=0.5)
    assert constrained_dominates(slight, severe)
    assert not constrained_dominates(severe, slight)
    assert not constrained_dominates(fv(0, 0, cv=0.2), fv(9, 9, cv=0.2))  # equal violation: incomparable


def test_crowding_distance_boundaries_are_infinite_and_interior_normalised() -> None:
    # obj0 spread 4: (0,4),(1,3),(2,1),(4,0)
    distances = crowding_distance([(0, 4), (1, 3), (2, 1), (4, 0)])
    assert distances[0] == math.inf and distances[3] == math.inf
    assert distances[1] == pytest.approx((2 - 0) / 4 + (4 - 1) / 4)
    assert distances[2] == pytest.approx((4 - 1) / 4 + (3 - 0) / 4)


def test_crowding_small_fronts_and_zero_spread() -> None:
    assert crowding_distance([]) == []
    assert crowding_distance([(1, 1), (2, 0)]) == [math.inf, math.inf]
    assert crowding_distance([(1, 1)] * 4) == [0.0, 0.0, 0.0, 0.0]


def test_rank_assigns_front_index_and_crowding() -> None:
    ranking = SelectionEngine.rank([fv(1, 1), fv(3, 1), fv(2, 2), fv(1, 3)])
    assert ranking.fronts == ((1, 2, 3), (0,))
    assert ranking.rank == (1, 0, 0, 0)
    assert ranking.crowding[1] == math.inf and ranking.crowding[3] == math.inf


def test_environmental_selection_fills_by_rank_then_crowding() -> None:
    vectors = [fv(4, 0), fv(0, 4), fv(2, 2), fv(3, 0), fv(1.9, 0.1), fv(1.5, 1.5), fv(0, 3)]
    ranking = SelectionEngine.rank(vectors)
    chosen = SelectionEngine.environmental_selection(ranking, 5)
    assert set(chosen[:3]) == {0, 1, 2}  # whole first front
    assert set(chosen[3:]) == {3, 6}  # the last front's boundary solutions (infinite crowding)
    assert SelectionEngine.environmental_selection(ranking, 100) == [0, 1, 2, 3, 4, 5, 6]


def test_environmental_selection_novelty_bonus_breaks_ties() -> None:
    vectors = [fv(1, 1)] * 4  # one front, identical → zero crowding everywhere
    ranking = SelectionEngine.rank(vectors)
    assert SelectionEngine.environmental_selection(ranking, 2) == [0, 1]
    assert SelectionEngine.environmental_selection(ranking, 2, secondary=[0.0, 0.1, 0.0, 0.9]) == [3, 1]


def test_tournament_is_deterministic_per_seed_and_generation() -> None:
    ranking = SelectionEngine.rank([fv(i % 3, (7 * i) % 5) for i in range(12)])
    pool = list(range(12))
    a = SelectionEngine(seed=42).tournament(pool, ranking, 40, generation=3)
    b = SelectionEngine(seed=42).tournament(pool, ranking, 40, generation=3)
    c = SelectionEngine(seed=43).tournament(pool, ranking, 40, generation=3)
    d = SelectionEngine(seed=42).tournament(pool, ranking, 40, generation=4)
    assert a == b
    assert a != c and a != d


def test_tournament_prefers_lower_rank_then_larger_crowding() -> None:
    ranking = SelectionEngine.rank([fv(2, 2), fv(1, 1)])  # index 0 dominates index 1
    assert set(SelectionEngine(seed=1).tournament([0, 1], ranking, 25)) == {0}
    front = SelectionEngine.rank([fv(0, 4), fv(1, 3), fv(2, 2), fv(4, 0)])  # 1 and 2 are interior
    winners = SelectionEngine(seed=1).tournament([0, 1], front, 25)
    assert set(winners) == {0}  # boundary (inf crowding) beats interior within the same rank
    with pytest.raises(ValueError):
        SelectionEngine().tournament([], ranking, 1)
