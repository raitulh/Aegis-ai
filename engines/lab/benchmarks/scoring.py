"""Scoring primitives shared by the benchmark suites (pure, deterministic)."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any


def dedupe(items: Iterable[Any]) -> list[Any]:
    """Order-preserving de-duplication (first occurrence wins)."""
    seen: set[str] = set()
    out: list[Any] = []
    for item in items:
        key = repr(item)
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


# ---------------------------------------------------------------------------------------------
# Ranking metrics
# ---------------------------------------------------------------------------------------------
def dcg_at_k(ranked: Sequence[str], gains: Mapping[str, float], k: int) -> float:
    """Discounted cumulative gain with exponential gain ``2^g − 1`` and ``log2(rank + 1)`` discount."""
    return sum((2.0 ** gains.get(doc, 0.0) - 1.0) / math.log2(i + 2) for i, doc in enumerate(ranked[:k]))


def ndcg_at_k(ranked: Sequence[str], gains: Mapping[str, float], k: int) -> float:
    """Normalised DCG@k in [0, 1]; 1.0 when nothing is relevant and nothing is required."""
    ideal = sorted((g for g in gains.values() if g > 0), reverse=True)
    idcg = sum((2.0**g - 1.0) / math.log2(i + 2) for i, g in enumerate(ideal[:k]))
    if idcg == 0:
        return 1.0
    return dcg_at_k(dedupe(ranked), gains, k) / idcg


def recall_at_k(ranked: Sequence[str], relevant: Iterable[str], k: int) -> float:
    relevant_set = set(relevant)
    if not relevant_set:
        return 1.0
    return len(relevant_set & set(dedupe(ranked)[:k])) / len(relevant_set)


def reciprocal_rank(ranked: Sequence[str], relevant: Iterable[str]) -> float:
    relevant_set = set(relevant)
    for i, doc in enumerate(dedupe(ranked)):
        if doc in relevant_set:
            return 1.0 / (i + 1)
    return 0.0


# ---------------------------------------------------------------------------------------------
# Set / label metrics
# ---------------------------------------------------------------------------------------------
def set_counts(predicted: Iterable[str], expected: Iterable[str]) -> tuple[int, int, int]:
    """``(tp, fp, fn)`` of two label sets."""
    p, e = set(predicted), set(expected)
    return len(p & e), len(p - e), len(e - p)


def prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    """Precision, recall, F1; an empty prediction of an empty truth scores 1.0 everywhere."""
    if tp == fp == fn == 0:
        return 1.0, 1.0, 1.0
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def macro_f1(expected: Sequence[str], predicted: Sequence[str | None]) -> float:
    """Macro-averaged F1 over every label appearing in ``expected`` or ``predicted``."""
    if len(expected) != len(predicted):
        raise ValueError("expected and predicted must have the same length")
    labels = sorted({*expected, *(p for p in predicted if p is not None)})
    if not labels:
        return 1.0
    scores = []
    for label in labels:
        tp = sum(1 for e, p in zip(expected, predicted, strict=True) if e == label and p == label)
        fp = sum(1 for e, p in zip(expected, predicted, strict=True) if e != label and p == label)
        fn = sum(1 for e, p in zip(expected, predicted, strict=True) if e == label and p != label)
        scores.append(prf(tp, fp, fn)[2])
    return sum(scores) / len(scores)


def extract_label(output: Any, keys: Sequence[str]) -> str | None:
    """A normalised (stripped, upper-case) label from a string or a dict holding one of ``keys``."""
    value: Any = output
    if isinstance(output, Mapping):
        value = next((output[k] for k in keys if k in output), None)
    if isinstance(value, str) and value.strip():
        return value.strip().upper()
    return None


def extract_string_list(output: Any, keys: Sequence[str]) -> list[str] | None:
    """A list of strings from a list or a dict holding one of ``keys``; ``None`` if malformed."""
    value: Any = output
    if isinstance(output, Mapping):
        value = next((output[k] for k in keys if k in output), None)
    if not isinstance(value, list | tuple) or not all(isinstance(v, str) for v in value):
        return None
    return [str(v) for v in value]


# ---------------------------------------------------------------------------------------------
# Value matching
# ---------------------------------------------------------------------------------------------
def values_match(expected: Any, actual: Any, *, rel_tol: float = 1e-6, abs_tol: float = 1e-9) -> bool:
    """Recursive equality with numeric tolerance (bools and strings compare exactly)."""
    if isinstance(expected, bool) or isinstance(actual, bool):
        return type(expected) is type(actual) and expected == actual
    if isinstance(expected, int | float) and isinstance(actual, int | float):
        if not (math.isfinite(float(expected)) and math.isfinite(float(actual))):
            return False
        return math.isclose(float(expected), float(actual), rel_tol=rel_tol, abs_tol=abs_tol)
    if isinstance(expected, str) or isinstance(actual, str):
        return isinstance(expected, str) and isinstance(actual, str) and expected == actual
    if isinstance(expected, list | tuple):
        return (
            isinstance(actual, list | tuple)
            and len(expected) == len(actual)
            and all(values_match(e, a, rel_tol=rel_tol, abs_tol=abs_tol) for e, a in zip(expected, actual, strict=True))
        )
    if isinstance(expected, Mapping):
        return (
            isinstance(actual, Mapping)
            and set(expected) == set(actual)
            and all(values_match(expected[k], actual[k], rel_tol=rel_tol, abs_tol=abs_tol) for k in expected)
        )
    return bool(expected == actual)


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0
