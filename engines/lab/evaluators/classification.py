"""ClassificationEvaluator — independent recomputation of classification metrics from raw predictions.

Predictions come from the experiment (``predictions.csv``); the held-out labels (``labels.csv``) are supplied by
the service from an ``evaluator_only`` dataset split the experiment could not read. Labels are compared as
strings. The label set is the sorted union of true and predicted labels (numeric labels sorted numerically).

Per class ``c``: ``precision = TP/(TP+FP)``, ``recall = TP/(TP+FN)``, ``F1 = 2PR/(P+R)``; an undefined ratio
(zero denominator) is reported as 0 with a warning (the common ``zero_division=0`` convention). Macro averages
are unweighted means over the label set; micro averages pool counts (for single-label data micro P = R = F1 =
accuracy); ``weighted`` averages weight by support; ``balanced_accuracy`` is the mean recall over classes present
in the labels. With ``positive_label`` binary precision/recall/F1 for that class are added.

Misaligned inputs (mismatched ids or lengths, checksum mismatch) → warnings and ``passed=None``.
``confidence = match_rate × min(1, n / 30)``.
"""

from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from engines.lab.evaluators.base import (
    ArtifactBundle,
    BaseEvaluator,
    EvalContext,
    EvaluationResult,
    EvaluatorConfig,
    ExperimentView,
    align_rows,
    criteria_for,
    read_csv,
)
from engines.lab.experiment_spec import ABSOLUTE_COMPARATORS, check_criterion

_THRESHOLD_METRICS = (
    "accuracy",
    "balanced_accuracy",
    "precision_macro",
    "recall_macro",
    "f1_macro",
    "precision_micro",
    "recall_micro",
    "f1_micro",
    "f1_weighted",
)


class ClassificationThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    accuracy_min: float | None = Field(default=None, ge=0, le=1)
    balanced_accuracy_min: float | None = Field(default=None, ge=0, le=1)
    precision_macro_min: float | None = Field(default=None, ge=0, le=1)
    recall_macro_min: float | None = Field(default=None, ge=0, le=1)
    f1_macro_min: float | None = Field(default=None, ge=0, le=1)
    f1_micro_min: float | None = Field(default=None, ge=0, le=1)
    f1_weighted_min: float | None = Field(default=None, ge=0, le=1)


class ClassificationConfig(EvaluatorConfig):
    predictions_file: str = "predictions.csv"
    labels_file: str = "labels.csv"
    id_column: str | None = "id"
    prediction_column: str = "prediction"
    label_column: str = "label"
    positive_label: str | None = None
    thresholds: ClassificationThresholds = Field(default_factory=ClassificationThresholds)
    min_rows: int = Field(default=1, ge=1)


def _label_key(label: str) -> tuple[int, float, str]:
    try:
        number = float(label)
        if math.isfinite(number):
            return (0, number, label)
    except ValueError:
        pass
    return (1, 0.0, label)


def _ratio(num: float, den: float) -> tuple[float, bool]:
    return (num / den, True) if den > 0 else (0.0, False)


def classification_metrics(y_true: list[str], y_pred: list[str]) -> tuple[dict[str, Any], list[str]]:
    """Confusion matrix and derived metrics for aligned label lists → ``(metrics, warnings)``."""
    labels = sorted(set(y_true) | set(y_pred), key=_label_key)
    index = {label: i for i, label in enumerate(labels)}
    k = len(labels)
    matrix = [[0] * k for _ in range(k)]  # rows = true label, columns = predicted label
    for t, p in zip(y_true, y_pred, strict=True):
        matrix[index[t]][index[p]] += 1
    n = len(y_true)
    warnings: list[str] = []
    per_class: dict[str, dict[str, float]] = {}
    precisions, recalls, f1s, supports = [], [], [], []
    tp_total = 0
    for i, label in enumerate(labels):
        tp = matrix[i][i]
        support = sum(matrix[i])
        predicted = sum(matrix[r][i] for r in range(k))
        tp_total += tp
        precision, p_defined = _ratio(tp, predicted)
        recall, r_defined = _ratio(tp, support)
        f1 = 2 * precision * recall / (precision + recall) if precision + recall > 0 else 0.0
        if not p_defined:
            warnings.append(f"precision undefined for class {label!r} (never predicted); set to 0")
        if not r_defined:
            warnings.append(f"recall undefined for class {label!r} (absent from labels); set to 0")
        per_class[label] = {"precision": precision, "recall": recall, "f1": f1, "support": float(support)}
        precisions.append(precision)
        recalls.append(recall)
        f1s.append(f1)
        supports.append(support)
    accuracy = tp_total / n if n else 0.0
    present = [recalls[i] for i in range(k) if supports[i] > 0]
    metrics: dict[str, Any] = {
        "n": float(n),
        "accuracy": accuracy,
        "balanced_accuracy": sum(present) / len(present) if present else 0.0,
        "precision_macro": sum(precisions) / k,
        "recall_macro": sum(recalls) / k,
        "f1_macro": sum(f1s) / k,
        # Single-label multiclass: pooled FP == pooled FN == n - TP, so micro P = R = F1 = accuracy.
        "precision_micro": accuracy,
        "recall_micro": accuracy,
        "f1_micro": accuracy,
        "f1_weighted": sum(f * s for f, s in zip(f1s, supports, strict=True)) / n if n else 0.0,
        "confusion_matrix": {"labels": labels, "matrix": matrix},
        "per_class": per_class,
    }
    return metrics, warnings


