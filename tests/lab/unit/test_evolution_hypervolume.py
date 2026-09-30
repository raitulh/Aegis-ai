"""Hypervolume: exact 2-D/3-D/HSO, Monte-Carlo estimate, orientation and reference points."""

from __future__ import annotations

import math

import numpy as np
import pytest

from engines.lab.evolution.hypervolume import hypervolume, reference_point


def brute_force_3d(points: np.ndarray, ref: float = 1.0) -> float:
    """Exact volume via the grid induced by all coordinates (independent reference implementation)."""
    xs = sorted({0.0, ref, *points[:, 0]})
    ys = sorted({0.0, ref, *points[:, 1]})
    volume = 0.0
    for i in range(len(xs) - 1):
        for j in range(len(ys) - 1):
            mask = (points[:, 0] <= xs[i]) & (points[:, 1] <= ys[j])
            if mask.any():
                volume += (xs[i + 1] - xs[i]) * (ys[j + 1] - ys[j]) * (ref - points[mask, 2].min())
    return volume


def test_two_dimensional_exact_values() -> None:
    assert hypervolume([[1, 2], [2, 1]], [3, 3]) == pytest.approx(3.0)
    assert hypervolume([[1, 1]], [2, 2]) == pytest.approx(1.0)
    # dominated points, duplicates and points beyond the reference contribute nothing
    assert hypervolume([[1, 2], [2, 1], [2, 2], [1, 2], [5, 0]], [3, 3]) == pytest.approx(3.0)
    assert hypervolume([], [1, 1]) == 0.0


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_three_dimensional_sweep_matches_brute_force(seed: int) -> None:
    points = np.random.default_rng(seed).random((40, 3))
    assert hypervolume(points.tolist(), [1, 1, 1]) == pytest.approx(brute_force_3d(points), rel=1e-12)


def test_four_dimensional_hso_matches_monte_carlo() -> None:
    points = np.random.default_rng(7).random((25, 4)).tolist()
    exact = hypervolume(points, [1, 1, 1, 1], method="exact")
    estimate = hypervolume(points, [1, 1, 1, 1], method="monte_carlo", samples=400_000, seed=3)
    assert estimate == pytest.approx(exact, rel=0.02)
    assert hypervolume([[0.5] * 4], [1] * 4) == pytest.approx(0.5**4)


def test_monte_carlo_is_deterministic_per_seed() -> None:
    points = np.random.default_rng(1).random((30, 5)).tolist()
    a = hypervolume(points, [1] * 5, method="monte_carlo", samples=20_000, seed=9)
    b = hypervolume(points, [1] * 5, method="monte_carlo", samples=20_000, seed=9)
    c = hypervolume(points, [1] * 5, method="monte_carlo", samples=20_000, seed=10)
    assert a == b and a != c


def test_maximisation_uses_the_worst_corner_as_reference() -> None:
    # union of [0,0.2]x[0,0.8] and [0,0.8]x[0,0.2]
    assert hypervolume([[0.2, 0.8], [0.8, 0.2]], [0, 0], maximize=True) == pytest.approx(0.28)
    assert hypervolume([[-0.1, 0.9]], [0, 0], maximize=True) == 0.0


def test_dense_zdt1_front_approaches_analytic_hypervolume() -> None:
    front = [[x, 1 - math.sqrt(x)] for x in np.linspace(0, 1, 2000)]
    analytic = 0.1 + 2 / 3 + 0.1 * 1.1
    assert hypervolume(front, [1.1, 1.1]) == pytest.approx(analytic, rel=1e-3)


def test_invalid_inputs_are_rejected() -> None:
    with pytest.raises(ValueError):
        hypervolume([[1, 2, 3]], [4, 4])
    with pytest.raises(ValueError):
        hypervolume([[1]], [])
    with pytest.raises(ValueError):
        hypervolume([[1, 1]], [math.inf, 2])


def test_reference_point_helper() -> None:
    assert reference_point([[0, 1], [1, 0]], margin=0.1) == pytest.approx([1.1, 1.1])
    assert reference_point([[0, 1], [1, 0]], margin=0.1, maximize=True) == pytest.approx([-0.1, -0.1])
    with pytest.raises(ValueError):
        reference_point([])
