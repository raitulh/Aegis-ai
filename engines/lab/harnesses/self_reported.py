"""Self-reported metrics harness: validates the candidate's metrics.json schema. Results are explicitly
flagged self_reported=True and cannot satisfy 'non_self_reported_metrics' verification criteria."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from _common import finite, run

KEY, VERSION = "self_reported", "1.0.0"


def evaluate(candidate: Path, data: Path, config: dict[str, Any]) -> dict[str, Any]:
    raw = json.loads((candidate / config.get("metrics_file", "metrics.json")).read_text())
    metrics = raw.get("metrics", raw)
    if not isinstance(metrics, dict) or not metrics:
        raise ValueError("metrics.json must contain a non-empty object of numeric metrics")
    clean = {str(k)[:80]: finite(float(v)) for k, v in list(metrics.items())[:100]}
    return {"metrics": clean, "n": 1, "self_reported": True}


if __name__ == "__main__":
    run(evaluate, KEY, VERSION)
