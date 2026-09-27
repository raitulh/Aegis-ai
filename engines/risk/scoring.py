"""Explainable risk scoring.

Risk is a transparent weighted combination of named factors — never an opaque single number. Every score
comes with the factors that produced it and human-readable reasons, so a reviewer can see exactly why a
finding is rated the way it is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from engines.common.types import SEVERITY_RANK, Severity

RISK_ORDER = ["informational", "low", "medium", "high", "critical"]


@dataclass
class RiskFactor:
    key: str
    label: str
    value: float  # normalised 0..1 contribution input
    weight: float
    detail: str

    @property
    def contribution(self) -> float:
        return round(self.value * self.weight, 4)


@dataclass
class RiskAssessment:
    level: str
    score: float  # 0..100
    factors: list[RiskFactor]
    reasons: list[str] = field(default_factory=list)

    def to_public(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "score": round(self.score, 1),
            "reasons": self.reasons,
            "factors": [
                {
                    "key": f.key,
                    "label": f.label,
                    "value": round(f.value, 3),
                    "weight": f.weight,
                    "contribution": f.contribution,
                    "detail": f.detail,
                }
                for f in self.factors
            ],
        }


ENV_WEIGHT = {"production": 1.0, "staging": 0.6, "development": 0.4}
TIER_WEIGHT = {"critical": 1.0, "high": 0.85, "limited": 0.55, "minimal": 0.35}
SEVERITY_VALUE = {"critical": 1.0, "high": 0.8, "medium": 0.55, "low": 0.3, "info": 0.1}


def assess_finding_risk(
    *,
    severity: str,
    confidence: float,
    occurrences: int,
    sample_size: int,
    environment: str,
    risk_tier: str,
    policy_importance: float = 0.5,
    evidence_confidence: float = 0.8,
    business_criticality: float | None = None,
    exploitability: float | None = None,
) -> RiskAssessment:
    frequency = (occurrences / sample_size) if sample_size else 0.0
    factors = [
        RiskFactor(
            "severity",
            "Technical severity",
            SEVERITY_VALUE.get(severity, 0.5),
            0.30,
            f"Evaluator severity: {severity}.",
        ),
        RiskFactor(
            "confidence",
            "Detection confidence",
            max(0.0, min(confidence, 1.0)),
            0.15,
            f"Evaluator confidence {confidence:.0%}.",
        ),
        RiskFactor(
            "frequency",
            "Frequency across tests",
            min(1.0, frequency * 1.5),
            0.15,
            f"Observed in {occurrences}/{sample_size} test(s) ({frequency:.0%}).",
        ),
        RiskFactor(
            "exposure", "Deployment exposure", ENV_WEIGHT.get(environment, 0.5), 0.12, f"System runs in {environment}."
        ),
        RiskFactor(
            "use_case",
            "Use-case risk tier",
            TIER_WEIGHT.get(risk_tier, 0.55),
            0.13,
            f"Declared risk tier: {risk_tier}.",
        ),
        RiskFactor(
            "policy",
            "Policy importance",
            max(0.0, min(policy_importance, 1.0)),
            0.08,
            "Importance of the mapped policy control.",
        ),
        RiskFactor(
            "evidence",
            "Evidence quality",
            max(0.0, min(evidence_confidence, 1.0)),
            0.07,
            f"Evidence confidence {evidence_confidence:.0%}.",
        ),
    ]
    if business_criticality is not None:
        factors.append(
            RiskFactor(
                "business",
                "Business criticality",
                max(0.0, min(business_criticality, 1.0)),
                0.10,
                "Operator-configured business criticality.",
            )
        )
    if exploitability is not None:
        factors.append(
            RiskFactor(
                "exploitability",
                "Exploitability",
                max(0.0, min(exploitability, 1.0)),
                0.10,
                "Ease of triggering the issue.",
            )
        )
    total_weight = sum(f.weight for f in factors)
    score = 100 * sum(f.contribution for f in factors) / total_weight if total_weight else 0.0

    # Severity caps: a critical technical severity cannot be diluted below High by low exposure.
    level = _score_to_level(score)
    sev_floor = {"critical": "high", "high": "medium"}.get(severity)
    if sev_floor and RISK_ORDER.index(level) < RISK_ORDER.index(sev_floor) and confidence >= 0.6:
        level = sev_floor
    reasons = _reasons(
        severity, frequency, occurrences, sample_size, environment, risk_tier, confidence, evidence_confidence
    )
    return RiskAssessment(level=level, score=score, factors=factors, reasons=reasons)


def _score_to_level(score: float) -> str:
    if score >= 78:
        return "critical"
    if score >= 60:
        return "high"
    if score >= 40:
        return "medium"
    if score >= 22:
        return "low"
    return "informational"


def _reasons(
    severity: str, frequency: float, occ: int, n: int, env: str, tier: str, conf: float, ev: float
) -> list[str]:
    reasons = [f"{severity.title()} severity from the evaluator"]
    if n:
        reasons.append(f"Reproduced in {occ}/{n} tests ({frequency:.0%})")
    if env == "production":
        reasons.append("Affects a production system")
    if tier in ("high", "critical"):
        reasons.append(f"{tier.title()}-risk use case")
    reasons.append(f"Detection confidence {conf:.0%}, evidence confidence {ev:.0%}")
    return reasons


def posture_from_scores(scores: dict[str, float]) -> str:
    if not scores:
        return "unknown"
    worst = min(scores.values())
    if worst >= 85:
        return "strong"
    if worst >= 70:
        return "moderate"
    if worst >= 50:
        return "elevated"
    return "at_risk"


def dimension_scores_from_results(results: list[dict[str, Any]]) -> dict[str, float]:
    """Map raw per-category pass/fail + severity into 0-100 dimension scores (higher = better)."""
    from engines.common.types import CATEGORY_DIMENSION

    buckets: dict[str, list[float]] = {}
    for r in results:
        dim = CATEGORY_DIMENSION.get(r.get("category", ""), None)
        if dim is None:
            continue
        if r.get("status") == "passed":
            penalty = 0.0
        elif r.get("status") == "failed":
            penalty = 0.4 + 0.15 * SEVERITY_RANK.get(r.get("severity") or Severity.MEDIUM, 2)
        else:
            continue
        buckets.setdefault(dim, []).append(max(0.0, 1.0 - penalty))
    return {dim: round(100 * sum(v) / len(v), 1) for dim, v in buckets.items() if v}
