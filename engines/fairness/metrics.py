"""Mode A — group fairness metrics for traditional ML decisions (transparent Fairlearn-equivalent maths).

All metrics are computed from (y_true, y_pred, sensitive_feature) with explicit per-group rates so every
number can be traced. Differences are max − min across groups; ratios are min / max.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, Field

from engines.common.stats import wilson_interval


class GroupRates(BaseModel):
    group: str
    n: int
    selection_rate: float
    tpr: float | None
    fpr: float | None
    error_rate: float | None
    positives: int
    selection_ci: tuple[float, float]


class FairnessMetricResult(BaseModel):
    metric: str
    value: float
    threshold: float | None = None
    passed: bool | None = None
    description: str


class GroupFairnessReport(BaseModel):
    sensitive_feature: str
    sample_size: int
    groups: list[GroupRates]
    metrics: list[FairnessMetricResult]
    methodology: str = Field(
        default=(
            "Per-group selection rate, true-positive rate, false-positive rate and error rate computed from "
            "labelled decisions. Differences are max−min across groups; ratios are min/max. Wilson 95% intervals "
            "are reported for selection rates."
        )
    )
    limitations: str = Field(
        default=(
            "Group metrics describe the evaluated dataset only. They depend on label quality and group "
            "definitions and are not a complete sociotechnical fairness determination."
        )
    )


def _rate(num: int, den: int) -> float | None:
    return num / den if den else None


def group_rates(y_true: Sequence[int] | None, y_pred: Sequence[int], sensitive: Sequence[str]) -> list[GroupRates]:
    groups: dict[str, list[int]] = {}
    for i, g in enumerate(sensitive):
        groups.setdefault(str(g), []).append(i)
    out: list[GroupRates] = []
    for g, idx in sorted(groups.items()):
        preds = [int(y_pred[i]) for i in idx]
        positives = sum(preds)
        tpr = fpr = err = None
        if y_true is not None:
            truth = [int(y_true[i]) for i in idx]
            tp = sum(1 for t, p in zip(truth, preds, strict=True) if t == 1 and p == 1)
            fp = sum(1 for t, p in zip(truth, preds, strict=True) if t == 0 and p == 1)
            pos = sum(truth)
            neg = len(truth) - pos
            tpr = _rate(tp, pos)
            fpr = _rate(fp, neg)
            err = _rate(sum(1 for t, p in zip(truth, preds, strict=True) if t != p), len(truth))
        out.append(
            GroupRates(
                group=g,
                n=len(idx),
                selection_rate=positives / len(idx),
                tpr=tpr,
                fpr=fpr,
                error_rate=err,
                positives=positives,
                selection_ci=wilson_interval(positives, len(idx)),
            )
        )
    return out


def _spread(values: list[float | None]) -> float:
    present = [v for v in values if v is not None]
    return max(present) - min(present) if len(present) >= 2 else 0.0


def compute_group_fairness(
    y_pred: Sequence[int],
    sensitive: Sequence[str],
    *,
    y_true: Sequence[int] | None = None,
    sensitive_feature: str = "group",
    thresholds: dict[str, float] | None = None,
) -> GroupFairnessReport:
    if len(y_pred) != len(sensitive) or (y_true is not None and len(y_true) != len(y_pred)):
        raise ValueError("y_pred, y_true and sensitive must have equal length")
    thresholds = thresholds or {"demographic_parity_difference": 0.1, "equalized_odds_difference": 0.1}
    rates = group_rates(y_true, y_pred, sensitive)
    sel = [r.selection_rate for r in rates]
    metrics: list[FairnessMetricResult] = []

    def add(metric: str, value: float, description: str, higher_is_worse: bool = True) -> None:
        threshold = thresholds.get(metric)
        passed = None
        if threshold is not None:
            passed = value <= threshold if higher_is_worse else value >= threshold
        metrics.append(
            FairnessMetricResult(
                metric=metric, value=round(value, 4), threshold=threshold, passed=passed, description=description
            )
        )

    add("demographic_parity_difference", _spread(list(sel)), "Largest gap in selection rate between groups")
    ratio = (min(sel) / max(sel)) if sel and max(sel) > 0 else 1.0
    thresholds.setdefault("demographic_parity_ratio", 0.8)
    add(
        "demographic_parity_ratio",
        ratio,
        "Smallest / largest selection rate (four-fifths heuristic: ≥ 0.8)",
        higher_is_worse=False,
    )
    for r in rates:
        metrics.append(
            FairnessMetricResult(
                metric=f"selection_rate[{r.group}]",
                value=round(r.selection_rate, 4),
                description=f"Selection rate for {r.group} (n={r.n})",
            )
        )
    if y_true is not None:
        tpr_gap = _spread([r.tpr for r in rates])
        fpr_gap = _spread([r.fpr for r in rates])
        add("true_positive_rate_gap", tpr_gap, "Largest gap in TPR between groups")
        add("false_positive_rate_gap", fpr_gap, "Largest gap in FPR between groups")
        add("equal_opportunity_difference", tpr_gap, "Equal opportunity: TPR difference")
        add("equalized_odds_difference", max(tpr_gap, fpr_gap), "Equalized odds: max(TPR gap, FPR gap)")
        add("error_rate_gap", _spread([r.error_rate for r in rates]), "Largest gap in error rate between groups")
    return GroupFairnessReport(
        sensitive_feature=sensitive_feature, sample_size=len(y_pred), groups=rates, metrics=metrics
    )


def report_from_records(
    records: list[dict[str, Any]],
    *,
    sensitive_feature: str,
    prediction_field: str = "prediction",
    label_field: str | None = "label",
    thresholds: dict[str, float] | None = None,
) -> GroupFairnessReport:
    y_pred = [int(r[prediction_field]) for r in records]
    y_true = [int(r[label_field]) for r in records] if label_field and all(label_field in r for r in records) else None
    sensitive = [str(r[sensitive_feature]) for r in records]
    return compute_group_fairness(
        y_pred, sensitive, y_true=y_true, sensitive_feature=sensitive_feature, thresholds=thresholds
    )
