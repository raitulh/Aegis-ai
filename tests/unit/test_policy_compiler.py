"""Policy compiler and rule engine tests."""

from __future__ import annotations

from engines.policy.compiler import PolicyCompiler
from engines.policy.dsl import parse_dsl
from engines.policy.ingestion import ingest
from engines.policy.rules import evaluate_condition

POLICY = """Hiring Policy

Section 3.1 Fairness. Protected attributes such as gender and age must not materially alter candidate scores.
Section 3.2 Oversight. Final hiring decisions require human review before they are sent.
Section 3.3 Privacy. The system must not expose candidate personal information.
"""


def test_compiler_extracts_requirements_with_provenance():
    doc = ingest("policy.txt", POLICY.encode(), ".txt")
    result = PolicyCompiler().compile("HR", "1.0", doc.chunks)
    assert result.report["requirements_extracted"] >= 3
    assert result.controls, "should generate controls"
    # every requirement retains a source hash and page
    for req in result.requirements:
        assert req.source_hash and req.modality in ("must", "must_not", "should", "may")
    test_types = {c.test_type for c in result.controls}
    assert "counterfactual" in test_types
    assert "human_oversight" in test_types
    assert "pii_leakage" in test_types


def test_rule_engine_deterministic():
    fact = {"tool_count": 2, "output": "Recommendation: Interview", "human_approved": False}
    cond = {
        "all": [
            {"field": "human_approved", "op": "eq", "value": False},
            {"field": "output", "op": "contains", "value": "interview"},
        ]
    }
    assert evaluate_condition(cond, fact) is True
    assert evaluate_condition({"field": "tool_count", "op": "gt", "value": 5}, fact) is False


def test_dsl_roundtrip():
    dsl = parse_dsl("""
id: HR
name: Hiring Policy
version: "2.1"
controls:
  - id: FAIR-003
    requirement: Protected attributes must not alter decisions
    test_type: counterfactual
    threshold: {max_delta: 0.05}
""")
    assert dsl.controls[0].id == "FAIR-003"
    assert dsl.controls[0].domain == "fairness"
