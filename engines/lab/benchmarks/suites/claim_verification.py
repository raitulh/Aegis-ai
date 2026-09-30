"""ClaimVerificationBench — deciding a claim's verification status (component ``claim_verification``).

Input ``{profile: {claim_type, statement, evidence_count}, checks: [{name, required, status}]}``; the
subject returns a :class:`engines.lab.states.ClaimStatus` (bare or ``{"status": ...}``). Scored by
accuracy.

:func:`reference_claim_status` implements the fixture's documented decision rules exactly and is
usable as a baseline subject (and as a consistency check of the fixture labels).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from engines.lab.benchmarks.base import BenchmarkSuite, LabelScorer, load_fixture
from engines.lab.states import ClaimStatus

KEY = "claim_verification_bench"


def reference_claim_status(case_input: Mapping[str, Any]) -> str:
    """Apply the documented rules (required checks decide; optional checks are advisory)."""
    profile = case_input.get("profile", {})
    if int(profile.get("evidence_count", 0)) <= 0:
        return ClaimStatus.UNVERIFIED.value
    required = [c for c in case_input.get("checks", []) if c.get("required", True)]
    passed = sum(1 for c in required if c.get("status") == "passed")
    failed = sum(1 for c in required if c.get("status") == "failed")
    pending = len(required) - passed - failed
    if passed and failed:
        return ClaimStatus.CONTESTED.value
    if failed:
        return ClaimStatus.REJECTED.value
    if not passed:
        return ClaimStatus.CANDIDATE.value
    if pending:
        return ClaimStatus.PARTIALLY_VERIFIED.value
    return ClaimStatus.VERIFIED.value


def build_suite() -> BenchmarkSuite:
    meta, cases = load_fixture(f"{KEY}.json")
    labels = [s.value for s in ClaimStatus]
    return BenchmarkSuite(
        key=KEY,
        name="ClaimVerificationBench",
        version=str(meta["version"]),
        component="claim_verification",
        description=str(meta["description"]),
        cases=cases,
        scorer=LabelScorer(expected_key="status", output_keys=("status", "label"), labels=labels),
        config={
            "labels": labels,
            "decision_rules": list(meta.get("decision_rules", [])),
            "headline_metric": "accuracy",
        },
    )
