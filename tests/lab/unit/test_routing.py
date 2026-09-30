"""Model routing: tier map, complexity/latency adjustments, budgets, capabilities, policy, fallbacks."""

from __future__ import annotations

from decimal import Decimal

import pytest

from engines.lab.llm_costs import Price
from engines.lab.routing import (
    TASK_TIERS,
    ModelCandidate,
    RoutingPolicy,
    RoutingRequest,
    Tier,
    base_tier,
    route,
    target_tier,
)

ALL_CAPS = frozenset({"structured_output", "tools", "search", "code_execution", "streaming"})
CHEAP = Price(Decimal("0.10"), Decimal("0.40"))
PRICEY = Price(Decimal("2.00"), Decimal("12.00"))


def gemini(tier: str, model: str, price: Price | None = None, priority: int = 100) -> ModelCandidate:
    return ModelCandidate("gemini", model, tier, priority=priority, capabilities=ALL_CAPS, price=price)


def local(model: str = "qwen", tier: str = "fast") -> ModelCandidate:
    return ModelCandidate(
        "ollama", model, tier, priority=200, local=True, capabilities=frozenset({"structured_output", "streaming"})
    )


CANDIDATES = [
    gemini("fast", "g-flash-lite", CHEAP),
    gemini("default", "g-flash", Price(Decimal("0.5"), Decimal("2"))),
    gemini("reasoning", "g-pro", PRICEY),
    local(),
]
ALLOW = RoutingPolicy(allow_external=True)


def test_task_tier_map_covers_all_task_types():
    assert len(TASK_TIERS) == 15
    assert {t for t, tier in TASK_TIERS.items() if tier is Tier.FAST} == {
        "classification",
        "extraction",
        "summarization",
    }
    assert {t for t, tier in TASK_TIERS.items() if tier is Tier.DEFAULT} == {"coding", "report_generation"}
    assert base_tier("planning") is Tier.REASONING
    assert base_tier("unknown-task") is Tier.DEFAULT


@pytest.mark.parametrize(
    ("task", "complexity", "latency", "expected"),
    [
        ("classification", "medium", None, Tier.FAST),
        ("summarization", "high", None, Tier.DEFAULT),
        ("coding", "high", None, Tier.REASONING),
        ("coding", "low", None, Tier.FAST),
        ("planning", "high", None, Tier.REASONING),
        ("planning", "low", None, Tier.DEFAULT),
        ("planning", "medium", 1500, Tier.FAST),
        ("planning", "medium", 3000, Tier.REASONING),
    ],
)
def test_target_tier(task, complexity, latency, expected):
    tier, reasons = target_tier(task, complexity, latency)
    assert tier is expected
    assert reasons[0].startswith(task)


def test_explicit_tier_wins():
    tier, reasons = target_tier("planning", "high", 100, explicit="fast")
    assert tier is Tier.FAST
    assert reasons == ["explicit tier fast"]


def test_routes_to_tier_match_with_fallbacks_in_deterministic_order():
    decision = route(RoutingRequest(task_type="planning"), CANDIDATES, ALLOW)
    assert decision.tier == "reasoning"
    assert decision.primary is not None and decision.primary.candidate.model == "g-pro"
    assert [r.candidate.model for r in decision.fallbacks] == ["g-flash", "g-flash-lite", "qwen"]
    assert "selected gemini:g-pro" in decision.reason
    again = route(RoutingRequest(task_type="planning"), list(reversed(CANDIDATES)), ALLOW)
    assert [r.candidate.key for r in again.ranked] == [r.candidate.key for r in decision.ranked]


def test_default_tier_tie_prefers_stronger_model_without_latency_budget():
    candidates = [gemini("fast", "f", CHEAP), gemini("reasoning", "r", PRICEY)]
    decision = route(RoutingRequest(task_type="coding"), candidates, ALLOW)
    assert decision.primary is not None and decision.primary.candidate.model == "r"


