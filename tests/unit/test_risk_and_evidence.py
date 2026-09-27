"""Risk scoring explainability and evidence hash-chain integrity."""

from __future__ import annotations

from engines.evidence.hashing import ChainState, verify_chain
from engines.risk.scoring import assess_finding_risk


def test_risk_is_explainable_and_capped():
    r = assess_finding_risk(
        severity="critical", confidence=0.9, occurrences=18, sample_size=100, environment="production", risk_tier="high"
    )
    assert r.level in ("high", "critical")
    assert r.factors and r.reasons
    assert abs(sum(f.contribution for f in r.factors) - r.score / 100 * sum(f.weight for f in r.factors)) < 1e-6


def test_low_exposure_still_respects_severity_floor():
    r = assess_finding_risk(
        severity="critical",
        confidence=0.8,
        occurrences=1,
        sample_size=100,
        environment="development",
        risk_tier="minimal",
    )
    assert r.level in ("high", "critical", "medium")


def test_evidence_chain_detects_tampering():
    chain = ChainState()
    records = []
    for i in range(5):
        c, ch, prev = chain.append({"i": i, "data": f"artifact-{i}"})
        records.append({"content_hash": c, "chain_hash": ch, "prev_hash": prev})
    assert verify_chain(records)["valid"]
    records[2]["content_hash"] = "tampered"
    assert not verify_chain(records)["valid"]
