"""CSV prediction evaluator: validation and scoring (standard library only — runs in the sandbox).

Validation and scoring are separate phases:

* ``validate`` checks the submission structure against the configuration and
  the set of test ids. Error messages never reveal hidden labels or which
  rows are public/private.
* ``score`` computes the primary and secondary metrics on the public and
  private splits. Rows are aligned by id and processed in sorted id order so
  results are reproducible bit-for-bit.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from typing import Any

from app.evaluation.metrics import METRICS, MetricUndefined

EVALUATOR_VERSION = "csv_prediction/1.0.0"
MAX_ERRORS = 20
csv.field_size_limit(1_000_000)


class EvaluationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ValidationReport:
    ok: bool
    row_count: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)

    def add(self, code: str, message: str, row: int | None = None) -> None:
        if len(self.errors) < MAX_ERRORS:
            entry: dict[str, Any] = {"code": code, "message": message}
            if row is not None:
                entry["row"] = row
            self.errors.append(entry)
        self.ok = False


def _read_rows(path: str, max_rows: int) -> tuple[list[str], list[list[str]]]:
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.reader(fh)
            try:
                header = next(reader)
            except StopIteration:
                raise EvaluationError("empty_file", "The file is empty.") from None
            rows: list[list[str]] = []
            for row in reader:
                if not row or (len(row) == 1 and row[0].strip() == ""):
                    continue
                rows.append(row)
                if len(rows) > max_rows:
                    raise EvaluationError("too_many_rows", f"The file has more than {max_rows} rows.")
            return [h.strip() for h in header], rows
    except UnicodeDecodeError:
        raise EvaluationError("encoding_error", "The file must be UTF-8 encoded CSV.") from None
    except csv.Error as exc:
        raise EvaluationError("bad_csv", f"The file is not valid CSV ({exc}).") from None


def load_ground_truth(path: str, config: dict[str, Any], max_rows: int) -> dict[str, tuple[str, str]]:
    """Returns id -> (target, usage) with usage in {"public", "private"}."""
    header, rows = _read_rows(path, max_rows)
    id_col, target_col = config["id_column"], config["target_column"]
    lower = [h.lower() for h in header]
    if id_col not in header or target_col not in header:
        raise EvaluationError("ground_truth_invalid", "Ground truth is missing the id or target column.")
    id_i, t_i = header.index(id_col), header.index(target_col)
    usage_i = lower.index("usage") if "usage" in lower else None
    truth: dict[str, tuple[str, str]] = {}
    for row in rows:
        if len(row) != len(header):
            raise EvaluationError("ground_truth_invalid", "Ground truth has malformed rows.")
        rid = row[id_i].strip()
        if rid in truth:
            raise EvaluationError("ground_truth_invalid", "Ground truth has duplicate ids.")
        usage = row[usage_i].strip().lower() if usage_i is not None else "public"
        truth[rid] = (row[t_i].strip(), "private" if usage == "private" else "public")
    if not truth:
        raise EvaluationError("ground_truth_invalid", "Ground truth is empty.")
    return truth


def inspect_ground_truth(path: str, config: dict[str, Any], max_rows: int = 5_000_000) -> dict[str, int]:
    truth = load_ground_truth(path, config, max_rows)
    private = sum(1 for _, usage in truth.values() if usage == "private")
    metric = METRICS[config["metric"]]
    if metric.kind == "numeric":
        for target, _ in truth.values():
            _parse_float(target)
    return {"row_count": len(truth), "public_count": len(truth) - private, "private_count": private}


def _parse_float(value: str) -> float:
    x = float(value)
    if math.isnan(x) or math.isinf(x):
        raise ValueError("non-finite")
    return x


def _check_value(kind: str, value: str) -> str | None:
    if kind == "label":
        return None if value.strip() != "" else "empty prediction"
    try:
        x = _parse_float(value)
    except ValueError:
        return "not a finite number"
    if kind == "probability" and not 0.0 <= x <= 1.0:
        return "probability must be between 0 and 1"
    return None


def validate(submission_path: str, ground_truth_path: str, config: dict[str, Any], max_rows: int) -> ValidationReport:
    report = ValidationReport(ok=True)
    id_col, target_col = config["id_column"], config["target_column"]
    metric = METRICS[config["metric"]]
    try:
        header, rows = _read_rows(submission_path, max_rows)
    except EvaluationError as exc:
        report.add(exc.code, exc.message)
        return report

    seen_headers: set[str] = set()
    for h in header:
        if h in seen_headers:
            report.add("duplicate_column", f"Column '{h}' appears more than once.")
        seen_headers.add(h)
    for required in (id_col, target_col):
        if required not in header:
            report.add("missing_column", f"Required column '{required}' is missing. Expected header: {id_col},{target_col}")
    if config.get("strict_schema", True):
        extras = [h for h in header if h not in (id_col, target_col)]
        if extras:
            report.add("unexpected_column", f"Unexpected column(s): {', '.join(extras[:5])}. Remove them or match the sample submission.")
    if not report.ok:
        return report

    try:
        truth = load_ground_truth(ground_truth_path, config, max_rows * 2)
    except EvaluationError as exc:
        raise exc  # configuration problem, not the participant's fault

    id_i, t_i = header.index(id_col), header.index(target_col)
    seen: set[str] = set()
    for line_no, row in enumerate(rows, start=2):
        if len(row) != len(header):
            report.add("malformed_row", f"Row has {len(row)} fields; expected {len(header)}.", line_no)
            continue
        rid = row[id_i].strip()
        if rid == "":
            report.add("missing_id", "Empty id.", line_no)
            continue
        if rid in seen:
            report.add("duplicate_id", f"Duplicate id '{rid[:40]}'.", line_no)
        seen.add(rid)
        problem = _check_value(metric.kind, row[t_i])
        if problem:
            report.add("invalid_value", f"Invalid prediction for id '{rid[:40]}': {problem}.", line_no)

    expected = set(truth.keys())
    missing = sorted(expected - seen)
    unexpected = sorted(seen - expected)
    if missing:
        sample = ", ".join(m[:40] for m in missing[:5])
        report.add("missing_ids", f"{len(missing)} test id(s) have no prediction (e.g. {sample}).")
    if unexpected:
        sample = ", ".join(u[:40] for u in unexpected[:5])
        report.add("unexpected_ids", f"{len(unexpected)} id(s) are not in the test set (e.g. {sample}).")
    expected_rows = config.get("expected_row_count")
    if expected_rows and len(rows) != int(expected_rows):
        report.add("row_count_mismatch", f"Expected {expected_rows} rows, found {len(rows)}.")
    report.row_count = len(rows)
    return report


def _prepare(metric_key: str, truth_vals: list[str], pred_vals: list[str], positive_label: str) -> tuple[list[Any], list[Any]]:
    metric = METRICS[metric_key]
    if metric.kind == "label":
        return truth_vals, [p.strip() for p in pred_vals]
    if metric.kind == "numeric":
        return [_parse_float(t) for t in truth_vals], [_parse_float(p) for p in pred_vals]
    return [1 if t == positive_label else 0 for t in truth_vals], [_parse_float(p) for p in pred_vals]


def score(submission_path: str, ground_truth_path: str, config: dict[str, Any], max_rows: int) -> dict[str, Any]:
    id_col, target_col = config["id_column"], config["target_column"]
    positive = str(config.get("positive_label", "1"))
    truth = load_ground_truth(ground_truth_path, config, max_rows * 2)
    header, rows = _read_rows(submission_path, max_rows)
    id_i, t_i = header.index(id_col), header.index(target_col)
    preds = {row[id_i].strip(): row[t_i] for row in rows}

    splits: dict[str, list[str]] = {"public": [], "private": []}
    for rid in sorted(truth):
        splits[truth[rid][1]].append(rid)

    def compute(metric_key: str, ids: list[str]) -> float | None:
        if not ids:
            return None
        t, p = _prepare(metric_key, [truth[i][0] for i in ids], [preds[i] for i in ids], positive)
        try:
            return float(METRICS[metric_key].fn(t, p))
        except MetricUndefined as exc:
            raise EvaluationError("metric_undefined", f"Metric {metric_key} is undefined for this split: {exc}") from None

    primary = config["metric"]
    public = compute(primary, splits["public"])
    private = compute(primary, splits["private"])
    if private is None:
        private = public  # no private split configured: final ranking uses the same rows
    secondary: dict[str, dict[str, float | None]] = {}
    for key in config.get("secondary_metrics", []) or []:
        if key in METRICS and key != primary:
            secondary[key] = {"public": compute(key, splits["public"]), "private": compute(key, splits["private"])}
    return {"public": public, "private": private, "secondary": secondary, "row_count": len(rows)}
