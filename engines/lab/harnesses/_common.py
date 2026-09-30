"""Shared helpers for harness scripts (copied into the harness container next to the harness)."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any

CANDIDATE_DIR = Path("/candidate")
DATA_DIR = Path("/data")
CONFIG_PATH = Path("/harness/config.json")
OUTPUT_PATH = Path("/output/metrics.json")
MAX_ROWS = 5_000_000


def read_csv_map(path: Path, key: str, value: str) -> dict[str, str]:
    out: dict[str, str] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fields = {f.strip().lower(): f for f in (reader.fieldnames or [])}
        if key not in fields or value not in fields:
            raise ValueError(f"{path.name} needs columns '{key}' and '{value}'")
        for i, row in enumerate(reader):
            if i >= MAX_ROWS:
                raise ValueError(f"{path.name} exceeds {MAX_ROWS} rows")
            out[str(row[fields[key]]).strip()] = str(row[fields[value]]).strip()
    return out


def finite(value: float) -> float:
    if math.isnan(value) or math.isinf(value):
        raise ValueError("non-finite metric")
    return value


def run(evaluate: Any, key: str, version: str) -> None:
    config = json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
    try:
        result = evaluate(CANDIDATE_DIR, DATA_DIR, config)
        payload: dict[str, Any] = {"harness": key, "version": version, "ok": True, **result}
    except Exception as exc:  # the harness reports failure as data; the platform classifies it
        payload = {"harness": key, "version": version, "ok": False, "error": f"{type(exc).__name__}: {exc}"[:500]}
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, sort_keys=True))
