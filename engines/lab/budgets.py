"""Budget accounting (pure). Missions/projects carry budgets; spend is aggregated from usage records.

Monetary limits are only enforceable when prices are configured. Token and compute-second limits are always
enforceable, so they act as a hard backstop when no pricing is configured (cost is never invented).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

WARNING_THRESHOLD = 0.8


@dataclass(frozen=True)
class Budget:
    max_total_cost: float | None = None
    max_llm_cost: float | None = None
    max_compute_cost: float | None = None
    max_experiment_count: int | None = None
    max_llm_tokens: int | None = None
    max_compute_seconds: float | None = None
    deadline: datetime | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None, deadline: datetime | None = None) -> Budget:
        data = dict(data or {})
        return cls(
            max_total_cost=_opt_float(data.get("max_total_cost")),
            max_llm_cost=_opt_float(data.get("max_llm_cost")),
            max_compute_cost=_opt_float(data.get("max_compute_cost")),
            max_experiment_count=_opt_int(data.get("max_experiment_count")),
            max_llm_tokens=_opt_int(data.get("max_llm_tokens")),
            max_compute_seconds=_opt_float(data.get("max_compute_seconds")),
            deadline=deadline,
        )


@dataclass(frozen=True)
class Spend:
    total_cost: float = 0.0
    llm_cost: float = 0.0
    compute_cost: float = 0.0
    experiment_count: int = 0
    llm_tokens: int = 0
    compute_seconds: float = 0.0

    def plus(self, other: Spend) -> Spend:
        return Spend(
            total_cost=self.total_cost + other.total_cost,
            llm_cost=self.llm_cost + other.llm_cost,
            compute_cost=self.compute_cost + other.compute_cost,
            experiment_count=self.experiment_count + other.experiment_count,
            llm_tokens=self.llm_tokens + other.llm_tokens,
            compute_seconds=self.compute_seconds + other.compute_seconds,
        )


@dataclass
class BudgetCheck:
    allowed: bool
    exceeded: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    remaining: dict[str, float | None] = field(default_factory=dict)
    utilisation: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_DIMENSIONS: tuple[tuple[str, str], ...] = (
    ("max_total_cost", "total_cost"),
    ("max_llm_cost", "llm_cost"),
    ("max_compute_cost", "compute_cost"),
    ("max_experiment_count", "experiment_count"),
    ("max_llm_tokens", "llm_tokens"),
    ("max_compute_seconds", "compute_seconds"),
)


def check_budget(
    budget: Budget, spent: Spend, estimate: Spend | None = None, *, now: datetime | None = None
) -> BudgetCheck:
    """Would ``spent + estimate`` stay within ``budget``? Reports remaining headroom and 80% warnings."""
    projected = spent.plus(estimate or Spend())
    result = BudgetCheck(allowed=True)
    for limit_name, spend_name in _DIMENSIONS:
        limit = getattr(budget, limit_name)
        used = float(getattr(projected, spend_name))
        if limit is None:
            result.remaining[spend_name] = None
            continue
        limit_f = float(limit)
        result.remaining[spend_name] = round(limit_f - float(getattr(spent, spend_name)), 6)
        ratio = used / limit_f if limit_f > 0 else (float("inf") if used > 0 else 0.0)
        result.utilisation[spend_name] = round(ratio, 4) if ratio != float("inf") else 1e9
        if used > limit_f + 1e-12:
            result.allowed = False
            result.exceeded.append(spend_name)
        elif ratio >= WARNING_THRESHOLD:
            result.warnings.append(f"{spend_name} at {ratio:.0%} of budget")
    if budget.deadline is not None and now is not None and now >= budget.deadline:
        result.allowed = False
        result.exceeded.append("deadline")
    return result


def _opt_float(value: Any) -> float | None:
    return None if value is None or value == "" else float(value)


def _opt_int(value: Any) -> int | None:
    return None if value is None or value == "" else int(value)
