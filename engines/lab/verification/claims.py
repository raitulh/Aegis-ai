"""ClaimExtractor: deterministic, carefully-worded claims derived from measured comparisons.

Claims generated here quote the measured numbers, sample sizes and uncertainty; they never generalize beyond
the experiment (no "proves", "always", "state of the art"). Model-extracted claims from free text are handled
by the application layer and are always stored as UNVERIFIED with ``source='model_extracted'``.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExtractedClaim:
    statement: str
    claim_type: str  # improvement | non_regression | threshold | reproduction
    metric: str
    baseline_value: float | None
    candidate_value: float | None
    delta: float | None
    relative_change: float | None
    ci: tuple[float, float] | None
    p_value: float | None
    n_candidate: int
    n_baseline: int
    scope: dict[str, Any] = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        key = f"{self.claim_type}|{self.metric}|{self.scope.get('experiment_id')}|{self.scope.get('candidate')}"
        return hashlib.sha256(key.encode()).hexdigest()[:32]

    def to_dict(self) -> dict[str, Any]:
        return {
            "statement": self.statement,
            "claim_type": self.claim_type,
            "metric": self.metric,
            "baseline_value": self.baseline_value,
            "candidate_value": self.candidate_value,
            "delta": self.delta,
            "relative_change": self.relative_change,
            "ci": list(self.ci) if self.ci else None,
            "p_value": self.p_value,
            "n_candidate": self.n_candidate,
            "n_baseline": self.n_baseline,
            "scope": self.scope,
            "fingerprint": self.fingerprint,
        }


def _fmt(value: float | None, digits: int = 4) -> str:
    if value is None or (isinstance(value, float) and (math.isnan(value) or math.isinf(value))):
        return "n/a"
    return f"{value:.{digits}g}"


class ClaimExtractor:
    """Build claims from BenchmarkEvaluator comparisons + StatisticalEvaluator rows."""

    def extract(
        self,
        comparisons: dict[str, dict[str, Any]],
        statistical_rows: list[dict[str, Any]] | None = None,
        *,
        experiment_title: str,
        experiment_id: str | None = None,
        baseline_name: str = "baseline",
        candidate_name: str = "candidate",
        dataset_label: str | None = None,
    ) -> list[ExtractedClaim]:
        stats = {r["metric"]: r for r in (statistical_rows or []) if "metric" in r}
        claims: list[ExtractedClaim] = []
        for metric, comp in sorted(comparisons.items()):
            improvement = comp.get("improvement")
            if improvement is None:
                continue
            row = stats.get(metric, {})
            p_value = row.get("p_adjusted", row.get("p_value"))
            ci = (comp.get("ci_low"), comp.get("ci_high"))
            ci_valid = ci[0] is not None and ci[1] is not None and not math.isnan(ci[0]) and not math.isnan(ci[1])
            rel = comp.get("relative_change")
            where = f" on {dataset_label}" if dataset_label else ""
            numbers = (
                f"{metric}: {_fmt(comp.get('candidate_mean'))} vs {_fmt(comp.get('baseline_mean'))}"
                + (f" ({rel:+.1%} relative)" if isinstance(rel, float) else "")
                + (f", 95% CI of difference [{_fmt(ci[0])}, {_fmt(ci[1])}]" if ci_valid else "")
                + (f", adjusted p={_fmt(p_value, 3)}" if p_value is not None else "")
                + f"; n={comp.get('n_candidate', 0)} candidate / {comp.get('n_baseline', 0)} baseline runs"
            )
            if improvement > 0:
                claim_type = "improvement"
                verb = "improved"
            elif improvement == 0:
                claim_type = "non_regression"
                verb = "matched"
            else:
                claim_type = "regression"
                verb = "underperformed"
            statement = (
                f"In experiment '{experiment_title}'{where}, {candidate_name} {verb} {baseline_name} on {numbers}."
            )
            claims.append(
                ExtractedClaim(
                    statement=statement,
                    claim_type=claim_type,
                    metric=metric,
                    baseline_value=comp.get("baseline_mean"),
                    candidate_value=comp.get("candidate_mean"),
                    delta=comp.get("delta"),
                    relative_change=rel if isinstance(rel, float) else None,
                    ci=(float(ci[0]), float(ci[1])) if ci_valid else None,  # type: ignore[arg-type]
                    p_value=p_value,
                    n_candidate=int(comp.get("n_candidate", 0)),
                    n_baseline=int(comp.get("n_baseline", 0)),
                    scope={
                        "experiment_id": experiment_id,
                        "baseline": baseline_name,
                        "candidate": candidate_name,
                        "dataset": dataset_label,
                        "direction": comp.get("direction"),
                    },
                )
            )
        return claims


_OVERCLAIM_TERMS = (
    "prove",
    "proves",
    "proven",
    "guarantee",
    "guarantees",
    "definitively",
    "always",
    "never fails",
    "state-of-the-art",
    "state of the art",
    "breakthrough",
    "revolutionary",
    "certified",
    "compliant",
    "conclusively",
    "universally",
)


def overclaiming_terms(text: str) -> list[str]:
    lowered = text.lower()
    return sorted({t for t in _OVERCLAIM_TERMS if f" {t} " in f" {lowered} " or f" {t}." in f" {lowered}"})
