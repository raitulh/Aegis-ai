"""Memory write policy matrix: quarantine, review gating, trust levels, provenance and spoofing guards."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from engines.lab.memory_policy import (
    PROVENANCE_REQUIRED_KEYS,
    MemoryWriteRequest,
    decide_memory_write,
    missing_provenance,
)
from engines.lab.states import MemoryCategory, MemoryStatus

PROVENANCE = {
    "human": {"user_id": "u1"},
    "agent": {"agent_run_id": "r1", "mission_id": "m1"},
    "tool": {"tool_name": "paper_search", "invocation_id": "i1"},
    "experiment": {"experiment_run_id": "run1"},
    "external": {"source_uri": "https://example.org", "retrieved_at": "2026-09-30T00:00:00Z"},
    "system": {"component": "failures.lessons"},
}
ACTOR_FOR = {
    "human": "user",
    "agent": "agent",
    "tool": "agent",
    "experiment": "workflow",
    "external": "workflow",
    "system": "system",
}


def request(source: str = "agent", **overrides) -> MemoryWriteRequest:
    data = {
        "category": MemoryCategory.MISSION,
        "scope": "mission",
        "source_type": source,
        "actor_kind": ACTOR_FOR[source],
        "confidence": 0.8,
        "provenance": PROVENANCE[source],
    }
    data.update(overrides)
    return MemoryWriteRequest(**data)


@pytest.mark.parametrize(
    ("source", "overrides", "status", "trust", "review"),
    [
        # injection risk quarantines everything, even trusted reviewers
        ("human", {"injection_risk": 0.7, "actor_can_review": True}, "QUARANTINED", "untrusted", True),
        ("agent", {"injection_risk": 0.95}, "QUARANTINED", "untrusted", True),
        ("experiment", {"injection_risk": 0.71, "scope": "project"}, "QUARANTINED", "untrusted", True),
        # just below the quarantine threshold
        ("agent", {"injection_risk": 0.69}, "ACTIVE", "untrusted", False),
        # low confidence needs review
        ("human", {"confidence": 0.29, "actor_can_review": True}, "PROPOSED", "untrusted", True),
        ("experiment", {"confidence": 0.1}, "PROPOSED", "untrusted", True),
        # human writes
        ("human", {"actor_can_review": True, "scope": "organization"}, "ACTIVE", "trusted", False),
        ("human", {"actor_can_review": False, "scope": "project"}, "ACTIVE", "untrusted", False),
        ("human", {"provenance": {}, "actor_can_review": True}, "ACTIVE", "untrusted", False),
        # agent / tool writes above mission scope need review
        ("agent", {"scope": "project"}, "PROPOSED", "untrusted", True),
        ("agent", {"scope": "organization", "confidence": 1.0}, "PROPOSED", "untrusted", True),
        ("agent", {"scope": "workspace"}, "PROPOSED", "untrusted", True),
        ("agent", {"scope": "user"}, "PROPOSED", "untrusted", True),
        ("tool", {"scope": "project"}, "PROPOSED", "untrusted", True),
        ("external", {"scope": "organization"}, "PROPOSED", "untrusted", True),
        # mission-scope agent writes with provenance and confidence >= 0.5 are usable in the mission only
        ("agent", {}, "ACTIVE", "untrusted", False),
        ("agent", {"confidence": 0.5}, "ACTIVE", "untrusted", False),
        ("agent", {"confidence": 0.49}, "PROPOSED", "untrusted", True),
        ("tool", {}, "ACTIVE", "untrusted", False),
        ("external", {"category": MemoryCategory.LITERATURE}, "ACTIVE", "untrusted", False),
        # short-term category counts as mission scope
        ("agent", {"scope": "project", "category": MemoryCategory.SHORT_TERM}, "ACTIVE", "untrusted", False),
        # missing provenance for non-human writers
        ("agent", {"provenance": {"agent_run_id": "r1"}}, "PROPOSED", "untrusted", True),
        ("tool", {"provenance": {"tool_name": "x", "invocation_id": "  "}}, "PROPOSED", "untrusted", True),
        ("experiment", {"provenance": {}}, "PROPOSED", "untrusted", True),
        # platform-measured / platform-generated records
        ("experiment", {"scope": "project", "category": MemoryCategory.EXPERIMENT}, "ACTIVE", "reviewed", False),
        ("system", {"scope": "organization"}, "ACTIVE", "reviewed", False),
        # restricted content from non-humans needs review
        ("agent", {"sensitivity": "restricted"}, "PROPOSED", "untrusted", True),
        ("experiment", {"sensitivity": "restricted"}, "PROPOSED", "untrusted", True),
        ("agent", {"sensitivity": "confidential"}, "ACTIVE", "untrusted", False),
    ],
)
def test_memory_policy_matrix(source, overrides, status, trust, review):
    decision = decide_memory_write(request(source, **overrides))
    assert decision.status == MemoryStatus(status)
    assert decision.trust_level == trust
    assert decision.review_required is review
    assert decision.reasons


def test_external_and_tool_content_is_never_trusted():
    for source in ("external", "tool"):
        for scope in ("mission", "project", "organization"):
            for can_review in (True, False):
                decision = decide_memory_write(request(source, scope=scope, actor_can_review=can_review))
                assert decision.trust_level == "untrusted"


def test_proposed_and_quarantined_always_require_review_and_only_humans_are_trusted():
    for source in PROVENANCE:
        for scope in ("mission", "project", "organization", "workspace", "user"):
            for confidence in (0.1, 0.4, 0.9):
                for risk in (0.0, 0.8):
                    for can_review in (True, False):
                        decision = decide_memory_write(
                            request(
                                source,
                                scope=scope,
                                confidence=confidence,
                                injection_risk=risk,
                                actor_can_review=can_review,
                            )
                        )
                        if decision.status in {MemoryStatus.PROPOSED, MemoryStatus.QUARANTINED}:
                            assert decision.review_required is True
                            assert decision.trust_level == "untrusted"
                        if decision.trust_level == "trusted":
                            assert source == "human" and can_review
                            assert decision.status == MemoryStatus.ACTIVE


def test_agent_cannot_claim_human_source():
    decision = decide_memory_write(
        request("agent", source_type="human", provenance={"user_id": "u1"}, scope="organization", actor_can_review=True)
    )
    assert decision.effective_source_type == "agent"
    assert decision.status == MemoryStatus.PROPOSED
    assert "cannot be claimed" in decision.reasons[0]


def test_agent_cannot_claim_experiment_or_system_source():
    for claimed in ("experiment", "system"):
        decision = decide_memory_write(
            request("agent", source_type=claimed, provenance=PROVENANCE[claimed], scope="project")
        )
        assert decision.effective_source_type == "agent"
        assert decision.status == MemoryStatus.PROPOSED
        assert decision.trust_level == "untrusted"


def test_user_cannot_claim_experiment_source():
    decision = decide_memory_write(
        request("human", source_type="experiment", actor_kind="user", provenance=PROVENANCE["experiment"])
    )
    assert decision.effective_source_type == "agent"
    assert decision.trust_level == "untrusted"


def test_api_key_can_write_as_human():
    decision = decide_memory_write(request("human", actor_kind="api_key", actor_can_review=True))
    assert (decision.status, decision.trust_level) == (MemoryStatus.ACTIVE, "trusted")


def test_missing_provenance_reported():
    decision = decide_memory_write(request("agent", provenance={"agent_run_id": ""}))
    assert decision.missing_provenance == ["agent_run_id", "mission_id"]
    assert missing_provenance("tool", {"tool_name": "t", "invocation_id": ["x"]}) == []
    assert missing_provenance("tool", {"tool_name": "t", "invocation_id": []}) == ["invocation_id"]
    assert missing_provenance("unknown", {}) == []


def test_provenance_keys_defined_for_every_source_type():
    assert set(PROVENANCE_REQUIRED_KEYS) == {"human", "agent", "tool", "experiment", "external", "system"}
    assert all(PROVENANCE_REQUIRED_KEYS.values())


def test_decision_is_deterministic_and_versioned():
    req = request("agent", scope="project")
    assert decide_memory_write(req) == decide_memory_write(req)
    assert decide_memory_write(req).policy_version == "memory-policy-1.0.0"


@pytest.mark.parametrize(
    "bad",
    [
        {"confidence": 1.5},
        {"injection_risk": -0.1},
        {"scope": "galaxy"},
        {"source_type": "rumor"},
        {"actor_kind": "robot"},
        {"sensitivity": "secret"},
        {"unexpected": True},
    ],
)
def test_request_validation(bad):
    with pytest.raises(ValidationError):
        request("agent", **bad)
