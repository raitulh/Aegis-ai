"""Transparent, dependency-light statistics for comparing baseline and candidate runs.

Implements Welch's t-test (Student-t tail via the regularized incomplete beta function), the Mann-Whitney U
test (normal approximation with tie and continuity correction), seeded bootstrap and permutation tests,
Cohen's d / Hedges' g and Holm/Bonferroni multiple-comparison corrections. Everything is deterministic for a
given seed so evaluations are reproducible.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

_EPS = 3e-14
_FPMIN = 1e-300


@dataclass(frozen=True)
class TestResult:
    __test__ = False  # not a pytest class

    test: str
    statistic: float
    p_value: float
    df: float | None = None
    n_a: int = 0
    n_b: int = 0
    note: str = ""


@dataclass(frozen=True)
class Summary:
    n: int
    mean: float
    std: float
    sem: float
    ci_low: float
    ci_high: float
    minimum: float
    maximum: float


def _betacf(a: float, b: float, x: float) -> float:
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > _FPMIN else _FPMIN)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > _FPMIN else _FPMIN)
        c = 1.0 + aa / c
        c = c if abs(c) > _FPMIN else _FPMIN
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > _FPMIN else _FPMIN)
        c = 1.0 + aa / c
        c = c if abs(c) > _FPMIN else _FPMIN
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _EPS:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta function I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    ln_bt = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    bt = math.exp(ln_bt)
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def t_sf_two_sided(t: float, df: float) -> float:
    """Two-sided p-value P(|T| >= |t|) for Student's t with ``df`` degrees of freedom."""
    if df <= 0 or math.isnan(t):
        return 1.0
    if math.isinf(t):
        return 0.0
    x = df / (df + t * t)
    return min(1.0, max(0.0, betainc(df / 2.0, 0.5, x)))


def t_cdf(t: float, df: float) -> float:
    p_two = t_sf_two_sided(t, df)
    return 1.0 - p_two / 2.0 if t >= 0 else p_two / 2.0


def t_ppf(q: float, df: float) -> float:
    """Quantile of Student's t via bisection (adequate precision for confidence intervals)."""
    if not 0.0 < q < 1.0:
        raise ValueError("q must be in (0, 1)")
    lo, hi = -1e3, 1e3
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if t_cdf(mid, df) < q:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def normal_sf_two_sided(z: float) -> float:
    return math.erfc(abs(z) / math.sqrt(2.0))


def _arr(values: Sequence[float]) -> np.ndarray:
    arr = np.asarray(list(values), dtype=float)
    return arr[~np.isnan(arr)]


def describe(values: Sequence[float], confidence: float = 0.95) -> Summary:
    arr = _arr(values)
    n = int(arr.size)
    if n == 0:
        return Summary(
            0, float("nan"), float("nan"), float("nan"), float("nan"), float("nan"), float("nan"), float("nan")
        )
    mean = float(arr.mean())
    std = float(arr.std(ddof=1)) if n > 1 else 0.0
    sem = std / math.sqrt(n) if n > 1 else 0.0
    if n > 1 and sem > 0:
        tq = t_ppf(1 - (1 - confidence) / 2, n - 1)
        lo, hi = mean - tq * sem, mean + tq * sem
    else:
        lo = hi = mean
    return Summary(n, mean, std, sem, lo, hi, float(arr.min()), float(arr.max()))


def welch_t_test(a: Sequence[float], b: Sequence[float]) -> TestResult:
    """Welch's unequal-variance t-test of H0: mean(a) == mean(b)."""
    x, y = _arr(a), _arr(b)
    na, nb = int(x.size), int(y.size)
    if na < 2 or nb < 2:
        return TestResult("welch_t", float("nan"), 1.0, None, na, nb, "need at least 2 observations per group")
    va, vb = float(x.var(ddof=1)), float(y.var(ddof=1))
    se2 = va / na + vb / nb
    diff = float(x.mean() - y.mean())
    if se2 == 0.0:
        if diff == 0.0:
            return TestResult("welch_t", 0.0, 1.0, float(na + nb - 2), na, nb, "zero variance, identical means")
        return TestResult("welch_t", math.copysign(math.inf, diff), 0.0, float(na + nb - 2), na, nb, "zero variance")
    t = diff / math.sqrt(se2)
    df = se2**2 / ((va / na) ** 2 / (na - 1) + (vb / nb) ** 2 / (nb - 1))
    return TestResult("welch_t", t, t_sf_two_sided(t, df), df, na, nb)


