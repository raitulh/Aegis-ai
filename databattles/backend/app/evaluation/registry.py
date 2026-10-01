"""Evaluator registry — the extension point for new task types.

A new evaluator (e.g. image segmentation IoU, code execution) registers an
`EvaluatorSpec` with its version string and supported metrics, and provides a
sandbox runner implementation with the same validate/score phases. See
docs/EVALUATOR_AUTHORING.md.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from app.core.errors import ValidationFailed
from app.evaluation.core import EVALUATOR_VERSION
from app.evaluation.metrics import METRICS


@dataclass(frozen=True)
class EvaluatorSpec:
    key: str
    version: str
    label: str
    metrics: tuple[str, ...]
    submission_format: str


EVALUATORS: dict[str, EvaluatorSpec] = {
    "csv_prediction": EvaluatorSpec(
        key="csv_prediction",
        version=EVALUATOR_VERSION,
        label="CSV predictions",
        metrics=tuple(METRICS.keys()),
        submission_format="CSV with an id column and a prediction column, one row per test id.",
    ),
}


def normalize_evaluation_config(raw: dict[str, Any]) -> dict[str, Any]:
    evaluator = raw.get("evaluator", "csv_prediction")
    spec = EVALUATORS.get(evaluator)
    if spec is None:
        raise ValidationFailed(details={"fields": {"evaluation.evaluator": "Unknown evaluator."}})
    metric = raw.get("metric", "accuracy")
    if metric not in spec.metrics:
        raise ValidationFailed(details={"fields": {"evaluation.metric": f"Unsupported metric. Choose one of: {', '.join(spec.metrics)}."}})
    secondary = [m for m in raw.get("secondary_metrics", []) or [] if m in spec.metrics and m != metric][:3]
    id_col = str(raw.get("id_column", "id")).strip() or "id"
    target_col = str(raw.get("target_column", "target")).strip() or "target"
    if id_col == target_col:
        raise ValidationFailed(details={"fields": {"evaluation.target_column": "Id and target columns must differ."}})
    config: dict[str, Any] = {
        "evaluator": evaluator,
        "metric": metric,
        "secondary_metrics": secondary,
        "id_column": id_col[:64],
        "target_column": target_col[:64],
        "strict_schema": bool(raw.get("strict_schema", True)),
        "positive_label": str(raw.get("positive_label", "1"))[:64],
    }
    if raw.get("expected_row_count"):
        config["expected_row_count"] = int(raw["expected_row_count"])
    return config


def metric_direction(config: dict[str, Any]) -> str:
    return METRICS[config.get("metric", "accuracy")].direction


def config_hash(config: dict[str, Any], ground_truth_sha256: str) -> str:
    spec = EVALUATORS[config.get("evaluator", "csv_prediction")]
    canonical = json.dumps({"v": spec.version, "config": config, "gt": ground_truth_sha256}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
