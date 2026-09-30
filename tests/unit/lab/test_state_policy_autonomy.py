"""Unit tests: lifecycle state machines, autonomy rules and the policy engine."""

from __future__ import annotations

import pytest

from engines.lab.autonomy import AutonomyViolation, assert_actor_may, requires_human, validate_autonomy_change
from engines.lab.enums import AutonomyLevel, PolicyDecision
from engines.lab.policy.engine import PolicyEngine, PolicyError, PolicySet, parse_policy_document
from engines.lab.state_machines import CLAIM, DISCOVERY, EXECUTION, EXPERIMENT, MISSION, InvalidTransition


def test_mission_lifecycle_valid_path() -> None:
    state = "draft"
    for target in ("planned", "approved", "running", "paused", "running", "completed", "archived"):
        state = MISSION.ensure(state, target)
    assert MISSION.is_terminal(state)


def test_invalid_transitions_rejected() -> None:
    with pytest.raises(InvalidTransition):
        MISSION.ensure("draft", "running")
    with pytest.raises(InvalidTransition):
        EXPERIMENT.ensure("running", "verified")  # cannot skip evaluation
    with pytest.raises(InvalidTransition):
        DISCOVERY.ensure("candidate", "approved")  # cannot skip verification + human review
    with pytest.raises(InvalidTransition):
        EXECUTION.ensure("failed", "running")
    assert CLAIM.can("candidate", "verified")
    assert not CLAIM.can("unverified", "verified")


def test_autonomy_requires_human() -> None:
    assert requires_human(AutonomyLevel.L0_ASSISTED, "research.run")
    assert not requires_human(AutonomyLevel.L1_RESEARCH_AUTOMATION, "research.run")
    assert requires_human(AutonomyLevel.L2_AUTOMATED_EXPERIMENT_DESIGN, "experiment.execute")
    assert not requires_human(AutonomyLevel.L3_AUTOMATED_EXECUTION, "experiment.execute")
    # Discovery approval always needs a human, even at L5.
    assert requires_human(AutonomyLevel.L5_LONG_HORIZON_AUTONOMOUS_RND, "discovery.approve")


def test_agents_cannot_change_autonomy() -> None:
    with pytest.raises(AutonomyViolation):
        assert_actor_may("agent", "autonomy.change")
    with pytest.raises(AutonomyViolation):
        validate_autonomy_change(
            "workflow",
            "L1_RESEARCH_AUTOMATION",
            "L4_CLOSED_LOOP_EVOLUTION",
            max_allowed="L5_LONG_HORIZON_AUTONOMOUS_RND",
        )
    with pytest.raises(AutonomyViolation):
        validate_autonomy_change(
            "user", "L1_RESEARCH_AUTOMATION", "L5_LONG_HORIZON_AUTONOMOUS_RND", max_allowed="L3_AUTOMATED_EXECUTION"
        )
    assert (
        validate_autonomy_change(
            "user", "L1_RESEARCH_AUTOMATION", "L3_AUTOMATED_EXECUTION", max_allowed="L4_CLOSED_LOOP_EVOLUTION"
        )
        == "L3_AUTOMATED_EXECUTION"
    )


def test_baseline_blocks_strategy_promotion_below_l4() -> None:
    engine = PolicyEngine()
    r = engine.evaluate("strategy.promote", {"actor": {"type": "workflow"}, "mission": {"autonomy_rank": 3}})
    assert r.decision == PolicyDecision.REQUIRE_APPROVAL
    assert "strategy_promotion" in r.approval_kinds


def test_auto_promotion_requires_org_opt_in_even_at_l4() -> None:
    engine = PolicyEngine()
    facts = {"actor": {"type": "workflow"}, "mission": {"autonomy_rank": 4}}
    assert engine.evaluate("strategy.promote", facts).needs_approval
    facts["org"] = {"allow_auto_strategy_promotion": True}
    assert engine.evaluate("strategy.promote", facts).allowed