def _rankdata(values: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(values.size, dtype=float)
    sorted_vals = values[order]
    i = 0
    while i < values.size:
        j = i
        while j + 1 < values.size and sorted_vals[j + 1] == sorted_vals[i]:
            j += 1
        ranks[order[i : j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return ranks


def mann_whitney_u(a: Sequence[float], b: Sequence[float]) -> TestResult:
    """Two-sided Mann-Whitney U test (normal approximation, tie + continuity corrected)."""
    x, y = _arr(a), _arr(b)
    na, nb = int(x.size), int(y.size)
    if na == 0 or nb == 0:
        return TestResult("mann_whitney", float("nan"), 1.0, None, na, nb, "empty group")
    combined = np.concatenate([x, y])
    ranks = _rankdata(combined)
    r1 = float(ranks[:na].sum())
    u1 = r1 - na * (na + 1) / 2.0
    mu = na * nb / 2.0
    n = na + nb
    _, counts = np.unique(combined, return_counts=True)
    tie_term = float(((counts**3 - counts).sum()) / (n * (n - 1))) if n > 1 else 0.0
    sigma = math.sqrt(na * nb / 12.0 * ((n + 1) - tie_term))
    if sigma == 0:
        return TestResult("mann_whitney", u1, 1.0, None, na, nb, "all values tied")
    z = (u1 - mu - math.copysign(0.5, u1 - mu)) / sigma if u1 != mu else 0.0
    return TestResult("mann_whitney", u1, normal_sf_two_sided(z), None, na, nb, f"z={z:.4f}")


def bootstrap_diff_ci(
    a: Sequence[float], b: Sequence[float], *, confidence: float = 0.95, iterations: int = 4000, seed: int = 20260930
) -> tuple[float, float]:
    """Percentile bootstrap CI for mean(a) - mean(b)."""
    x, y = _arr(a), _arr(b)
    if x.size == 0 or y.size == 0:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    xs = rng.choice(x, size=(iterations, x.size), replace=True).mean(axis=1)
    ys = rng.choice(y, size=(iterations, y.size), replace=True).mean(axis=1)
    diffs = xs - ys
    alpha = (1 - confidence) / 2
    return (float(np.quantile(diffs, alpha)), float(np.quantile(diffs, 1 - alpha)))


def bootstrap_test(
    a: Sequence[float], b: Sequence[float], *, iterations: int = 4000, seed: int = 20260930
) -> TestResult:
    """Bootstrap test of H0: equal means (shift both groups to the pooled mean, resample)."""
    x, y = _arr(a), _arr(b)
    if x.size < 2 or y.size < 2:
        return TestResult("bootstrap", float("nan"), 1.0, None, int(x.size), int(y.size), "insufficient data")
    observed = float(x.mean() - y.mean())
    pooled = float(np.concatenate([x, y]).mean())
    x0, y0 = x - x.mean() + pooled, y - y.mean() + pooled
    rng = np.random.default_rng(seed)
    diffs = rng.choice(x0, size=(iterations, x.size), replace=True).mean(axis=1) - rng.choice(
        y0, size=(iterations, y.size), replace=True
    ).mean(axis=1)
    p = float((np.sum(np.abs(diffs) >= abs(observed) - 1e-12) + 1) / (iterations + 1))
    return TestResult("bootstrap", observed, p, None, int(x.size), int(y.size))


def permutation_test(
    a: Sequence[float], b: Sequence[float], *, iterations: int = 5000, seed: int = 20260930
) -> TestResult:
    """Two-sided permutation test on the difference in means."""
    x, y = _arr(a), _arr(b)
    if x.size == 0 or y.size == 0:
        return TestResult("permutation", float("nan"), 1.0, None, int(x.size), int(y.size), "empty group")
    observed = float(x.mean() - y.mean())
    combined = np.concatenate([x, y])
    rng = np.random.default_rng(seed)
    count = 0
    for _ in range(iterations):
        perm = rng.permutation(combined)
        if abs(perm[: x.size].mean() - perm[x.size :].mean()) >= abs(observed) - 1e-12:
            count += 1
    return TestResult("permutation", observed, (count + 1) / (iterations + 1), None, int(x.size), int(y.size))


def cohens_d(a: Sequence[float], b: Sequence[float]) -> float:
    x, y = _arr(a), _arr(b)
    na, nb = x.size, y.size
    if na < 2 or nb < 2:
        return float("nan")
    pooled = math.sqrt(((na - 1) * x.var(ddof=1) + (nb - 1) * y.var(ddof=1)) / (na + nb - 2))
    diff = float(x.mean() - y.mean())
    if pooled == 0:
        return 0.0 if diff == 0 else math.copysign(math.inf, diff)
    return diff / pooled


def hedges_g(a: Sequence[float], b: Sequence[float]) -> float:
    d = cohens_d(a, b)
    n = len(_arr(a)) + len(_arr(b))
    if math.isnan(d) or math.isinf(d) or n <= 3:
        return d
    return d * (1 - 3 / (4 * n - 9))


def holm_correction(p_values: Sequence[float]) -> list[float]:
    """Holm-Bonferroni step-down adjusted p-values (monotone, capped at 1)."""
    m = len(p_values)
    order = sorted(range(m), key=lambda i: p_values[i])
    adjusted = [0.0] * m
    running = 0.0
    for rank, idx in enumerate(order):
        value = min(1.0, (m - rank) * p_values[idx])
        running = max(running, value)
        adjusted[idx] = running
    return adjusted


def bonferroni_correction(p_values: Sequence[float]) -> list[float]:
    m = len(p_values)
    return [min(1.0, p * m) for p in p_values]


def correct(p_values: Sequence[float], method: str) -> list[float]:
    if method == "holm":
        return holm_correction(p_values)
    if method == "bonferroni":
        return bonferroni_correction(p_values)
    return list(p_values)


def run_test(test: str, a: Sequence[float], b: Sequence[float], *, seed: int = 20260930) -> TestResult:
    if test == "welch_t":
        return welch_t_test(a, b)
    if test == "mann_whitney":
        return mann_whitney_u(a, b)
    if test == "bootstrap":
        return bootstrap_test(a, b, seed=seed)
    if test == "permutation":
        return permutation_test(a, b, seed=seed)
    raise ValueError(f"Unknown statistical test '{test}'")


def relative_change(candidate: float, baseline: float) -> float | None:
    if baseline == 0 or math.isnan(baseline) or math.isnan(candidate):
        return None
    return (candidate - baseline) / abs(baseline)
