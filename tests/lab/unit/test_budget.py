"""Budget math: parsing, remaining/utilization, thresholds, estimates, counts and time budgets."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from engines.lab.budget import (
    DIMENSIONS,
    BudgetLimits,
    BudgetSpend,
    BudgetState,
    normalize_kind,
    to_money,
)

D = Decimal
NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


def state(spend: BudgetSpend | None = None, **budget: object) -> BudgetState:
    return BudgetState(BudgetLimits.from_budget(budget), spend or BudgetSpend())


def test_parsing_and_validation() -> None:
    limits = BudgetLimits.from_budget(
        {
            "max_total_cost_usd": "100",
            "max_llm_cost_usd": 40.5,
            "max_experiment_count": 3,
            "max_research_tasks": 2.0,
            "unrelated": "ignored",
        }
    )
    assert limits.total_usd == D("100") and limits.llm_usd == D("40.5")
    assert limits.experiments == 3 and limits.research_tasks == 2
    assert limits.compute_usd is None and not limits.is_unlimited
    assert BudgetLimits.from_budget({}).is_unlimited
    for bad in (
        {"max_total_cost_usd": -1},
        {"max_llm_cost_usd": "abc"},
        {"max_experiment_count": 1.5},
        {"max_total_cost_usd": True},
    ):
        with pytest.raises(ValueError, match="budget\\."):
            BudgetLimits.from_budget(bad)
    with pytest.raises(ValueError):
        to_money(float("inf"))
    assert normalize_kind("experiment") == "experiments" and normalize_kind("research") == "research_tasks"
    with pytest.raises(ValueError, match="Unknown budget kind"):
        normalize_kind("gpu")


def test_remaining_utilization_and_status_thresholds() -> None:
    s = state(BudgetSpend(llm_usd=D("7.9")), max_llm_cost_usd=10)
    assert s.remaining("llm") == D("2.1")
    assert s.utilization("llm") == pytest.approx(0.79)
    assert s.status("llm") == "ok"
    s = state(BudgetSpend(llm_usd=D("8")), max_llm_cost_usd=10)
    assert s.status("llm") == "warning" and s.warnings() == ["llm"]
    s = state(BudgetSpend(llm_usd=D("10")), max_llm_cost_usd=10)
    assert s.status("llm") == "exceeded" and s.exceeded() == ["llm"] and s.remaining("llm") == 0
    s = state(BudgetSpend(llm_usd=D("12")), max_llm_cost_usd=10)
    assert s.remaining("llm") == 0  # never negative
    assert s.status("compute") == "unlimited" and s.remaining("compute") is None and s.utilization("compute") is None
    assert [d.dimension for d in s.dimensions()] == list(DIMENSIONS)


def test_money_checks_include_total() -> None:
    s = state(BudgetSpend(llm_usd=D("4"), compute_usd=D("5")), max_total_cost_usd=10, max_llm_cost_usd=8)
    assert s.check("llm").ok
    assert s.check("llm", estimated_usd=1).ok  # exactly fits the total (4+5+1 == 10)
    verdict = s.check("llm", estimated_usd=D("1.01"))
    assert not verdict.ok and verdict.dimension == "total" and "would be exceeded" in (verdict.reason or "")
    assert verdict.remaining_usd == D("1")
    verdict = s.check("compute", estimated_usd=2)
    assert not verdict.ok and verdict.dimension == "total"
    assert s.would_exceed("tool", 2) and not s.would_exceed("tool", 1)
    exhausted = state(BudgetSpend(llm_usd=D("8")), max_llm_cost_usd=8)
    verdict = exhausted.check("llm")
    assert not verdict.ok and verdict.dimension == "llm" and "exhausted" in (verdict.reason or "")
    assert exhausted.check("compute").ok  # other dimensions are unaffected


def test_zero_limit_blocks_everything() -> None:
    s = state(max_compute_cost_usd=0)
    assert not s.check("compute").ok
    assert s.utilization("compute") == 1.0 and s.status("compute") == "exceeded"


def test_count_budgets() -> None:
    s = state(
        BudgetSpend(experiments=2, research_tasks=1), max_experiment_count=3, max_research_tasks=1, max_total_cost_usd=5
    )
    assert s.check("experiment").ok
    assert s.check("experiment", count=1).ok
    assert not s.check("experiment", count=2).ok
    assert not s.check("research").ok  # exhausted
    assert s.would_exceed("experiment", 2) and not s.would_exceed("experiment", 1)
    verdict = s.check("experiment", count=1, estimated_usd=6)
    assert not verdict.ok and verdict.dimension == "total"


def test_tightest_limits() -> None:
    mission = BudgetLimits.from_budget({"max_total_cost_usd": 100, "max_llm_cost_usd": 50})
    project = BudgetLimits.from_budget({"max_total_cost_usd": 60, "max_compute_cost_usd": 20})
    combined = mission.tightest(project)
    assert combined.total_usd == D("60") and combined.llm_usd == D("50") and combined.compute_usd == D("20")
    deadline = NOW + timedelta(days=1)
    assert BudgetLimits(deadline=deadline).tightest(BudgetLimits(deadline=NOW)).deadline == NOW


def test_time_budget_and_deadline() -> None:
    limits = BudgetLimits(time_budget_seconds=3600, deadline=NOW + timedelta(hours=2))
    s = BudgetState(limits, BudgetSpend(), started_at=NOW - timedelta(minutes=30))
    assert not s.time_exceeded(NOW) and not s.deadline_passed(NOW)
    assert s.time_remaining_seconds(NOW) == pytest.approx(1800)
    assert s.check("llm", now=NOW).ok
    later = NOW + timedelta(minutes=31)
    assert s.time_exceeded(later)
    verdict = s.check("llm", now=later)
    assert not verdict.ok and verdict.dimension == "time"
    past_deadline = NOW + timedelta(hours=3)
    verdict = BudgetState(BudgetLimits(deadline=NOW), BudgetSpend()).check("compute", now=past_deadline)
    assert not verdict.ok and verdict.dimension == "deadline"
    assert BudgetState(limits, BudgetSpend()).elapsed_seconds(NOW) is None  # not started yet
    data = s.to_dict(NOW)
    assert data["time_exceeded"] is False and data["elapsed_seconds"] == pytest.approx(1800)
    assert len(data["dimensions"]) == len(DIMENSIONS)


def test_warnings_reported_on_ok_checks() -> None:
    s = state(BudgetSpend(tool_usd=D("9")), max_tool_cost_usd=10, max_total_cost_usd=100)
    verdict = s.check("tool")
    assert verdict.ok and verdict.warnings == ("tool",) and verdict.utilization == pytest.approx(0.9)
