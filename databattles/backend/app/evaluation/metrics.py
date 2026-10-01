"""Deterministic metric implementations (standard library only).

These run inside the evaluator sandbox, so they must not import application
code, databases or network clients. Every function receives values already
aligned by id in a stable (sorted) order and returns a float.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass


class MetricUndefined(ValueError):
    """Raised when a metric is mathematically undefined for the given data (e.g. AUC with one class)."""


def accuracy(y_true: Sequence[str], y_pred: Sequence[str]) -> float:
    if not y_true:
        raise MetricUndefined("no rows")
    return sum(1 for t, p in zip(y_true, y_pred, strict=True) if t == p) / len(y_true)


def macro_f1(y_true: Sequence[str], y_pred: Sequence[str]) -> float:
    if not y_true:
        raise MetricUndefined("no rows")
    labels = sorted(set(y_true) | set(y_pred))
    scores: list[float] = []
    for label in labels:
        tp = sum(1 for t, p in zip(y_true, y_pred, strict=True) if t == label and p == label)
        fp = sum(1 for t, p in zip(y_true, y_pred, strict=True) if t != label and p == label)
        fn = sum(1 for t, p in zip(y_true, y_pred, strict=True) if t == label and p != label)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        scores.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
    return math.fsum(scores) / len(scores)


def rmse(y_true: Sequence[float], y_pred: Sequence[float]) -> float:
    if not y_true:
        raise MetricUndefined("no rows")
    return math.sqrt(math.fsum((t - p) ** 2 for t, p in zip(y_true, y_pred, strict=True)) / len(y_true))


def mae(y_true: Sequence[float], y_pred: Sequence[float]) -> float:
    if not y_true:
        raise MetricUndefined("no rows")
    return math.fsum(abs(t - p) for t, p in zip(y_true, y_pred, strict=True)) / len(y_true)


def r2(y_true: Sequence[float], y_pred: Sequence[float]) -> float:
    if not y_true:
        raise MetricUndefined("no rows")
    mean = math.fsum(y_true) / len(y_true)
    ss_tot = math.fsum((t - mean) ** 2 for t in y_true)
    ss_res = math.fsum((t - p) ** 2 for t, p in zip(y_true, y_pred, strict=True))
    if ss_tot == 0:
        return 1.0 if ss_res == 0 else 0.0
    return 1 - ss_res / ss_tot


def log_loss(y_true: Sequence[int], y_prob: Sequence[float]) -> float:
    if not y_true:
        raise MetricUndefined("no rows")
    eps = 1e-15
    total = math.fsum(
        -(t * math.log(min(max(p, eps), 1 - eps)) + (1 - t) * math.log(1 - min(max(p, eps), 1 - eps)))
        for t, p in zip(y_true, y_prob, strict=True)
    )
    return total / len(y_true)


def roc_auc(y_true: Sequence[int], y_score: Sequence[float]) -> float:
    n_pos = sum(1 for t in y_true if t == 1)
    n_neg = len(y_true) - n_pos
    if n_pos == 0 or n_neg == 0:
        raise MetricUndefined("ROC AUC needs both positive and negative examples")
    # Average ranks for ties (Mann–Whitney U formulation).
    order = sorted(range(len(y_score)), key=lambda i: y_score[i])
    ranks = [0.0] * len(y_score)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and y_score[order[j + 1]] == y_score[order[i]]:
            j += 1
        avg_rank = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg_rank
        i = j + 1
    sum_pos = math.fsum(r for r, t in zip(ranks, y_true, strict=True) if t == 1)
    return (sum_pos - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    direction: str  # "maximize" | "minimize"
    kind: str       # "label" | "numeric" | "probability"
    fn: Callable[..., float]
    description: str


METRICS: dict[str, Metric] = {
    m.key: m
    for m in [
        Metric("accuracy", "Accuracy", "maximize", "label", accuracy, "Share of rows whose predicted label matches exactly."),
        Metric("macro_f1", "Macro F1", "maximize", "label", macro_f1, "Unweighted mean of per-class F1 scores."),
        Metric("rmse", "RMSE", "minimize", "numeric", rmse, "Root mean squared error."),
        Metric("mae", "MAE", "minimize", "numeric", mae, "Mean absolute error."),
        Metric("r2", "R²", "maximize", "numeric", r2, "Coefficient of determination."),
        Metric("log_loss", "Log loss", "minimize", "probability", log_loss, "Binary cross-entropy of predicted probabilities."),
        Metric("roc_auc", "ROC AUC", "maximize", "probability", roc_auc, "Area under the ROC curve (binary)."),
    ]
}


def is_better(a: float, b: float, direction: str) -> bool:
    return a > b if direction == "maximize" else a < b