class ClassificationEvaluator(BaseEvaluator):
    key = "classification"
    version = "1.0.0"
    kind = "classification"
    name = "Classification metrics (independent recomputation)"
    description = (
        "Recomputes accuracy, precision/recall/F1 (macro, micro, weighted), balanced accuracy and the confusion "
        "matrix from predictions and held-out labels."
    )
    config_schema = ClassificationConfig
    independent = True

    def _evaluate(
        self, experiment: ExperimentView, artifacts: ArtifactBundle, context: EvalContext, config: Any
    ) -> EvaluationResult:
        cfg: ClassificationConfig = config
        missing = [f for f in (cfg.predictions_file, cfg.labels_file) if f not in artifacts.files]
        if missing:
            return self._result(passed=None, confidence=0.0, warnings=[f"missing file(s): {', '.join(missing)}"])
        evidence: list[dict[str, Any]] = [
            {"check": "input", **artifacts.describe(cfg.predictions_file), "role": "predictions"},
            {"check": "input", **artifacts.describe(cfg.labels_file), "role": "labels"},
        ]
        warnings: list[str] = []
        visibility = artifacts.metadata.get(cfg.labels_file, {}).get("visibility")
        if visibility is not None and visibility != "evaluator_only":
            warnings.append("labels do not come from an evaluator_only split; the experiment may have seen them")
        integrity = artifacts.integrity_issues([cfg.predictions_file, cfg.labels_file])
        if integrity:
            return self._result(passed=None, confidence=0.0, warnings=warnings + integrity, evidence=evidence)
        try:
            p_header, p_rows = read_csv(artifacts.files[cfg.predictions_file])
            l_header, l_rows = read_csv(artifacts.files[cfg.labels_file])
        except ValueError as exc:
            return self._result(passed=None, confidence=0.0, warnings=[f"unreadable CSV: {exc}"], evidence=evidence)
        for column, header, file in (
            (cfg.prediction_column, p_header, cfg.predictions_file),
            (cfg.label_column, l_header, cfg.labels_file),
        ):
            if column not in header:
                return self._result(
                    passed=None, confidence=0.0, warnings=[f"column {column!r} not found in {file}"], evidence=evidence
                )
        pairs, align_warnings, aligned_ok = align_rows(p_rows, l_rows, cfg.id_column, p_header, l_header)
        warnings.extend(align_warnings)
        y_pred = [p[cfg.prediction_column] for p, _ in pairs]
        y_true = [t[cfg.label_column] for _, t in pairs]
        if len(y_true) < cfg.min_rows:
            warnings.append(f"only {len(y_true)} aligned row(s); {cfg.min_rows} required")
            return self._result(passed=None, confidence=0.0, warnings=warnings, evidence=evidence)
        metrics, metric_warnings = classification_metrics(y_true, y_pred)
        warnings.extend(metric_warnings)
        if cfg.positive_label is not None:
            stats = metrics["per_class"].get(cfg.positive_label)
            if stats is None:
                warnings.append(f"positive label {cfg.positive_label!r} does not occur")
            else:
                metrics["precision_binary"] = stats["precision"]
                metrics["recall_binary"] = stats["recall"]
                metrics["f1_binary"] = stats["f1"]
        checks = self._checks(cfg, metrics, experiment, context)
        match_rate = len(y_true) / max(len(l_rows), len(p_rows), 1)
        evidence.append({"check": "alignment", "pairs": len(y_true), "reference_rows": len(l_rows), "ok": aligned_ok})
        evidence.extend(checks)
        if not aligned_ok:
            passed: bool | None = None
        elif not checks:
            passed = None
            warnings.append("no thresholds configured: metrics computed but no pass/fail decision")
        elif any(c["satisfied"] is False for c in checks):
            passed = False
        elif any(c["satisfied"] is None for c in checks):
            passed = None
        else:
            passed = True
        return self._result(
            passed=passed,
            confidence=match_rate * min(1.0, len(y_true) / 30.0),
            metrics=metrics,
            warnings=warnings,
            evidence=evidence,
            details={"match_rate": match_rate, "aligned": aligned_ok, "labels": metrics["confusion_matrix"]["labels"]},
        )

    @staticmethod
    def _checks(
        cfg: ClassificationConfig, metrics: dict[str, Any], experiment: ExperimentView, context: EvalContext
    ) -> list[dict[str, Any]]:
        checks: list[dict[str, Any]] = []
        for field_name, bound in cfg.thresholds.model_dump().items():
            if bound is None:
                continue
            metric = field_name.removesuffix("_min")
            value = metrics.get(metric)
            checks.append(
                {
                    "check": "threshold",
                    "metric": metric,
                    "op": "gte",
                    "bound": bound,
                    "value": value,
                    "satisfied": None if value is None else value >= bound,
                }
            )
        for criterion in criteria_for(experiment, context):
            if (
                criterion.metric in _THRESHOLD_METRICS
                and criterion.comparator in ABSOLUTE_COMPARATORS
                and criterion.relative_to == "absolute"
            ):
                result = check_criterion(criterion, metrics.get(criterion.metric), None, "maximize")
                checks.append({"check": "success_criterion", **result.model_dump()})
        return checks