def test_complexity_bump_changes_selection():
    low = route(RoutingRequest(task_type="summarization", complexity="medium"), CANDIDATES, ALLOW)
    high = route(RoutingRequest(task_type="summarization", complexity="high"), CANDIDATES, ALLOW)
    assert low.primary is not None and low.primary.candidate.model == "g-flash-lite"
    assert high.primary is not None and high.primary.candidate.model == "g-flash"


def test_latency_budget_prefers_fast():
    decision = route(RoutingRequest(task_type="verification", latency_budget_ms=800), CANDIDATES, ALLOW)
    assert decision.primary is not None and decision.primary.candidate.tier == "fast"
    assert "latency budget" in decision.reason


def test_cost_budget_filters_expensive_and_unpriced_candidates():
    request = RoutingRequest(
        task_type="planning",
        cost_budget_usd=Decimal("0.01"),
        estimated_input_tokens=2000,
        estimated_output_tokens=1000,
    )
    decision = route(request, CANDIDATES, ALLOW)
    keys = [r.candidate.model for r in decision.ranked]
    assert "g-pro" not in keys  # 2000*2e-6 + 1000*12e-6 = $0.016 > $0.01
    assert keys[0] == "g-flash"
    reasons = {r.key: r.reason for r in decision.rejected}
    assert "exceeds cost budget" in reasons["gemini:g-pro"]
    unpriced = gemini("reasoning", "g-unpriced", None)
    decision = route(request, [unpriced], ALLOW)
    assert decision.primary is None
    assert "no price configured" in decision.rejected[0].reason
    # Without a cost budget an unknown price is acceptable.
    assert route(RoutingRequest(task_type="planning"), [unpriced], ALLOW).primary is not None


def test_local_models_cost_nothing_and_pass_cost_budget():
    decision = route(RoutingRequest(task_type="classification", cost_budget_usd=Decimal("0")), [local()], ALLOW)
    assert decision.primary is not None and decision.primary.estimated_cost_usd == Decimal("0")


def test_capability_filter():
    request = RoutingRequest(task_type="classification", required_capabilities=frozenset({"tools", "search"}))
    decision = route(request, CANDIDATES, ALLOW)
    assert all(r.candidate.provider == "gemini" for r in decision.ranked)
    assert any("missing capability search, tools" in r.reason for r in decision.rejected)


def test_external_disallowed_routes_only_to_local_models():
    decision = route(RoutingRequest(task_type="planning"), CANDIDATES, RoutingPolicy(allow_external=False))
    assert [r.candidate.provider for r in decision.ranked] == ["ollama"]
    assert decision.primary is not None and decision.primary.tier_distance == 2
    assert sum("external processing not allowed" in r.reason for r in decision.rejected) == 3
    none = route(RoutingRequest(task_type="planning"), CANDIDATES[:3], RoutingPolicy(allow_external=False))
    assert none.primary is None and "no eligible model" in none.reason


def test_policy_allow_and_deny_lists():
    allow_only_local = RoutingPolicy(allow_external=True, allowed_providers=frozenset({"ollama"}))
    assert [
        r.candidate.provider for r in route(RoutingRequest(task_type="coding"), CANDIDATES, allow_only_local).ranked
    ] == ["ollama"]
    deny = RoutingPolicy(allow_external=True, denied_providers=frozenset({"gemini"}))
    assert [r.candidate.provider for r in route(RoutingRequest(task_type="coding"), CANDIDATES, deny).ranked] == [
        "ollama"
    ]


