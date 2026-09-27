"""Transparent statistics helpers (numpy only, seeded for reproducibility)."""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np


def mean(values: Sequence[float]) -> float:
    return float(np.mean(values)) if len(values) else 0.0


def std(values: Sequence[float]) -> float:
    return float(np.std(values, ddof=1)) if len(values) > 1 else 0.0


def bootstrap_ci(
    values: Sequence[float], *, confidence: float = 0.95, iterations: int = 2000, seed: int = 7
) -> tuple[float, float]:
    """Percentile bootstrap CI for the mean."""
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        return (0.0, 0.0)
    if arr.size == 1:
        return (float(arr[0]), float(arr[0]))
    rng = np.random.default_rng(seed)
    samples = rng.choice(arr, size=(iterations, arr.size), replace=True).mean(axis=1)
    alpha = (1 - confidence) / 2
    return (float(np.quantile(samples, alpha)), float(np.quantile(samples, 1 - alpha)))


def paired_permutation_pvalue(deltas: Sequence[float], *, iterations: int = 5000, seed: int = 11) -> float:
    """Two-sided sign-flip permutation test for H0: mean paired difference == 0."""
    arr = np.asarray(deltas, dtype=float)
    if arr.size == 0 or np.allclose(arr, 0):
        return 1.0
    observed = abs(arr.mean())
    rng = np.random.default_rng(seed)
    signs = rng.choice([-1.0, 1.0], size=(iterations, arr.size))
    permuted = np.abs((signs * arr).mean(axis=1))
    return float((np.sum(permuted >= observed - 1e-12) + 1) / (iterations + 1))


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def two_proportion_ci(x1: int, n1: int, x2: int, n2: int, z: float = 1.96) -> tuple[float, float]:
    """Normal-approximation CI for p1 - p2."""
    if n1 == 0 or n2 == 0:
        return (0.0, 0.0)
    p1, p2 = x1 / n1, x2 / n2
    se = math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
    diff = p1 - p2
    return (diff - z * se, diff + z * se)
