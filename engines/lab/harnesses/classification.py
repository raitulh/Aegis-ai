"""Classification harness: hidden labels (harness-only split) vs candidate predictions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from _common import read_csv_map, run

KEY, VERSION = "classification", "1.0.0"


def evaluate(candidate: Path, data: Path, config: dict[str, Any]) -> dict[str, Any]:
    labels = read_csv_map(data / config.get("labels_file", "labels.csv"), "id", "label")
    preds = read_csv_map(candidate / config.get("predictions_file", "predictions.csv"), "id", "prediction")
    if not labels:
        raise ValueError("no labels")
    ids = sorted(labels)
    covered = [i for i in ids if i in preds]
    classes = sorted(set(labels.values()) | {preds[i] for i in covered})
    correct = sum(1 for i in covered if preds[i] == labels[i])
    f1s = []
    for c in classes:
        tp = sum(1 for i in covered if preds[i] == c and labels[i] == c)
        fp = sum(1 for i in covered if preds[i] == c and labels[i] != c)
        fn = sum(1 for i in ids if labels[i] == c and preds.get(i) != c)
        p = tp / (tp + fp) if tp + fp else 0.0
        r = tp / (tp + fn) if tp + fn else 0.0
        f1s.append(2 * p * r / (p + r) if p + r else 0.0)
    return {
        "metrics": {
            "accuracy": correct / len(ids),
            "f1_macro": sum(f1s) / len(f1s) if f1s else 0.0,
            "coverage": len(covered) / len(ids),
        },
        "n": len(ids),
        "self_reported": False,
    }


if __name__ == "__main__":
    run(evaluate, KEY, VERSION)