def test_pins_and_task_type_restrictions():
    pinned = route(RoutingRequest(task_type="planning", model="g-flash"), CANDIDATES, ALLOW)
    assert [r.candidate.model for r in pinned.ranked] == ["g-flash"]
    by_provider = route(RoutingRequest(task_type="planning", provider="ollama"), CANDIDATES, ALLOW)
    assert [r.candidate.provider for r in by_provider.ranked] == ["ollama"]
    restricted = ModelCandidate(
        "gemini", "g-coder", "reasoning", priority=1, capabilities=ALL_CAPS, task_types=frozenset({"coding"})
    )
    decision = route(RoutingRequest(task_type="planning"), [*CANDIDATES, restricted], ALLOW)
    assert "g-coder" not in [r.candidate.model for r in decision.ranked]
    coding = route(RoutingRequest(task_type="coding", complexity="high"), [*CANDIDATES, restricted], ALLOW)
    assert coding.primary is not None and coding.primary.candidate.model == "g-coder"


def test_priority_and_source_precedence():
    settings_row = gemini("reasoning", "g-pro", PRICEY, priority=100)
    org_row = ModelCandidate("gemini", "g-pro", "reasoning", priority=100, capabilities=frozenset(), source="org")
    # Same provider/model/tier from two sources: the org row wins (and here drops the tools capability).
    decision = route(
        RoutingRequest(task_type="planning", required_capabilities=frozenset({"tools"})), [settings_row, org_row], ALLOW
    )
    assert decision.primary is None
    better = ModelCandidate("gemini", "g-pro-2", "reasoning", priority=10, capabilities=ALL_CAPS, source="org")
    decision = route(RoutingRequest(task_type="planning"), [settings_row, better], ALLOW)
    assert decision.primary is not None and decision.primary.candidate.model == "g-pro-2"


def test_reason_is_bounded():
    many = [gemini("fast", f"m{i}" * 20, CHEAP) for i in range(50)]
    decision = route(RoutingRequest(task_type="planning", provider="none"), many, ALLOW)
    assert len(decision.reason) <= 300


def test_app_model_router_builds_candidates_from_profiles_and_configs():
    from aegis_api.lab.llm.base import GEMINI_CAPABILITIES, OLLAMA_CAPABILITIES
    from aegis_api.lab.llm.costs import ModelConfigView
    from aegis_api.lab.llm.routing import ModelRouter, ProviderProfile

    profiles = [
        ProviderProfile("gemini", GEMINI_CAPABILITIES, {"fast": "gf", "default": "gd", "reasoning": "gr"}, 100),
        ProviderProfile("ollama", OLLAMA_CAPABILITIES, {"fast": "qwen"}, 200),
    ]
    configs = [
        ModelConfigView(
            id="c1",
            organization_id=None,
            provider_kind="gemini",
            model="g-special",
            tier="reasoning",
            priority=50,
            input_per_mtok_usd=Decimal("1"),
            output_per_mtok_usd=Decimal("4"),
            capabilities={"tools": False},
        ),
        ModelConfigView(id="c2", organization_id=None, provider_kind="anthropic", model="claude-x", tier="default"),
        ModelConfigView(id="c3", organization_id=None, provider_kind="gemini", model="off", tier="fast", enabled=False),
    ]
    router = ModelRouter(profiles, configs, pricing={"gemini:gr": {"input_per_mtok": 2, "output_per_mtok": 12}})
    assert {c.model for c in router.candidates} == {"gf", "gd", "gr", "qwen", "g-special"}
    assert router.unconfigured[0].candidate == "anthropic:claude-x"
    decision = router.route("planning", policy=RoutingPolicy(allow_external=True))
    assert decision.model == "g-special" and decision.primary is not None
    assert decision.primary.source == "platform" and decision.primary.estimated_cost_usd is not None
    with_tools = router.route("planning", policy=RoutingPolicy(allow_external=True), required_capabilities=["tools"])
    assert with_tools.model == "gr"
    no_consent = router.route("planning", policy=RoutingPolicy(allow_external=False))
    assert no_consent.provider == "ollama" and not no_consent.external_allowed
    pinned = router.route("planning", policy=RoutingPolicy(allow_external=True), provider="gemini", model="g-new")
    assert pinned.model == "g-new" and pinned.primary is not None and pinned.primary.source == "request"
