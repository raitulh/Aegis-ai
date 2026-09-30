"""RegressionEvaluator — independent recomputation of regression metrics from raw predictions.

Reads ``predictions.csv`` (written by the experiment) and ``targets.csv`` (held-out targets supplied by the
service, typically from an ``evaluator_only`` dataset split the experiment never saw), aligns them by id (or by
position when no id column exists) and recomputes:

* ``mae`` = mean |ŷ - y|, ``rmse`` = sqrt(mean (ŷ - y)²), ``bias`` = mean (ŷ - y),
  ``max_abs_error``, ``median_abs_error``;
* ``r2`` = 1 - SS_res / SS_tot (omitted with a warning when the targets are constant);
* ``mape`` = 100 · mean |ŷ - y| / |y| over rows with |y| > ``mape_epsilon`` (rows with ~zero targets excluded
  and counted).

Thresholds come from the config (``mae_max``, ``rmse_max``, ``r2_min``, ``mape_max``) plus any absolute success
criteria on metrics with these names. Misaligned inputs (id mismatch, length mismatch, unparsable values,
checksum mismatch) produce warnings and ``passed=None``. ``confidence = match_rate × min(1, n / 30)``.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
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

_METRIC_DIRECTIONS = {"mae": "minimize", "rmse": "minimize", "mape": "minimize", "r2": "maximize"}


class RegressionThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    mae_max: float | None = Field(default=None, ge=0)
    rmse_max: float | None = Field(default=None, ge=0)
    r2_min: float | None = None
    mape_max: float | None = Field(default=None, ge=0)


class RegressionConfig(EvaluatorConfig):
    predictions_file: str = "predictions.csv"
    targets_file: str = "targets.csv"
    id_column: str | None = "id"
    prediction_column: str = "prediction"
    target_column: str = "target"
    thresholds: RegressionThresholds = Field(default_factory=RegressionThresholds)
    mape_epsilon: float = Field(default=1e-12, ge=0)
    min_rows: int = Field(default=1, ge=1)


def regression_metrics(y_true: np.ndarray, y_pred: np.ndarray, mape_epsilon: float = 1e-12) -> dict[str, float | None]:
    """Regression metrics for aligned arrays (``r2``/``mape`` are ``None`` when undefined)."""
    err = y_pred - y_true
    abs_err = np.abs(err)
    ss_res = float(np.sum(err**2))
    ss_tot = float(np.sum((y_true - y_true.mean()) ** 2))
    nonzero = np.abs(y_true) > mape_epsilon
    return {
        "n": float(y_true.size),
        "mae": float(abs_err.mean()),
        "rmse": math.sqrt(ss_res / y_true.size),
        "bias": float(err.mean()),
        "max_abs_error": float(abs_err.max()),
        "median_abs_error": float(np.median(abs_err)),
        "r2": 1.0 - ss_res / ss_tot if ss_tot > 0 else None,
        "mape": float(100.0 * np.mean(abs_err[nonzero] / np.abs(y_true[nonzero]))) if nonzero.any() else None,
        "mape_excluded_rows": float(np.sum(~nonzero)),
    }


class RegressionEvaluator(BaseEvaluator):
    key = "regression"
    version = "1.0.0"
    kind = "regression"
    name = "Regression metrics (independent recomputation)"
    description = "Recomputes MAE, RMSE, R² and MAPE from predictions and held-out targets."
    config_schema = RegressionConfig
    independent = True

    def _evaluate(
        self, experiment: ExperimentView, artifacts: ArtifactBundle, context: EvalContext, config: Any
    ) -> EvaluationResult:
        cfg: RegressionConfig = config
        warnings: list[str] = []
        missing = [f for f in (cfg.predictions_file, cfg.targets_file) if f not in artifacts.files]
        if missing:
            return self._result(passed=None, confidence=0.0, warnings=[f"missing file(s): {', '.join(missing)}"])
        evidence = [
            {"check": "input", **artifacts.describe(cfg.predictions_file), "role": "predictions"},
            {"check": "input", **artifacts.describe(cfg.targets_file), "role": "targets"},
        ]
        integrity = artifacts.integrity_issues([cfg.predictions_file, cfg.targets_file])
        if integrity:
            return self._result(passed=None, confidence=0.0, warnings=integrity, evidence=evidence)
        try:
            p_header, p_rows = read_csv(artifacts.files[cfg.predictions_file])
            t_header, t_rows = read_csv(artifacts.files[cfg.targets_file])
        except ValueError as exc:
            return self._result(passed=None, confidence=0.0, warnings=[f"unreadable CSV: {exc}"], evidence=evidence)
        for column, header, file in (
            (cfg.prediction_column, p_header, cfg.predictions_file),
            (cfg.target_column, t_header, cfg.targets_file),
        ):
            if column not in header:
                return self._result(
                    passed=None,
                    confidence=0.0,
                    warnings=[f"column {column!r} not found in {file}"],
                    evidence=evidence,
                )
        pairs, align_warnings, aligned_ok = align_rows(p_rows, t_rows, cfg.id_column, p_header, t_header)
        warnings.extend(align_warnings)
        y_pred: list[float] = []
        y_true: list[float] = []
        bad = 0
        for pred_row, target_row in pairs:
            try:
                p = float(pred_row[cfg.prediction_column])
                t = float(target_row[cfg.target_column])
            except ValueError:
                bad += 1
                continue
            if math.isfinite(p) and math.isfinite(t):
                y_pred.append(p)
                y_true.append(t)
            else:
                bad += 1
        if bad:
            warnings.append(f"{bad} row(s) with non-numeric or non-finite values excluded")
            aligned_ok = False
        n_reference = len(t_rows)
        if len(y_true) < cfg.min_rows:
            warnings.append(f"only {len(y_true)} usable row(s); {cfg.min_rows} required")
            return self._result(passed=None, confidence=0.0, warnings=warnings, evidence=evidence)
        computed = regression_metrics(np.asarray(y_true), np.asarray(y_pred), cfg.mape_epsilon)
        if computed["r2"] is None:
            warnings.append("R² undefined: targets are constant")
        if computed["mape"] is None:
            warnings.append("MAPE undefined: all targets are ~0")
        elif computed["mape_excluded_rows"]:
            warnings.append(f"{int(computed['mape_excluded_rows'] or 0)} row(s) with ~zero target excluded from MAPE")
        checks = self._checks(cfg, computed, experiment, context)
        match_rate = len(y_true) / max(n_reference, len(p_rows), 1)
        evidence.append({"check": "alignment", "pairs": len(y_true), "reference_rows": n_reference, "ok": aligned_ok})
        evidence.extend(checks)
        if not aligned_ok:
            passed: bool | None = None
        elif not checks:
            passed = None
            warnings.append("no thresholds configured: metrics computed but no pass/fail decision")
        elif any(c["satisfied"] is None for c in checks):
            passed = None if all(c["satisfied"] is not False for c in checks) else False
        else:
            passed = all(c["satisfied"] for c in checks)
        return self._result(
            passed=passed,
            confidence=match_rate * min(1.0, len(y_true) / 30.0),
            metrics={k: v for k, v in computed.items() if v is not None},
            warnings=warnings,
            evidence=evidence,
            details={"match_rate": match_rate, "aligned": aligned_ok},
        )

    @staticmethod
    def _checks(
        cfg: RegressionConfig, computed: dict[str, float | None], experiment: ExperimentView, context: EvalContext
    ) -> list[dict[str, Any]]:
        checks: list[dict[str, Any]] = []
        limits = cfg.thresholds
        for metric, bound, op in (
            ("mae", limits.mae_max, "lte"),
            ("rmse", limits.rmse_max, "lte"),
            ("mape", limits.mape_max, "lte"),
            ("r2", limits.r2_min, "gte"),
        ):
            if bound is None:
                continue
            value = computed.get(metric)
            ok = None if value is None else (value <= bound if op == "lte" else value >= bound)
            checks.append(
                {"check": "threshold", "metric": metric, "op": op, "bound": bound, "value": value, "satisfied": ok}
            )
        for criterion in criteria_for(experiment, context):
            if criterion.metric in _METRIC_DIRECTIONS and criterion.comparator in ABSOLUTE_COMPARATORS:
                if criterion.relative_to != "absolute":
                    continue
                result = check_criterion(
                    criterion, computed.get(criterion.metric), None, _METRIC_DIRECTIONS[criterion.metric]
                )
                checks.append({"check": "success_criterion", **result.model_dump()})
        return checks