def test_discovery_approval_rules() -> None:
    engine = PolicyEngine()
    base = {"discovery": {"requested_by": "u1"}, "claim": {"status": "verified"}}
    assert engine.evaluate("discovery.approve", {**base, "actor": {"type": "agent", "id": "a"}}).denied
    assert engine.evaluate("discovery.approve", {**base, "actor": {"type": "user", "id": "u1"}}).denied  # SoD
    assert engine.evaluate("discovery.approve", {**base, "actor": {"type": "user", "id": "u2"}}).allowed
    unverified = {**base, "claim": {"status": "candidate"}, "actor": {"type": "user", "id": "u2"}}
    assert engine.evaluate("discovery.approve", unverified).denied


def test_execution_defaults_network_secrets_gpu_budget() -> None:
    engine = PolicyEngine()
    assert engine.evaluate("experiment.execute", {"execution": {"network": "none"}}).allowed
    assert engine.evaluate("experiment.execute", {"execution": {"network": "allowlist"}}).needs_approval
    assert engine.evaluate("experiment.execute", {"execution": {"secrets_requested": ["k"]}}).needs_approval
    assert engine.evaluate("experiment.execute", {"execution": {"gpu_count": 1}}).needs_approval
    assert engine.evaluate("experiment.execute", {"budget": {"would_exceed": True}}).needs_approval
    r = engine.evaluate("experiment.execute", {"estimated_cost": 50, "org": {"expensive_compute_threshold_usd": 10}})
    assert "expensive_compute" in r.approval_kinds


def test_tool_rules() -> None:
    engine = PolicyEngine()
    assert engine.evaluate("tool.call", {"tool": {"risk": "high"}}).needs_approval
    assert engine.evaluate("tool.call", {"tool": {"risk": "low"}}).allowed
    assert engine.evaluate("tool.call", {"tool": {"source": "mcp", "registered": False}}).denied


def test_org_policy_adds_restrictions_but_cannot_relax_baseline() -> None:
    org = parse_policy_document(
        "org.research",
        "3",
        {
            "rules": [
                {
                    "id": "cap-cost",
                    "actions": ["experiment.*"],
                    "when": {"fact": "estimated_cost", "op": "gt", "value": 5},
                    "effect": "deny",
                    "reason": "too expensive",
                },
                {"id": "allow-network", "actions": ["experiment.execute"], "effect": "allow"},
            ]
        },
    )
    engine = PolicyEngine([org])
    assert engine.evaluate("experiment.execute", {"estimated_cost": 6}).denied
    # An org "allow" cannot override the baseline network approval requirement (deny-overrides).
    assert engine.evaluate("experiment.execute", {"execution": {"network": "allowlist"}}).needs_approval
    result = engine.evaluate("experiment.execute", {"estimated_cost": 6})
    assert any(m.policy_key == "org.research" and m.policy_version == "3" for m in result.matched)


def test_policy_validation_rejects_bad_documents() -> None:
    with pytest.raises(PolicyError):
        parse_policy_document(
            "x", "1", {"rules": [{"id": "r", "effect": "deny", "when": {"fact": "a", "op": "regex", "value": "x"}}]}
        )
    with pytest.raises(PolicyError):
        parse_policy_document("x", "1", {"rules": [{"id": "r", "effect": "deny", "when": {"all": []}}]})
    ps = PolicySet(key="k", version="1")
    assert len(ps.fingerprint) == 64


def test_fact_ref_comparison_and_missing_facts() -> None:
    org = parse_policy_document(
        "org.budget",
        "1",
        {
            "rules": [
                {
                    "id": "over",
                    "actions": ["*"],
                    "when": {"fact": "estimated_cost", "op": "gt", "fact_ref": "project.budget"},
                    "effect": "require_approval",
                }
            ]
        },
    )
    engine = PolicyEngine([org], include_baseline=False)
    assert engine.evaluate("x", {"estimated_cost": 10, "project": {"budget": 5}}).needs_approval
    assert engine.evaluate("x", {"estimated_cost": 1, "project": {"budget": 5}}).allowed
    assert engine.evaluate("x", {"estimated_cost": 10}).allowed  # missing facts never match comparisons
