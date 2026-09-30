"""Mission budget math: limits, spend, remaining, utilization, thresholds and time budgets (pure).

Dimensions: ``llm``, ``compute``, ``tool`` and ``total`` (USD, :class:`~decimal.Decimal`) and
``experiments`` / ``research_tasks`` (counts). A spend kind is checked against its own dimension and,
for money, against ``total``. ``None`` limits are unlimited; a limit of ``0`` allows nothing.

Thresholds: ≥ 80 % utilization is a *warning*; ≥ 100 % *stops* the dimension. A check with an estimate
fails when ``spent + estimate > limit``; a check without an estimate fails once ``spent >= limit``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

WARN_THRESHOLD = Decimal("0.8")
STOP_THRESHOLD = Decimal("1")

MONEY_DIMENSIONS: tuple[str, ...] = ("llm", "compute", "tool", "total")
COUNT_DIMENSIONS: tuple[str, ...] = ("experiments", "research_tasks")
DIMENSIONS: tuple[str, ...] = (*MONEY_DIMENSIONS, *COUNT_DIMENSIONS)

# Accepted spend kinds (contract names first) → canonical dimension.
KIND_ALIASES: dict[str, str] = {
    "llm": "llm",
    "compute": "compute",
    "tool": "tool",
    "total": "total",
    "experiment": "experiments",
    "experiments": "experiments",
    "research": "research_tasks",
    "research_task": "research_tasks",
    "research_tasks": "research_tasks",
}

# Mission/project ``budget`` JSON keys → dimension.
BUDGET_KEYS: dict[str, str] = {
    "max_total_cost_usd": "total",
    "max_llm_cost_usd": "llm",
    "max_compute_cost_usd": "compute",
    "max_tool_cost_usd": "tool",
    "max_experiment_count": "experiments",
    "max_research_tasks": "research_tasks",
}

DimensionStatus = Literal["unlimited", "ok", "warning", "exceeded"]


def normalize_kind(kind: str) -> str:
    try:
        return KIND_ALIASES[kind]
    except KeyError as exc:
        raise ValueError(f"Unknown budget kind {kind!r}; expected one of {', '.join(KIND_ALIASES)}") from exc


def to_money(value: Any) -> Decimal:
    """Parse a non-negative money amount; raises ``ValueError`` for anything else."""
    if isinstance(value, bool) or value is None:
        raise ValueError(f"Invalid amount: {value!r}")
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid amount: {value!r}") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"Amounts must be finite and non-negative: {value!r}")
    return amount


def _to_count(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"Invalid count: {value!r}")
    if isinstance(value, float) and not value.is_integer():
        raise ValueError(f"Counts must be whole numbers: {value!r}")
    try:
        count = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid count: {value!r}") from exc
    if count < 0:
        raise ValueError(f"Counts must be non-negative: {value!r}")
    return count


def _min_opt[T: (Decimal, int)](a: T | None, b: T | None) -> T | None:
    if a is None:
        return b
    if b is None:
        return a
    return a if a <= b else b


@dataclass(frozen=True)
class BudgetLimits:
    total_usd: Decimal | None = None
    llm_usd: Decimal | None = None
    compute_usd: Decimal | None = None
    tool_usd: Decimal | None = None
    experiments: int | None = None
    research_tasks: int | None = None
    time_budget_seconds: int | None = None
    deadline: datetime | None = None

    @classmethod
    def from_budget(
        cls,
        budget: Mapping[str, Any] | None,
        *,
        time_budget_seconds: int | None = None,
        deadline: datetime | None = None,
    ) -> BudgetLimits:
        """Parse a mission/project ``budget`` mapping. Unknown keys are ignored; ``None`` = unlimited."""
        values: dict[str, Any] = {}
        for key, dimension in BUDGET_KEYS.items():
            raw = (budget or {}).get(key)
            if raw is None:
                continue
            try:
                values[dimension] = _to_count(raw) if dimension in COUNT_DIMENSIONS else to_money(raw)
            except ValueError as exc:
                raise ValueError(f"budget.{key}: {exc}") from exc
        if time_budget_seconds is not None and (isinstance(time_budget_seconds, bool) or time_budget_seconds < 0):
            raise ValueError("time_budget_seconds must be non-negative")
        return cls(
            total_usd=values.get("total"),
            llm_usd=values.get("llm"),
            compute_usd=values.get("compute"),
            tool_usd=values.get("tool"),
            experiments=values.get("experiments"),
            research_tasks=values.get("research_tasks"),
            time_budget_seconds=time_budget_seconds,
            deadline=deadline,
        )

    def limit(self, dimension: str) -> Decimal | int | None:
        return {
            "total": self.total_usd,
            "llm": self.llm_usd,
            "compute": self.compute_usd,
            "tool": self.tool_usd,
            "experiments": self.experiments,
            "research_tasks": self.research_tasks,
        }[dimension]

    def tightest(self, other: BudgetLimits) -> BudgetLimits:
        """Per-dimension minimum of two limit sets (e.g. mission limits under a project ceiling)."""
        deadline = self.deadline
        if other.deadline is not None and (deadline is None or other.deadline < deadline):
            deadline = other.deadline
        return BudgetLimits(
            total_usd=_min_opt(self.total_usd, other.total_usd),
            llm_usd=_min_opt(self.llm_usd, other.llm_usd),
            compute_usd=_min_opt(self.compute_usd, other.compute_usd),
            tool_usd=_min_opt(self.tool_usd, other.tool_usd),
            experiments=_min_opt(self.experiments, other.experiments),
            research_tasks=_min_opt(self.research_tasks, other.research_tasks),
            time_budget_seconds=_min_opt(self.time_budget_seconds, other.time_budget_seconds),
            deadline=deadline,
        )

    @property
    def is_unlimited(self) -> bool:
        return all(self.limit(d) is None for d in DIMENSIONS) and self.time_budget_seconds is None and not self.deadline


@dataclass(frozen=True)
class BudgetSpend:
    llm_usd: Decimal = Decimal("0")
    compute_usd: Decimal = Decimal("0")
    tool_usd: Decimal = Decimal("0")
    experiments: int = 0
    research_tasks: int = 0

    @property
    def total_usd(self) -> Decimal:
        return self.llm_usd + self.compute_usd + self.tool_usd

    def spent(self, dimension: str) -> Decimal | int:
        if dimension == "experiments":
            return self.experiments
        if dimension == "research_tasks":
            return self.research_tasks
        money: dict[str, Decimal] = {
            "total": self.total_usd,
            "llm": self.llm_usd,
            "compute": self.compute_usd,
            "tool": self.tool_usd,
        }
        return money[dimension]


@dataclass(frozen=True)
class DimensionState:
    dimension: str
    limit: Decimal | int | None
    spent: Decimal | int
    remaining: Decimal | int | None
    utilization: float | None
    status: DimensionStatus


@dataclass(frozen=True)
class BudgetVerdict:
    ok: bool
    kind: str
    reason: str | None = None
    dimension: str | None = None
    remaining_usd: Decimal | None = None
    utilization: float | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)


def _utilization(spent: Decimal | int, limit: Decimal | int | None) -> Decimal | None:
    if limit is None:
        return None
    if limit == 0:
        return STOP_THRESHOLD
    return Decimal(spent) / Decimal(limit)


def _status(spent: Decimal | int, limit: Decimal | int | None) -> DimensionStatus:
    util = _utilization(spent, limit)
    if util is None:
        return "unlimited"
    if util >= STOP_THRESHOLD:
        return "exceeded"
    if util >= WARN_THRESHOLD:
        return "warning"
    return "ok"


@dataclass(frozen=True)
class BudgetState:
    limits: BudgetLimits
    spend: BudgetSpend
    started_at: datetime | None = None

    # -- per-dimension views ----------------------------------------------------------------------
    def remaining(self, dimension: str) -> Decimal | int | None:
        limit = self.limits.limit(dimension)
        if limit is None:
            return None
        spent = self.spend.spent(dimension)
        if isinstance(limit, Decimal) or isinstance(spent, Decimal):
            left = Decimal(limit) - Decimal(spent)
            return left if left > 0 else Decimal("0")
        return max(limit - spent, 0)

    def utilization(self, dimension: str) -> float | None:
        util = _utilization(self.spend.spent(dimension), self.limits.limit(dimension))
        return None if util is None else float(util)

    def status(self, dimension: str) -> DimensionStatus:
        return _status(self.spend.spent(dimension), self.limits.limit(dimension))

    def dimensions(self) -> list[DimensionState]:
        return [
            DimensionState(
                dimension=d,
                limit=self.limits.limit(d),
                spent=self.spend.spent(d),
                remaining=self.remaining(d),
                utilization=self.utilization(d),
                status=self.status(d),
            )
            for d in DIMENSIONS
        ]

    def warnings(self) -> list[str]:
        """Dimensions at or above the warning threshold (80 %) but not yet stopped."""
        return [d for d in DIMENSIONS if self.status(d) == "warning"]

    def exceeded(self) -> list[str]:
        return [d for d in DIMENSIONS if self.status(d) == "exceeded"]

    # -- time ---------------------------------------------------------------------------------------
    def elapsed_seconds(self, now: datetime) -> float | None:
        if self.started_at is None:
            return None
        return max((now - self.started_at).total_seconds(), 0.0)

    def time_exceeded(self, now: datetime) -> bool:
        budget = self.limits.time_budget_seconds
        elapsed = self.elapsed_seconds(now)
        return budget is not None and elapsed is not None and elapsed >= budget

    def deadline_passed(self, now: datetime) -> bool:
        return self.limits.deadline is not None and now >= self.limits.deadline

    def time_remaining_seconds(self, now: datetime) -> float | None:
        candidates: list[float] = []
        if self.limits.deadline is not None:
            candidates.append((self.limits.deadline - now).total_seconds())
        if self.limits.time_budget_seconds is not None and self.started_at is not None:
            end = self.started_at + timedelta(seconds=self.limits.time_budget_seconds)
            candidates.append((end - now).total_seconds())
        return max(min(candidates), 0.0) if candidates else None

    # -- checks ---------------------------------------------------------------------------------------
    def _dimensions_for(self, dimension: str, estimated_usd: Decimal) -> tuple[str, ...]:
        if dimension == "total":
            return ("total",)
        if dimension in COUNT_DIMENSIONS:
            return (dimension, "total") if estimated_usd > 0 else (dimension,)
        return (dimension, "total")

    def would_exceed(self, kind: str, estimate: Decimal | int | float = 0) -> bool:
        """True when charging ``estimate`` (USD for money kinds, a count for count kinds) to ``kind`` would
        exceed a limit (or the dimension is already exhausted when ``estimate`` is 0)."""
        dimension = normalize_kind(kind)
        if dimension in COUNT_DIMENSIONS:
            return not self.check(kind, count=_to_count(estimate)).ok
        return not self.check(kind, estimated_usd=estimate).ok

    def check(
        self,
        kind: str,
        *,
        estimated_usd: Decimal | int | float = 0,
        count: int = 0,
        now: datetime | None = None,
    ) -> BudgetVerdict:
        dimension = normalize_kind(kind)
        estimate = to_money(estimated_usd)
        extra_count = _to_count(count)
        dims = self._dimensions_for(dimension, estimate)
        money_dims = [d for d in dims if d in MONEY_DIMENSIONS and self.limits.limit(d) is not None]
        remaining_values = [self.remaining(d) for d in money_dims]
        remaining_usd = min((Decimal(r) for r in remaining_values if r is not None), default=None)
        utils = [u for u in (self.utilization(d) for d in dims) if u is not None]
        utilization = max(utils) if utils else None
        warnings = tuple(d for d in dims if self.status(d) == "warning")

        def fail(dim: str, reason: str) -> BudgetVerdict:
            return BudgetVerdict(
                ok=False,
                kind=dimension,
                reason=reason,
                dimension=dim,
                remaining_usd=remaining_usd,
                utilization=utilization,
                warnings=warnings,
            )

        if now is not None:
            if self.deadline_passed(now):
                return fail("deadline", f"The mission deadline {self.limits.deadline} has passed")
            if self.time_exceeded(now):
                return fail("time", f"The mission time budget of {self.limits.time_budget_seconds}s is used up")

        for dim in dims:
            limit = self.limits.limit(dim)
            if limit is None:
                continue
            spent = self.spend.spent(dim)
            add: Decimal | int = extra_count if dim in COUNT_DIMENSIONS else estimate
            unit = "" if dim in COUNT_DIMENSIONS else " USD"
            if add > 0:
                if spent + add > limit:
                    return fail(
                        dim,
                        f"{dim} budget would be exceeded: spent {spent}{unit} + requested {add}{unit} "
                        f"> limit {limit}{unit}",
                    )
            elif spent >= limit:
                return fail(dim, f"{dim} budget exhausted: spent {spent}{unit} of {limit}{unit}")
        return BudgetVerdict(
            ok=True,
            kind=dimension,
            reason=None,
            dimension=None,
            remaining_usd=remaining_usd,
            utilization=utilization,
            warnings=warnings,
        )

    def to_dict(self, now: datetime | None = None) -> dict[str, Any]:
        data: dict[str, Any] = {
            "dimensions": [
                {
                    "dimension": s.dimension,
                    "limit": s.limit,
                    "spent": s.spent,
                    "remaining": s.remaining,
                    "utilization": s.utilization,
                    "status": s.status,
                }
                for s in self.dimensions()
            ],
            "time_budget_seconds": self.limits.time_budget_seconds,
            "deadline": self.limits.deadline,
            "started_at": self.started_at,
        }
        if now is not None:
            data["elapsed_seconds"] = self.elapsed_seconds(now)
            data["time_remaining_seconds"] = self.time_remaining_seconds(now)
            data["time_exceeded"] = self.time_exceeded(now)
            data["deadline_passed"] = self.deadline_passed(now)
        return data
