"""Policy DSL: validation, operators, autonomy/risk conditions, combining, and every baseline rule."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from engines.lab.policy import (
    ACTIONS,
    BASELINE,
    BASELINE_VERSION,
    DEFAULT_EFFECT,
    MAX_RULES,
    PolicySet,
    PolicyValidationError,
    Rule,
    action_matches,
    evaluate,
    evaluate_condition,
    parse_rules,
    validate_rules,
)


def rule(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"id": "org.test", "action": "execution.submit", "effect": "deny", "reason": "test"}
    base.update(overrides)
    return base


def cond(when: dict[str, Any], context: dict[str, Any]) -> bool:
    parsed = Rule.model_validate(rule(when=when))
    return evaluate_condition(parsed.when, context)


# ---------------------------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "fragment"),
    [
        (rule(when={"field": "gpu_count", "op": "bogus", "value": 1}), "when.op"),
        (rule(when={"field": "gpu_count", "op": "gt"}), "needs exactly one of 'value' or 'value_from'"),
        (rule(when={"field": "gpu_count", "op": "exists", "value": 1}), "takes no 'value'"),
        (
            rule(when={"field": "gpu_count", "op": "gt", "value": 1, "value_from": "estimated_cost_usd"}),
            "exactly one of",
        ),
        (rule(when={"field": "gpu_count", "op": "gt", "value": "many"}), "numeric 'value'"),
        (rule(when={"field": "gpu_count", "op": "in", "value": 3}), "list 'value'"),
        (rule(when={"field": "tool_name", "op": "matches", "value": "("}), "invalid regular expression"),
        (rule(when={"field": "tool_name", "op": "matches", "value": "(a+)+$"}), "nested quantifiers"),
        (rule(when={"field": "no_such_key", "op": "exists"}), "unknown context key 'no_such_key'"),
        (rule(when={"field": "BadKey", "op": "exists"}), "not a valid context key"),
        (rule(action="execution.explode"), "unknown action 'execution.explode'"),
        (rule(action="nuclear.*"), "unknown action namespace 'nuclear'"),
        (rule(effect="maybe"), "effect"),
        (rule(reason=""), "reason"),
        (rule(id="Bad ID!"), "rule id must be"),
        (rule(approver_permission="approval:decide"), "only valid for effect 'require_approval'"),
        (rule(effect="require_approval", approver_permission="not a permission"), "resource:verb"),
        (rule(when={"bogus": 1}), "A condition must be one of"),
        (rule(when={"all": []}), "when.all"),
        (rule(when={"autonomy_below": "L9"}), "when.autonomy_below"),
        (rule(when={"risk_at_least": "EXTREME"}), "when.risk_at_least"),
        (rule(obligations={"x": float("nan")}), "JSON-serializable"),
        (rule(priority=-1), "priority"),
        (rule(extra_key=True), "extra_key"),
    ],
)
def test_validation_errors_are_human_friendly(raw: dict[str, Any], fragment: str) -> None:
    errors = validate_rules([raw])
    assert errors, f"expected a validation error for {raw}"
    assert any(fragment in e for e in errors), errors
    assert all(e.startswith("rule #1 ('") or e.startswith("rule #1:") or "rule #1" in e for e in errors)


def test_nested_error_paths_hide_discriminator_tags() -> None:
    errors = validate_rules([rule(when={"all": [{"any": [{"field": "gpu_count", "op": "zz", "value": 1}]}]})])
    assert any("when.all[0].any[0].op:" in e for e in errors), errors


def test_depth_limit() -> None:
    when: dict[str, Any] = {"field": "gpu_count", "op": "exists"}
    for _ in range(9):
        when = {"not": when}
    assert any("nested at most" in e for e in validate_rules([rule(when=when)]))


def test_duplicate_ids_reserved_prefix_and_rule_count() -> None:
    errors = validate_rules([rule(), rule()])
    assert any("duplicate rule id 'org.test'" in e for e in errors)
    errors = validate_rules([rule(id="baseline.sneaky", effect="allow")])
    assert any("reserved for the platform baseline" in e for e in errors)
    errors = validate_rules([rule(id=f"r{i}") for i in range(MAX_RULES + 1)])
    assert any(f"at most {MAX_RULES} rules" in e for e in errors)


def test_parse_rules_raises_with_all_errors() -> None:
    with pytest.raises(PolicyValidationError) as exc:
        parse_rules([rule(effect="nope"), rule(id="ok.2", action="nope.x")])
    assert len(exc.value.errors) == 2


def test_valid_rules_and_canonical_documents() -> None:
    rules = parse_rules(
        [
            rule(
                id="org.gpu",
                action="execution.*",
                effect="require_approval",
                approver_permission="approval:decide",
                when={"all": [{"field": "gpu_count", "op": "gte", "value": 2}, {"autonomy_below": "L4"}]},
                obligations={"max_runtime_seconds": 600},
                priority=5,
            )
        ]
    )
    doc = rules[0].to_document()
    assert doc["when"]["all"][1] == {"autonomy_below": "L4_CLOSED_LOOP_EVOLUTION", "field": "autonomy_level"}
    assert Rule.model_validate(doc) == rules[0]  # round-trips through storage
    assert validate_rules([doc]) == []


# ---------------------------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("when", "context", "expected"),
    [
        ({"field": "network_mode", "op": "eq", "value": "none"}, {"network_mode": "none"}, True),
        ({"field": "network_mode", "op": "eq", "value": "none"}, {}, False),
        ({"field": "network_mode", "op": "ne", "value": "none"}, {"network_mode": "allowlist"}, True),
        ({"field": "network_mode", "op": "ne", "value": "none"}, {}, False),  # missing → false
        ({"field": "gpu_count", "op": "gt", "value": 0}, {"gpu_count": 1}, True),
        ({"field": "gpu_count", "op": "gt", "value": 0}, {"gpu_count": 0}, False),
        ({"field": "gpu_count", "op": "gte", "value": 2}, {"gpu_count": 2}, True),
        ({"field": "gpu_count", "op": "lt", "value": 2}, {"gpu_count": 1.5}, True),
        ({"field": "gpu_count", "op": "lte", "value": 2}, {"gpu_count": Decimal("2.0")}, True),
        ({"field": "gpu_count", "op": "gt", "value": 0}, {"gpu_count": "3"}, False),  # strings are not numbers
        ({"field": "gpu_count", "op": "gt", "value": 0}, {"gpu_count": True}, False),  # bools are not numbers
        ({"field": "production", "op": "eq", "value": True}, {"production": 1}, False),  # bool vs int
        ({"field": "actor_kind", "op": "in", "value": ["agent", "workflow"]}, {"actor_kind": "agent"}, True),
        ({"field": "actor_kind", "op": "in", "value": ["agent"]}, {"actor_kind": "user"}, False),
        ({"field": "actor_kind", "op": "not_in", "value": ["agent"]}, {"actor_kind": "user"}, True),
        ({"field": "actor_kind", "op": "not_in", "value": ["agent"]}, {}, False),
        ({"field": "egress_hosts", "op": "contains", "value": "a.org"}, {"egress_hosts": ["a.org", "b.org"]}, True),
        ({"field": "egress_hosts", "op": "contains", "value": "c.org"}, {"egress_hosts": ["a.org"]}, False),
        ({"field": "tool_name", "op": "contains", "value": "fetch"}, {"tool_name": "url_fetch"}, True),
        ({"field": "x_meta", "op": "contains", "value": "k"}, {"x_meta": {"k": 1}}, True),
        ({"field": "egress_hosts", "op": "not_contains", "value": "c.org"}, {"egress_hosts": ["a.org"]}, True),
        ({"field": "egress_hosts", "op": "not_contains", "value": "c.org"}, {}, False),
        ({"field": "tool_name", "op": "exists"}, {"tool_name": "x"}, True),
        ({"field": "tool_name", "op": "exists"}, {"tool_name": None}, False),
        ({"field": "tool_name", "op": "not_exists"}, {}, True),
        ({"field": "tool_name", "op": "matches", "value": "^web_"}, {"tool_name": "web_search"}, True),
        ({"field": "tool_name", "op": "matches", "value": "^web_"}, {"tool_name": "python"}, False),
        ({"field": "tool_name", "op": "matches", "value": "^web_"}, {"tool_name": 42}, False),
        (
            {"field": "egress_hosts", "op": "subset_of", "value": [".wikipedia.org", "arxiv.org"]},
            {"egress_hosts": ["en.wikipedia.org", "ARXIV.org"]},
            True,
        ),
        (
            {"field": "egress_hosts", "op": "subset_of", "value": [".wikipedia.org"]},
            {"egress_hosts": ["wikipedia.org.evil.com"]},
            False,
        ),
        ({"field": "egress_hosts", "op": "subset_of", "value": ["a.org"]}, {"egress_hosts": "a.org"}, True),
        ({"field": "egress_hosts", "op": "subset_of", "value": ["a.org"]}, {"egress_hosts": []}, True),
    ],
)
def test_operators(when: dict[str, Any], context: dict[str, Any], expected: bool) -> None:
    assert cond(when, context) is expected


def test_value_from_compares_two_context_keys() -> None:
    when = {"field": "estimated_cost_usd", "op": "gt", "value_from": "budget_remaining_usd"}
    assert cond(when, {"estimated_cost_usd": 12, "budget_remaining_usd": Decimal("10")}) is True
    assert cond(when, {"estimated_cost_usd": 5, "budget_remaining_usd": 10}) is False
    assert cond(when, {"estimated_cost_usd": 12}) is False  # missing value_from → false
    subset = {"field": "egress_hosts", "op": "subset_of", "value_from": "org_egress_allowlist"}
    assert cond(subset, {"egress_hosts": ["a.org"], "org_egress_allowlist": ["a.org"]}) is True
    assert cond(subset, {"egress_hosts": ["a.org"]}) is False


def test_boolean_combinators() -> None:
    ctx = {"gpu_count": 2, "network_mode": "none"}
    gpu = {"field": "gpu_count", "op": "gt", "value": 0}
    net = {"field": "network_mode", "op": "ne", "value": "none"}
    assert cond({"all": [gpu, {"not": net}]}, ctx) is True
    assert cond({"all": [gpu, net]}, ctx) is False
    assert cond({"any": [gpu, net]}, ctx) is True
    assert cond({"not": {"any": [gpu, net]}}, ctx) is False


def test_autonomy_and_risk_conditions() -> None:
    assert cond({"autonomy_below": "L3"}, {"autonomy_level": "L2_AUTOMATED_EXPERIMENT_DESIGN"}) is True
    assert cond({"autonomy_below": "L3"}, {"autonomy_level": "L3_AUTOMATED_EXECUTION"}) is False
    assert cond({"autonomy_below": "L1"}, {}) is True  # missing autonomy = L0 (fail-safe)
    assert cond({"autonomy_below": "L1"}, {"autonomy_level": "garbage"}) is True
    assert cond({"autonomy_below": "L0"}, {}) is False
    assert cond({"autonomy_at_least": "L2"}, {"autonomy_level": "L4"}) is True
    assert cond({"autonomy_at_least": "L2"}, {}) is False
    assert cond({"risk_at_least": "HIGH"}, {"risk_level": "CRITICAL"}) is True
    assert cond({"risk_at_least": "HIGH"}, {"risk_level": "MEDIUM"}) is False
    assert cond({"risk_at_least": "high", "field": "tool_risk"}, {"tool_risk": "high"}) is True
    assert cond({"risk_at_least": "HIGH", "field": "tool_risk"}, {}) is False
    assert cond({"autonomy_below": "L3", "field": "x_requested"}, {"x_requested": "L2"}) is True


def test_action_matching() -> None:
    assert action_matches("*", "tool.invoke")
    assert action_matches("execution.*", "execution.submit")
    assert not action_matches("execution.*", "experiment.execute")
    assert action_matches("tool.invoke", "tool.invoke")
    assert not action_matches("tool.invoke", "tool.invoked")


# ---------------------------------------------------------------------------------------------
# Combining
# ---------------------------------------------------------------------------------------------
def org_set(*raw: dict[str, Any], key: str = "org-policy", version: str = "3") -> PolicySet:
    return PolicySet(key=key, version=version, rules=tuple(parse_rules(raw)))


def test_deny_beats_require_approval_beats_allow() -> None:
    ctx = {"actor_kind": "user", "is_human": True, "gpu_count": 0}
    policies = org_set(
        rule(id="r-allow", effect="allow"),
        rule(id="r-approval", effect="require_approval"),
        rule(id="r-deny", effect="deny", reason="no"),
    )
    decision = evaluate("tool.invoke", ctx, BASELINE, [policies])
    assert decision.effect == "allow"  # none of the rules target tool.invoke
    decision = evaluate("execution.submit", ctx, BASELINE, [policies])
    assert decision.effect == "deny"
    assert decision.reasons == ("no",)
    assert set(decision.matched_rules) == {"r-allow", "r-approval", "r-deny"}
    assert decision.obligations == {}
    relaxed = org_set(rule(id="r-allow", effect="allow"), rule(id="r-approval", effect="require_approval"))
    decision = evaluate("execution.submit", ctx, BASELINE, [relaxed])
    assert decision.effect == "require_approval"
    assert decision.policy_versions == (f"baseline@{BASELINE_VERSION}", "org-policy@3")


def test_org_allow_cannot_override_baseline() -> None:
    ctx = {"actor_kind": "user", "is_human": True, "secrets_requested": ["DB_PASSWORD"]}
    allow_all = org_set(rule(id="allow.everything", action="*", effect="allow", priority=10_000))
    allow_exact = org_set(rule(id="allow.exec", action="execution.submit", effect="allow", priority=10_000), key="k2")
    decision = evaluate("execution.submit", ctx, BASELINE, [allow_all, allow_exact])
    assert decision.effect == "deny"
    assert "baseline.execution.submit.secrets_denied" in decision.matched_rules
    decision = evaluate("discovery.publish", {"is_human": True}, BASELINE, [allow_all])
    assert decision.effect == "require_approval"


def test_defaults_and_relaxing_them() -> None:
    for action in ACTIONS:
        expected = DEFAULT_EFFECT.get(action, "allow")
        # a context that matches no baseline rule for most actions
        decision = evaluate(action, {"actor_kind": "user", "is_human": True, "mcp_registered": True}, BASELINE, [])
        if not decision.matched_rules:
            assert decision.effect == expected
            assert decision.default_applied
    human = {"actor_kind": "user", "is_human": True, "source_trust": "trusted", "scope": "project"}
    assert evaluate("memory.promote", human).effect == "require_approval"
    wildcard = org_set(rule(id="allow.memory", action="memory.*", effect="allow"))
    decision = evaluate("memory.promote", human, BASELINE, [wildcard])
    assert decision.effect == "require_approval" and decision.default_applied
    exact = org_set(rule(id="allow.memory", action="memory.promote", effect="allow", obligations={"review": "async"}))
    decision = evaluate("memory.promote", human, BASELINE, [exact])
    assert decision.effect == "allow" and not decision.default_applied
    assert decision.obligations == {"review": "async"}


def test_approver_permission_and_obligations_merge() -> None:
    ctx = {"actor_kind": "user", "is_human": True, "gpu_count": 1, "autonomy_level": "L3"}
    policies = org_set(
        rule(id="low", effect="require_approval", approver_permission="execution:manage", priority=1),
        rule(id="high", effect="require_approval", approver_permission="approval:decide", priority=50),
        rule(id="ob1", effect="allow", obligations={"max_runtime_seconds": 60, "log": "full"}, priority=1),
        rule(id="ob2", effect="allow", obligations={"max_runtime_seconds": 30}, priority=9),
    )
    decision = evaluate("execution.submit", ctx, BASELINE, [policies])
    assert decision.effect == "require_approval"
    assert decision.approver_permission == "approval:decide"
    assert decision.obligations == {"max_runtime_seconds": 30, "log": "full"}
    assert decision.requires_approval and not decision.allowed and not decision.denied


def test_evaluation_is_deterministic_and_serializable() -> None:
    ctx = {"actor_kind": "agent", "autonomy_level": "L2", "gpu_count": 1, "network_mode": "allowlist"}
    first = evaluate("execution.submit", ctx)
    second = evaluate("execution.submit", dict(reversed(list(ctx.items()))))
    assert first == second
    data = first.to_dict()
    assert data["effect"] == "require_approval" and data["action"] == "execution.submit"
    assert isinstance(data["matched_rules"], list)


# ---------------------------------------------------------------------------------------------
# Baseline — one case per rule (and a guard that every baseline rule is covered)
# ---------------------------------------------------------------------------------------------
HUMAN = {"actor_kind": "user", "is_human": True, "autonomy_level": "L3_AUTOMATED_EXECUTION"}
AGENT_L2 = {"actor_kind": "agent", "is_human": False, "autonomy_level": "L2_AUTOMATED_EXPERIMENT_DESIGN"}
AGENT_L3 = {"actor_kind": "agent", "is_human": False, "autonomy_level": "L3_AUTOMATED_EXECUTION"}

BASELINE_CASES: list[tuple[str, str, dict[str, Any], str]] = []
for _action in ("execution.submit", "experiment.execute"):
    _p = f"baseline.{_action}"
    BASELINE_CASES += [
        (f"{_p}.network_requires_approval", _action, {**HUMAN, "network_mode": "allowlist"}, "require_approval"),
        (
            f"{_p}.egress_outside_allowlist",
            _action,
            {**HUMAN, "egress_hosts": ["evil.example"], "org_egress_allowlist": [".wikipedia.org"]},
            "deny",
        ),
        (f"{_p}.secrets_denied", _action, {**HUMAN, "secrets_requested": ["OPENAI_KEY"]}, "deny"),
        (f"{_p}.production_denied", _action, {**HUMAN, "production": True}, "deny"),
        (f"{_p}.gpu_requires_approval", _action, {**HUMAN, "autonomy_level": "L2", "gpu_count": 1}, "require_approval"),
        (
            f"{_p}.cost_over_budget",
            _action,
            {**HUMAN, "estimated_cost_usd": 12.5, "budget_remaining_usd": 10},
            "require_approval",
        ),
        (f"{_p}.automation_below_l3", _action, dict(AGENT_L2), "require_approval"),
    ]
BASELINE_CASES += [
    ("baseline.tool.invoke.high_risk", "tool.invoke", {**AGENT_L3, "tool_risk": "HIGH"}, "require_approval"),
    (
        "baseline.tool.invoke.agent_below_l1",
        "tool.invoke",
        {"actor_kind": "agent", "autonomy_level": "L0"},
        "require_approval",
    ),
    ("baseline.mcp.invoke.unregistered", "mcp.invoke", {**AGENT_L3, "tool_risk": "LOW"}, "deny"),
    (
        "baseline.mcp.invoke.high_risk",
        "mcp.invoke",
        {**AGENT_L3, "mcp_registered": True, "tool_risk": "CRITICAL"},
        "require_approval",
    ),
    (
        "baseline.research.deep_research.automation_below_l1",
        "research.deep_research",
        {"actor_kind": "workflow", "autonomy_level": "L0_ASSISTED"},
        "require_approval",
    ),
    (
        "baseline.research.deep_research.expensive",
        "research.deep_research",
        {**HUMAN, "estimated_cost_usd": 6},
        "require_approval",
    ),
    (
        "baseline.llm.external_processing.no_consent",
        "llm.external_processing",
        {**HUMAN, "external_provider": "gemini", "data_processing_consent": False},
        "deny",
    ),
    ("baseline.strategy.auto_promote.denied", "strategy.auto_promote", dict(HUMAN), "deny"),
    (
        "baseline.strategy.promote.non_human",
        "strategy.promote",
        {**AGENT_L3, "autonomy_level": "L4"},
        "require_approval",
    ),
    ("baseline.strategy.promote.automation_below_l4", "strategy.promote", dict(AGENT_L3), "deny"),
    ("baseline.discovery.approve.non_human", "discovery.approve", dict(AGENT_L3), "deny"),
    ("baseline.discovery.publish.approval", "discovery.publish", dict(HUMAN), "require_approval"),
    (
        "baseline.memory.promote.untrusted_shared",
        "memory.promote",
        {**AGENT_L3, "source_trust": "untrusted", "scope": "ORGANIZATION"},
        "require_approval",
    ),
    (
        "baseline.mission.autonomy_change.non_human",
        "mission.autonomy_change",
        {**AGENT_L3, "requested_rank": 1, "ceiling_rank": 3},
        "deny",
    ),
    (
        "baseline.mission.autonomy_change.above_ceiling",
        "mission.autonomy_change",
        {**HUMAN, "requested_rank": 4, "ceiling_rank": 3},
        "deny",
    ),
    ("baseline.integration.production.denied", "integration.production", dict(HUMAN), "deny"),
]


@pytest.mark.parametrize(("rule_id", "action", "context", "effect"), BASELINE_CASES, ids=[c[0] for c in BASELINE_CASES])
def test_baseline_rule(rule_id: str, action: str, context: dict[str, Any], effect: str) -> None:
    decision = evaluate(action, context)
    assert rule_id in decision.matched_rules, decision
    assert decision.effect == effect, decision


def test_every_baseline_rule_has_a_case() -> None:
    assert {r.id for r in BASELINE.rules} == {c[0] for c in BASELINE_CASES}
    assert all(r.id.startswith("baseline.") for r in BASELINE.rules)
    assert BASELINE.version == BASELINE_VERSION


@pytest.mark.parametrize(
    ("action", "context", "effect"),
    [
        # sandbox without network, secrets or GPU, human at L3 → allowed
        ("execution.submit", {**HUMAN, "network_mode": "none", "egress_hosts": [], "secrets_requested": []}, "allow"),
        # allowlisted egress still needs approval for network access but is not denied
        (
            "execution.submit",
            {
                **HUMAN,
                "network_mode": "allowlist",
                "egress_hosts": ["en.wikipedia.org"],
                "org_egress_allowlist": [".wikipedia.org"],
            },
            "require_approval",
        ),
        # egress hosts requested but no allowlist known → deny (fail-safe)
        ("execution.submit", {**HUMAN, "egress_hosts": ["a.org"]}, "deny"),
        # agent at L3 within budget executes automatically
        ("experiment.execute", {**AGENT_L3, "estimated_cost_usd": 1, "budget_remaining_usd": 5}, "allow"),
        ("tool.invoke", {**AGENT_L3, "tool_risk": "LOW"}, "allow"),
        ("mcp.invoke", {**AGENT_L3, "mcp_registered": True, "tool_risk": "LOW"}, "allow"),
        ("llm.external_processing", {**HUMAN, "external_provider": "gemini", "data_processing_consent": True}, "allow"),
        ("llm.external_processing", {**HUMAN, "external_provider": False}, "allow"),
        ("discovery.approve", dict(HUMAN), "require_approval"),  # human: default applies
        # the agent cannot spoof humanness by omitting keys
        ("discovery.approve", {"actor_kind": "agent"}, "deny"),
        ("mission.autonomy_change", {**HUMAN, "requested_rank": 2, "ceiling_rank": 3}, "allow"),
        ("mission.autonomy_change", {**HUMAN, "requested_rank": 2}, "deny"),  # unknown ceiling → deny
        ("strategy.promote", dict(HUMAN), "require_approval"),  # human promotion still needs the default gate
    ],
)
def test_baseline_fail_safe_combinations(action: str, context: dict[str, Any], effect: str) -> None:
    assert evaluate(action, context).effect == effect
