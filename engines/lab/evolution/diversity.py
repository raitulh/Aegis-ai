"""Parameter-space diversity and novelty.

Distances are computed per parameter and averaged, so every parameter contributes in [0, 1]:

* ``int``/``float`` — ``|a − b| / (max − min)`` (unbounded: relative difference, capped at 1);
* ``bool``/``choice`` — 0 if equal, else 1;
* ``ordered_list`` — Jaccard distance ``1 − |A ∩ B| / |A ∪ B|`` of the item sets (two empty lists: 0);
* a parameter missing on one side — 1.

Novelty (Lehman & Stanley) is the mean distance to the *k* nearest neighbours among a reference set
(archive + population); niche count is the fitness-sharing sum ``Σ max(0, 1 − (d/σ)^α)``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict

from engines.lab.evolution.types import ParameterSchema, ParameterSpec, ParamType, canonical_hash

_MISSING = object()


class DiversityMetrics(BaseModel):
    """Diversity summary of a set of parameter vectors."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    n: int
    unique_ratio: float
    mean_pairwise_distance: float
    min_pairwise_distance: float
    max_pairwise_distance: float


def _numeric_distance(spec: ParameterSpec, a: float, b: float) -> float:
    span = spec.value_range
    if span is not None:
        return 0.0 if span == 0 else min(1.0, abs(a - b) / span)
    denom = max(abs(a), abs(b), 1.0)
    return min(1.0, abs(a - b) / denom)


def _jaccard_distance(a: Sequence[Any], b: Sequence[Any]) -> float:
    set_a = {repr(x) for x in a}
    set_b = {repr(x) for x in b}
    union = set_a | set_b
    if not union:
        return 0.0
    return 1.0 - len(set_a & set_b) / len(union)


