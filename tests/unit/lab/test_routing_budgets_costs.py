"""Unit tests: model router, budgets and cost estimation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from engines.lab.budgets import Budget, Spend, check_budget
from engines.lab.costs import compute_cost, storage_cost
from engines.lab.enums import ModelTier
from engines.lab.routing import Feature, ModelCandidate, ModelRouter, RoutingPolicy

SO = frozenset({Feature.STRUCTURED_OUTPUT, Feature.TOOLS})
CATALOG = [
    ModelCandidate("gemini", "fast-m", ModelTier.FAST, SO, 0.1, 0.4, 400),
    ModelCandidate("gemini", "default-m", ModelTier.DEFAULT, SO, 0.3, 2.5, 900),
    ModelCandidate("gemini", "reason-m", ModelTier.REASONING, SO, 1.25, 10.0, 4000),
    ModelCandidate(
        "gemini",
        "deep-agent",
        ModelTier.DEEP_RESEARCH,
        frozenset({Feature.BACKGROUND}),
        None,
        None,
        600_000,
        is_agent=True,
    ),
    ModelCandidate(
        "ollama",
        "local-m",
        ModelTier.FAST,
        frozenset({Feature.STRUCTURED_OUTPUT}),
        0.0,
        0.0,
        1500,
        data_leaves_organization=False,
    ),
]


def test_route_picks_tier_by_task() -> None:
    router = ModelRouter(CATALOG)
    assert router.route("classification").candidate.model in ("fast-m", "local-m")  # type: ignore[union-attr]
    assert router.route("hypothesis_generation").candidate.model == "reason-m"  # type: ignore[union-attr]
    assert router.route("research").candidate.model == "deep-agent"  # type: ignore[union-attr]


def test_complexity_bumps_tier() -> None:
    router = ModelRouter(CATALOG)
    low = router.route("coding", complexity=0.1)
    high = router.route("coding", complexity=0.9)
    assert low.tier == ModelTier.FAST and high.tier == ModelTier.REASONING


def test_no_external_without_consent() -> None:
    router = ModelRouter(CATALOG)
    d = router.route("classification", policy=RoutingPolicy(allow_external=False))
    assert d.candidate is not None and d.candidate.provider == "ollama"
    d2 = router.route("hypothesis_generation", policy=RoutingPolicy(allow_external=False))
    # Only the local fast model remains; it is used below the target tier rather than failing.
    assert d2.candidate is not None and d2.candidate.model == "local-m"


def test_cost_and_latency_budgets() -> None:
    router = ModelRouter(CATALOG)
    d = router.route("hypothesis_generation", cost_budget_usd=0.001)
    assert d.candidate is not None and d.candidate.model in ("fast-m", "local-m")
    assert any("exceeds budget" in why for _, why in d.rejected)
    d = router.route("coding", latency_budget_ms=500)
    assert (
        d.candidate is not None and d.candidate.typical_latency_ms is not None and d.candidate.typical_latency_ms <= 500
    )
    none = router.route("research", policy=RoutingPolicy(allowed_providers=frozenset({"ollama"})))
    assert not none.ok


def test_pinning_and_estimates() -> None:
    router = ModelRouter(CATALOG)
    d = router.route("coding", policy=RoutingPolicy(pinned={"coding": "gemini:fast-m"}))
    assert d.candidate is not None and d.candidate.key == "gemini:fast-m" and "[pinned]" in d.reason
    assert d.estimated_cost_usd is not None and d.estimated_cost_usd > 0


def test_budget_check_limits_warnings_deadline() -> None:
    budget = Budget(
        max_total_cost=10, max_llm_tokens=1000, max_experiment_count=2, deadline=datetime(2026, 1, 1, tzinfo=UTC)
    )
    ok = check_budget(
        budget, Spend(total_cost=5, llm_tokens=100), Spend(total_cost=1), now=datetime(2025, 12, 1, tzinfo=UTC)
    )
    assert ok.allowed and ok.remaining["total_cost"] == 5
    warn = check_budget(budget, Spend(total_cost=8.5), now=datetime(2025, 12, 1, tzinfo=UTC))
    assert warn.allowed and warn.warnings
    over = check_budget(budget, Spend(llm_tokens=900), Spend(llm_tokens=200))
    assert not over.allowed and "llm_tokens" in over.exceeded
    late = check_budget(budget, Spend(), now=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=1))
    assert not late.allowed and "deadline" in late.exceeded


def test_costs_only_when_priced() -> None:
    assert compute_cost({}, seconds=3600, cpu=2, memory_mb=2048).usd is None
    priced = compute_cost({"cpu_core_hour": 0.05, "memory_gb_hour": 0.01}, seconds=3600, cpu=2, memory_mb=2048)
    assert priced.usd == 0.12
    assert (
        compute_cost(
            {"cpu_core_hour": 0.05, "gpu_hour": {}}, seconds=60, cpu=1, memory_mb=512, gpu_type="a100", gpu_count=1
        ).usd
        is None
    )
    assert storage_cost({}, size_bytes=10).usd is None
    assert storage_cost({"storage_gb_month": 0.02}, size_bytes=1024**3).usd == 0.02
