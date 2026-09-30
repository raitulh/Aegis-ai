"""Hypervolume indicator (the Lebesgue measure dominated by a front, bounded by a reference point).

* 2 objectives — exact sweep, O(n log n).
* ≥ 3 objectives — exact *Hypervolume by Slicing Objectives* (HSO, While et al. 2006) for fronts
  small enough to be tractable, otherwise a seeded Monte-Carlo estimate (deterministic per seed).

Conventions: by default objectives are **minimised** and the reference point is a *nadir* (worse
than every point on every axis). Pass ``maximize=True`` for maximised/oriented vectors — the
reference point is then the *worst* corner (e.g. the zero vector of normalised oriented objectives).
Points that do not strictly dominate the reference point contribute nothing and are ignored.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

import numpy as np

from engines.lab.evolution.rng import make_np_rng

Method = Literal["auto", "exact", "monte_carlo"]

# Largest front size for which ``method="auto"`` still uses the exact HSO, per dimension count.
EXACT_LIMITS: dict[int, int] = {3: 1500, 4: 120, 5: 40}
_MC_CHUNK = 4096


def _nondominated_min(points: Sequence[tuple[float, ...]]) -> list[tuple[float, ...]]:
    """Unique non-dominated subset (minimisation), sorted lexicographically."""
    unique = sorted(set(points))
    kept: list[tuple[float, ...]] = []
    for p in unique:
        dominated = False
        for q in kept:
            if all(qi <= pi for qi, pi in zip(q, p, strict=True)):
                dominated = True
                break
        if dominated:
            continue
        kept = [q for q in kept if not all(pi <= qi for pi, qi in zip(p, q, strict=True))]
        kept.append(p)
    return sorted(kept)


def _hv2d(points: Sequence[tuple[float, ...]], ref: Sequence[float]) -> float:
    """Exact 2-D hypervolume (minimisation) of points strictly dominating ``ref``.

    Sweeps points by ascending first objective; every staircase point adds the horizontal strip
    between its second objective and the previous staircase level. Dominated points add nothing.
    """
    volume = 0.0
    level = ref[1]
    for x, y in sorted(points):
        if y < level:
            volume += (ref[0] - x) * (level - y)
            level = y
    return volume


def _hv_exact(points: list[tuple[float, ...]], ref: Sequence[float]) -> float:
    d = len(ref)
    if not points:
        return 0.0
    if d == 1:
        return ref[0] - min(p[0] for p in points)
    if d == 2:
        return _hv2d(points, ref)
    ordered = sorted(points, key=lambda p: (p[-1], p))
    volume = 0.0
    for i, p in enumerate(ordered):
        upper = ordered[i + 1][-1] if i + 1 < len(ordered) else ref[-1]
        height = upper - p[-1]
        if height <= 0:
            continue
        projection = [q[:-1] for q in ordered[: i + 1]]
        if d > 3:
            projection = _nondominated_min(projection)
        volume += height * _hv_exact(projection, ref[:-1])
    return volume


def _hv_monte_carlo(points: list[tuple[float, ...]], ref: Sequence[float], samples: int, seed: int) -> float:
    front = np.asarray(points, dtype=float)
    reference = np.asarray(ref, dtype=float)
    ideal = front.min(axis=0)
    box = float(np.prod(reference - ideal))
    if box <= 0:
        return 0.0
    rng = make_np_rng(seed, "hypervolume")
    dominated = 0
    remaining = samples
    while remaining > 0:
        size = min(_MC_CHUNK, remaining)
        draws = ideal + rng.random((size, front.shape[1])) * (reference - ideal)
        # a draw is dominated if some front point is <= it on every axis
        covered = (front[None, :, :] <= draws[:, None, :]).all(axis=2).any(axis=1)
        dominated += int(covered.sum())
        remaining -= size
    return box * dominated / samples


def hypervolume(
    points: Sequence[Sequence[float]],
    reference: Sequence[float],
    *,
    maximize: bool = False,
    method: Method = "auto",
    samples: int = 200_000,
    seed: int = 0,
) -> float:
    """Hypervolume of ``points`` w.r.t. ``reference`` (see module docstring for conventions)."""
    ref = [float(r) for r in reference]
    if not ref:
        raise ValueError("reference point must have at least one dimension")
    if not all(np.isfinite(ref)):
        raise ValueError("reference point must be finite")
    sign = -1.0 if maximize else 1.0
    ref_min = [sign * r for r in ref]
    converted: list[tuple[float, ...]] = []
    for p in points:
        if len(p) != len(ref):
            raise ValueError(f"point {list(p)} has {len(p)} objectives, reference has {len(ref)}")
        q = tuple(sign * float(v) for v in p)
        if not all(np.isfinite(q)):
            continue
        if all(qi < ri for qi, ri in zip(q, ref_min, strict=True)):
            converted.append(q)
    if not converted:
        return 0.0
    front = _nondominated_min(converted)
    d = len(ref)
    if method == "monte_carlo" and d >= 2:
        return _hv_monte_carlo(front, ref_min, samples, seed)
    if method == "auto" and d >= 3 and len(front) > EXACT_LIMITS.get(d, 12):
        return _hv_monte_carlo(front, ref_min, samples, seed)
    return _hv_exact(front, ref_min)


def reference_point(
    points: Sequence[Sequence[float]], *, margin: float = 0.1, maximize: bool = False
) -> list[float]:
    """A reference point just beyond the worst observed value on each axis (``margin`` × spread).

    Useful when no problem-specific reference exists; prefer a fixed reference point when comparing
    hypervolumes across runs, because this one moves with the data.
    """
    arr = np.asarray(points, dtype=float)
    if arr.ndim != 2 or arr.shape[0] == 0:
        raise ValueError("points must be a non-empty 2-D array")
    lo, hi = arr.min(axis=0), arr.max(axis=0)
    spread = np.where(hi - lo > 0, hi - lo, 1.0)
    if maximize:
        return [float(v) for v in lo - margin * spread]
    return [float(v) for v in hi + margin * spread]
