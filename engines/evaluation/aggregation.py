"""Aggregate individual test outcomes into findings and a test matrix.

Failed results are grouped by (category, group, control) so repeated occurrences of the same issue form a
single finding with an occurrence count and sample size (feeding the explainable risk score).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from engines.common.text import stable_hash
from engines.common.types import CATEGORY_DIMENSION, SEVERITY_RANK, Severity
from engines.evaluation.base import EvaluationOutcome, TestCaseSpec


@dataclass
class ResultRow:
    case: TestCaseSpec
    outcome: EvaluationOutcome
    evaluator_key: str
    evaluator_version: str


@dataclass
class FindingGroup:
    category: str
    dimension: str
    group: str
    control_ref: str | None
    severity: str
    test_type: str
    rows: list[ResultRow] = field(default_factory=list)

    @property
    def occurrences(self) -> int:
        return sum(1 for r in self.rows if r.outcome.failed)

    @property
    def sample_size(self) -> int:
        return len(self.rows)

    @property
    def confidence(self) -> float:
        failed = [r.outcome.confidence for r in self.rows if r.outcome.failed]
        return round(max(failed), 3) if failed else 0.0

    @property
    def representative(self) -> ResultRow:
        return max(
            (r for r in self.rows if r.outcome.failed),
            key=lambda r: (SEVERITY_RANK.get(r.outcome.severity or "", 0), r.outcome.confidence),
            default=self.rows[0],
        )

    def fingerprint(self) -> str:
        return stable_hash([self.category, self.group, self.control_ref or "", self.test_type], 32)

    def title(self) -> str:
        rep = self.representative.outcome
        base = {
            "fairness": f"Counterfactual disparity on {self.group}",
            "truthfulness": "Unsupported or contradicted claims",
            "privacy": f"Personal data leakage ({self.group})",
            "safety": f"Unsafe response to {self.group.replace('_', ' ')} probe",
            "security": f"Guardrail bypass via {self.group}",
            "governance": rep.summary or "Governance control failure",
        }.get(self.dimension, rep.summary or "Policy control failure")
        return base


def group_findings(rows: list[ResultRow]) -> list[FindingGroup]:
    groups: dict[tuple[str, str, str | None], FindingGroup] = {}
    for row in rows:
        category = row.case.category
        dimension = CATEGORY_DIMENSION.get(category, "governance")
        group_key = row.outcome.group or row.case.group or "default"
        key = (category, group_key, row.case.control_ref)
        fg = groups.get(key)
        if fg is None:
            fg = FindingGroup(
                category=category,
                dimension=dimension,
                group=group_key,
                control_ref=row.case.control_ref,
                severity=Severity.INFO,
                test_type=row.case.test_type,
            )
            groups[key] = fg
        fg.rows.append(row)
        if row.outcome.failed and SEVERITY_RANK.get(row.outcome.severity or "", 0) > SEVERITY_RANK.get(fg.severity, 0):
            fg.severity = row.outcome.severity or fg.severity
    return [fg for fg in groups.values() if fg.occurrences > 0]


def test_matrix(rows: list[ResultRow]) -> list[dict[str, Any]]:
    matrix: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"samples": 0, "failures": 0, "errors": 0, "confidences": []}
    )
    for row in rows:
        key = (row.case.category, row.case.test_type)
        cell = matrix[key]
        cell["samples"] += 1
        if row.outcome.failed:
            cell["failures"] += 1
        if row.outcome.status == "error":
            cell["errors"] += 1
        if row.outcome.confidence:
            cell["confidences"].append(row.outcome.confidence)
        cell["severity"] = max(
            cell.get("severity", Severity.INFO),
            row.outcome.severity or Severity.INFO,
            key=lambda s: SEVERITY_RANK.get(s, 0),
        )
    out = []
    for (category, test_type), cell in matrix.items():
        confs = cell.pop("confidences")
        out.append(
            {
                "category": category,
                "test_type": test_type,
                "samples": cell["samples"],
                "failures": cell["failures"],
                "errors": cell["errors"],
                "status": "failed"
                if cell["failures"]
                else ("error" if cell["errors"] == cell["samples"] else "passed"),
                "severity": cell.get("severity", Severity.INFO) if cell["failures"] else Severity.INFO,
                "confidence": round(sum(confs) / len(confs), 3) if confs else None,
            }
        )
    return sorted(out, key=lambda r: (-SEVERITY_RANK.get(r["severity"], 0), r["category"]))
