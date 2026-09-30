"""Small, seeded bootstrap helpers for the promotion gate and benchmark comparisons.

Deliberately minimal: percentile bootstrap confidence intervals for a difference of means
(independent or paired samples). The general statistics engine lives elsewhere; these helpers exist
so the promotion decision is self-contained, reproducible and auditable.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np

from engines.lab.evolution.rng import make_np_rng

Method = Literal["independent", "paired"]


@dataclass(frozen=True)
class BootstrapResult:
    """Point estimate and percentile CI of ``mean(candidate) − mean(incumbent)`` (or relative)."""

    estimate: float
    lower: float
    upper: float
    ci_level: float
    resamples: int
    method: Method
    n_candidate: int
    n_incumbent: int
    relative: bool = False

    def excludes_zero(self) -> bool:
        return self.lower > 0 or self.upper < 0

    def as_dict(self) -> dict[str, object]:
        return {
            "estimate": self.estimate,
            "ci": [self.lower, self.upper],
            "ci_level": self.ci_level,
            "resamples": self.resamples,
            "method": self.method,
            "n_candidate": self.n_candidate,
            "n_incumbent": self.n_incumbent,
            "relative": self.relative,
        }


def _clean(values: Sequence[float]) -> np.ndarray:
    arr = np.asarray([float(v) for v in values], dtype=float)
    if arr.size and not np.all(np.isfinite(arr)):
        raise ValueError("samples must be finite")
    return arr


def _percentiles(draws: np.ndarray, ci_level: float) -> tuple[float, float]:
    alpha = (1.0 - ci_level) / 2.0
    lo, hi = np.quantile(draws, [alpha, 1.0 - alpha])
    return float(lo), float(hi)


def bootstrap_mean_difference(
    candidate: Sequence[float],
    incumbent: Sequence[float],
    *,
    ci_level: float = 0.95,
    resamples: int = 2000,
    seed: int = 0,
    paired: bool = False,
    relative: bool = False,
) -> BootstrapResult:
    """Percentile-bootstrap CI for ``mean(candidate) − mean(incumbent)``.

    ``paired=True`` resamples per-seed differences (requires equal lengths — samples at index *i*
    share seed *i*). ``relative=True`` reports ``(mean_c − mean_i) / |mean_i|`` (resample-wise);
    it raises ``ValueError`` when the incumbent mean is zero, where a relative change is undefined.
    """
    if not 0 < ci_level < 1:
        raise ValueError("ci_level must be in (0, 1)")
    if resamples < 100:
        raise ValueError("resamples must be >= 100")
    c = _clean(candidate)
    i = _clean(incumbent)
    if c.size == 0 or i.size == 0:
        raise ValueError("both samples must be non-empty")
    if relative and math.isclose(float(i.mean()), 0.0, abs_tol=1e-12):
        raise ValueError("relative difference is undefined for a zero incumbent mean")
    rng = make_np_rng(seed, "bootstrap", "paired" if paired else "independent")
    if paired:
        if c.size != i.size:
            raise ValueError("paired bootstrap requires samples of equal length")
        idx = rng.integers(0, c.size, size=(resamples, c.size))
        c_means = c[idx].mean(axis=1)
        i_means = i[idx].mean(axis=1)
    else:
        c_means = c[rng.integers(0, c.size, size=(resamples, c.size))].mean(axis=1)
        i_means = i[rng.integers(0, i.size, size=(resamples, i.size))].mean(axis=1)
    if relative:
        with np.errstate(divide="ignore", invalid="ignore"):
            draws = (c_means - i_means) / np.abs(i_means)
        draws = draws[np.isfinite(draws)]
        estimate = float((c.mean() - i.mean()) / abs(i.mean()))
    else:
        draws = c_means - i_means
        estimate = float(c.mean() - i.mean())
    lower, upper = _percentiles(draws, ci_level) if draws.size else (estimate, estimate)
    return BootstrapResult(
        estimate=round(estimate, 12),
        lower=round(lower, 12),
        upper=round(upper, 12),
        ci_level=ci_level,
        resamples=resamples,
        method="paired" if paired else "independent",
        n_candidate=int(c.size),
        n_incumbent=int(i.size),
        relative=relative,
    )


def bootstrap_mean_ci(
    values: Sequence[float], *, ci_level: float = 0.95, resamples: int = 2000, seed: int = 0
) -> tuple[float, float, float]:
    """``(mean, lower, upper)`` percentile-bootstrap CI of the mean of ``values`` (e.g. paired deltas)."""
    if not 0 < ci_level < 1:
        raise ValueError("ci_level must be in (0, 1)")
    arr = _clean(values)
    if arr.size == 0:
        raise ValueError("values must be non-empty")
    mean = float(arr.mean())
    if arr.size == 1:
        return mean, mean, mean
    rng = make_np_rng(seed, "bootstrap", "mean")
    draws = arr[rng.integers(0, arr.size, size=(resamples, arr.size))].mean(axis=1)
    lower, upper = _percentiles(draws, ci_level)
    return round(mean, 12), round(lower, 12), round(upper, 12)