class DiversityManager:
    """Distances, novelty and population diversity over a :class:`ParameterSchema`."""

    def __init__(self, schema: ParameterSchema | Mapping[str, Any]) -> None:
        self.schema = ParameterSchema.from_dict(schema)
        self._names = tuple(sorted(self.schema.parameters))

    def parameter_distance(self, spec: ParameterSpec, a: Any, b: Any) -> float:
        if a is _MISSING or b is _MISSING:
            return 0.0 if a is b else 1.0
        if spec.type in (ParamType.INT, ParamType.FLOAT):
            try:
                return _numeric_distance(spec, float(a), float(b))
            except (TypeError, ValueError):
                return 1.0
        if spec.type is ParamType.ORDERED_LIST:
            if isinstance(a, list | tuple) and isinstance(b, list | tuple):
                return _jaccard_distance(a, b)
            return 1.0
        return 0.0 if (type(a) is type(b) and a == b) else 1.0

    def distance(self, a: Mapping[str, Any], b: Mapping[str, Any]) -> float:
        """Normalised distance in [0, 1] between two parameter vectors."""
        if not self._names:
            return 0.0
        total = 0.0
        for name in self._names:
            total += self.parameter_distance(self.schema.parameters[name], a.get(name, _MISSING), b.get(name, _MISSING))
        return total / len(self._names)

    def cross_distances(self, rows: Sequence[Mapping[str, Any]], others: Sequence[Mapping[str, Any]]) -> np.ndarray:
        """Matrix ``D[i, j] = distance(rows[i], others[j])`` (vectorised; identical to :meth:`distance`)."""
        total = np.zeros((len(rows), len(others)), dtype=float)
        if not self._names or not len(rows) or not len(others):
            return total
        for name in self._names:
            spec = self.schema.parameters[name]
            col_a = [row.get(name, _MISSING) for row in rows]
            col_b = [row.get(name, _MISSING) for row in others]
            if spec.type in (ParamType.INT, ParamType.FLOAT):
                total += self._numeric_block(spec, col_a, col_b)
            elif spec.type is ParamType.ORDERED_LIST:
                total += np.array([[self.parameter_distance(spec, a, b) for b in col_b] for a in col_a], dtype=float)
            else:
                total += self._categorical_block(col_a, col_b)
        return total / len(self._names)

    @staticmethod
    def _categorical_block(col_a: Sequence[Any], col_b: Sequence[Any]) -> np.ndarray:
        codes: dict[tuple[str, str], int] = {}

        def encode(value: Any) -> int:
            if value is _MISSING:
                return -1
            return codes.setdefault((type(value).__name__, repr(value)), len(codes))

        ca = np.array([encode(v) for v in col_a], dtype=np.int64)
        cb = np.array([encode(v) for v in col_b], dtype=np.int64)
        return (ca[:, None] != cb[None, :]).astype(float)

    @staticmethod
    def _numeric_block(spec: ParameterSpec, col_a: Sequence[Any], col_b: Sequence[Any]) -> np.ndarray:
        def encode(column: Sequence[Any]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
            values = np.zeros(len(column), dtype=float)
            missing = np.zeros(len(column), dtype=bool)
            invalid = np.zeros(len(column), dtype=bool)
            for i, v in enumerate(column):
                if v is _MISSING:
                    missing[i] = True
                    continue
                try:
                    values[i] = float(v)
                except (TypeError, ValueError):
                    invalid[i] = True
            return values, missing, invalid

        va, ma, ia = encode(col_a)
        vb, mb, ib = encode(col_b)
        diff = np.abs(va[:, None] - vb[None, :])
        span = spec.value_range
        if span is not None:
            block = np.zeros_like(diff) if span == 0 else np.minimum(1.0, diff / span)
        else:
            denom = np.maximum(np.maximum(np.abs(va)[:, None], np.abs(vb)[None, :]), 1.0)
            block = np.minimum(1.0, diff / denom)
        any_missing = ma[:, None] | mb[None, :]
        both_missing = ma[:, None] & mb[None, :]
        any_invalid = ia[:, None] | ib[None, :]
        block = np.where(any_missing | any_invalid, 1.0, block)
        return np.where(both_missing, 0.0, block)

    def novelty_all(
        self,
        population: Sequence[Mapping[str, Any]],
        reference: Sequence[Mapping[str, Any]] = (),
        *,
        k: int = 5,
    ) -> list[float]:
        """Novelty of every member of ``population`` against the *other* members plus ``reference``."""
        if k < 1:
            raise ValueError("k must be >= 1")
        n = len(population)
        within = self.cross_distances(population, population)
        external = self.cross_distances(population, reference)
        scores: list[float] = []
        for i in range(n):
            row = np.concatenate([np.delete(within[i], i), external[i]])
            if row.size == 0:
                scores.append(1.0)
                continue
            nearest = np.sort(row)[: min(k, row.size)]
            scores.append(float(nearest.mean()))
        return scores

    def novelty(self, params: Mapping[str, Any], reference: Sequence[Mapping[str, Any]], *, k: int = 5) -> float:
        """Mean distance to the ``k`` nearest vectors of ``reference`` (1.0 when ``reference`` is empty).

        The caller excludes ``params`` itself from ``reference``.
        """
        if k < 1:
            raise ValueError("k must be >= 1")
        if not reference:
            return 1.0
        distances = sorted(self.distance(params, other) for other in reference)
        nearest = distances[: min(k, len(distances))]
        return sum(nearest) / len(nearest)

    def niche_count(
        self,
        params: Mapping[str, Any],
        population: Sequence[Mapping[str, Any]],
        *,
        sigma_share: float = 0.2,
        alpha: float = 1.0,
    ) -> float:
        """Fitness-sharing niche count of ``params`` within ``population`` (include itself → ≥ 1)."""
        if sigma_share <= 0:
            raise ValueError("sigma_share must be > 0")
        count = 0.0
        for other in population:
            d = self.distance(params, other)
            if d < sigma_share:
                count += 1.0 - (d / sigma_share) ** alpha
        return count

    def metrics(self, population: Sequence[Mapping[str, Any]]) -> DiversityMetrics:
        """Mean/min/max pairwise distance and the fraction of unique parameter vectors."""
        n = len(population)
        if n == 0:
            return DiversityMetrics(
                n=0, unique_ratio=0.0, mean_pairwise_distance=0.0, min_pairwise_distance=0.0, max_pairwise_distance=0.0
            )
        unique = len({canonical_hash(dict(p)) for p in population})
        if n == 1:
            return DiversityMetrics(
                n=1, unique_ratio=1.0, mean_pairwise_distance=0.0, min_pairwise_distance=0.0, max_pairwise_distance=0.0
            )
        matrix = self.cross_distances(population, population)
        upper = matrix[np.triu_indices(n, k=1)]
        total, lo, hi, pairs = float(upper.sum()), float(upper.min()), float(upper.max()), int(upper.size)
        return DiversityMetrics(
            n=n,
            unique_ratio=unique / n,
            mean_pairwise_distance=round(total / pairs, 12),
            min_pairwise_distance=round(lo, 12),
            max_pairwise_distance=round(hi, 12),
        )
