"""Regression harness: hidden numeric targets vs candidate predictions."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from _common import finite, read_csv_map, run

KEY, VERSION = "regression", "1.0.0"


def evaluate(candidate: Path, data: Path, config: dict[str, Any]) -> dict[str, Any]:
    targets = {
        k: float(v) for k, v in read_csv_map(data / config.get("labels_file", "targets.csv"), "id", "target").items()
    }
    preds_raw = read_csv_map(candidate / config.get("predictions_file", "predictions.csv"), "id", "prediction")
    ids = sorted(targets)
    if not ids:
        raise ValueError("no targets")
    missing = [i for i in ids if i not in preds_raw]
    if missing:
        raise ValueError(f"{len(missing)} ids have no prediction")
    preds = {i: finite(float(preds_raw[i])) for i in ids}
    errors = [preds[i] - targets[i] for i in ids]
    mse = sum(e * e for e in errors) / len(ids)
    mean_t = sum(targets.values()) / len(ids)
    ss_tot = sum((targets[i] - mean_t) ** 2 for i in ids)
    return {
        "metrics": {
            "mse": mse,
            "rmse": math.sqrt(mse),
            "mae": sum(abs(e) for e in errors) / len(ids),
            "r2": 1 - mse * len(ids) / ss_tot if ss_tot else 0.0,
        },
        "n": len(ids),
        "self_reported": False,
    }


if __name__ == "__main__":
    run(evaluate, KEY, VERSION)
