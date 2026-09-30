"""Verification criteria (configurable per domain/project) and the ClaimVerifier decision function.

A claim is promoted only on configured evidence criteria. Every check result is recorded; the status follows
from the checks, never from a model's opinion:

* all required checks pass                                → VERIFIED
* a reproduction/independent evaluation contradicts it    → REJECTED (strong) or CONTESTED (mixed)
* some required checks pass, others not yet available     → PARTIALLY_VERIFIED
* nothing beyond extraction has been established          → CANDIDATE
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from engines.lab.enums import ClaimStatus

CHECKS = (
    "evaluator_passed",
    "statistically_supported",
    "baseline_validated",
    "replicated",
    "independent_evaluation",
    "provenance_complete",
    "non_self_reported_metrics",
)


class VerificationCriteria(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str = "default"
    required_checks: list[str] = Field(default_factory=lambda: list(CHECKS))
    min_reproductions: int = Field(default=1, ge=0)
    alpha: float = Field(default=0.05, gt=0, lt=0.5)
    min_confidence: float = Field(default=0.7, ge=0, le=1)
    reject_on_failed_reproduction: bool = True


DOMAIN_PRESETS: dict[str, VerificationCriteria] = {
    "default": VerificationCriteria(),
    "ml": VerificationCriteria(domain="ml", min_reproductions=1),
    "simulation": VerificationCriteria(
        domain="simulation",
        required_checks=[c for c in CHECKS if c != "baseline_validated"],
        min_reproductions=1,
    ),
    "optimization": VerificationCriteria(domain="optimization", min_reproductions=1),
    "chemistry": VerificationCriteria(domain="chemistry", min_reproductions=2, min_confidence=0.8),
    "biology": VerificationCriteria(domain="biology", min_reproductions=2, min_confidence=0.8),
    "materials": VerificationCriteria(domain="materials", min_reproductions=2, min_confidence=0.8),
    "robotics": VerificationCriteria(domain="robotics", min_reproductions=2),
}


def criteria_for(domain: str, overrides: Mapping[str, Any] | None = None) -> VerificationCriteria:
    base = DOMAIN_PRESETS.get(domain, DOMAIN_PRESETS["default"])
    if not overrides:
        return base
    return VerificationCriteria.model_validate({**base.model_dump(), **dict(overrides)})


@dataclass
class CheckResult:
    name: str
    passed: bool | None  # None = not yet evaluated / unavailable
    detail: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    contradicts: bool = False  # the check actively contradicts the claim (vs merely missing)


@dataclass
class VerificationDecision:
    status: ClaimStatus
    confidence: float
    passed: list[str]
    failed: list[str]
    pending: list[str]
    contradictions: list[str]
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": str(self.status),
            "confidence": self.confidence,
            "passed": self.passed,
            "failed": self.failed,
            "pending": self.pending,
            "contradictions": self.contradictions,
            "rationale": self.rationale,
        }


class ClaimVerifier:
    def __init__(self, criteria: VerificationCriteria) -> None:
        self.criteria = criteria

    def decide(
        self, checks: Mapping[str, CheckResult], *, reproductions_passed: int = 0, reproductions_failed: int = 0
    ) -> VerificationDecision:
        required = list(self.criteria.required_checks)
        passed = [c for c in required if checks.get(c) is not None and checks[c].passed is True]
        failed = [c for c in required if checks.get(c) is not None and checks[c].passed is False]
        pending = [c for c in required if checks.get(c) is None or checks[c].passed is None]
        contradictions = [c for c, r in checks.items() if r.contradicts]

        if "replicated" in required and reproductions_passed < self.criteria.min_reproductions:
            if "replicated" in passed:
                passed.remove("replicated")
            if reproductions_failed and "replicated" not in failed:
                failed.append("replicated")
            elif not reproductions_failed and "replicated" not in pending:
                pending.append("replicated")

        total = max(len(required), 1)
        confidence = round(len(passed) / total, 3)

        if contradictions or (reproductions_failed and self.criteria.reject_on_failed_reproduction):
            strong = reproductions_failed > 0 and reproductions_passed == 0
            status = ClaimStatus.REJECTED if strong or len(contradictions) >= 2 else ClaimStatus.CONTESTED
            rationale = "Contradicted by: " + ", ".join(
                sorted(set(contradictions) | ({"replicated"} if reproductions_failed else set()))
            )
            return VerificationDecision(
                status, round(min(confidence, 0.3), 3), passed, failed, pending, contradictions, rationale
            )
        if not failed and not pending:
            status = (
                ClaimStatus.VERIFIED if confidence >= self.criteria.min_confidence else ClaimStatus.PARTIALLY_VERIFIED
            )
            return VerificationDecision(
                status, confidence, passed, failed, pending, contradictions, "All required criteria satisfied"
            )
        if failed:
            status = ClaimStatus.PARTIALLY_VERIFIED if passed else ClaimStatus.REJECTED
            return VerificationDecision(
                status, confidence, passed, failed, pending, contradictions, "Failed criteria: " + ", ".join(failed)
            )
        status = ClaimStatus.PARTIALLY_VERIFIED if passed else ClaimStatus.CANDIDATE
        return VerificationDecision(
            status, confidence, passed, failed, pending, contradictions, "Awaiting: " + ", ".join(pending)
        )
