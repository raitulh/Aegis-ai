"""Model routing (pure, deterministic).

Given a task, its complexity and budgets, the configured model candidates and the organization's policy,
decide which model to call first and which to fall back to — and explain why in one human-readable line.

Rules, applied in order:

1. **Tier.** Every task type has a default tier (``TASK_TIERS``). An explicit ``tier`` on the request wins;
   otherwise ``complexity="high"`` bumps the tier one step up and ``"low"`` one step down, and a latency
   budget below ``FAST_LATENCY_BUDGET_MS`` prefers the fast tier.
2. **Filters.** A candidate is rejected when it is disabled for the task type, lacks a required capability
   (``structured_output``, ``tools``, ``search``, ``code_execution``, ``streaming``), is excluded by
   policy (external processing not allowed → only *local* models; allowed/denied provider lists), does
   not match an explicit provider/model pin, or — when a cost budget is set — has no configured price or
   an estimated cost above the budget. Unknown prices are acceptable only without a cost budget.
3. **Ordering.** Remaining candidates are sorted by (tier distance, tier tie-break, priority, source rank,
   estimated cost, provider, model). The first is the primary route; the rest are fallbacks.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from engines.lab.llm_costs import Price, estimate_cost


class Tier(StrEnum):
    FAST = "fast"
    DEFAULT = "default"
    REASONING = "reasoning"


TIER_ORDER: tuple[Tier, ...] = (Tier.FAST, Tier.DEFAULT, Tier.REASONING)
ROUTABLE_TIERS = frozenset(t.value for t in TIER_ORDER)

TASK_TIERS: dict[str, Tier] = {
    "classification": Tier.FAST,
    "extraction": Tier.FAST,
    "summarization": Tier.FAST,
    "planning": Tier.REASONING,
    "research": Tier.REASONING,
    "hypothesis_generation": Tier.REASONING,
    "hypothesis_critique": Tier.REASONING,
    "experiment_design": Tier.REASONING,
    "result_analysis": Tier.REASONING,
    "failure_analysis": Tier.REASONING,
    "strategy_evolution": Tier.REASONING,
    "scientific_review": Tier.REASONING,
    "verification": Tier.REASONING,
    "coding": Tier.DEFAULT,
    "report_generation": Tier.DEFAULT,
}

CAPABILITIES = frozenset({"structured_output", "tools", "search", "code_execution", "streaming", "background"})
FAST_LATENCY_BUDGET_MS = 3000
SOURCE_RANK = {"org": 0, "platform": 1, "request": 2, "settings": 3}
MAX_REASON_CHARS = 300


@dataclass(frozen=True)
class ModelCandidate:
    provider: str
    model: str
    tier: str
    priority: int = 100
    local: bool = False
    capabilities: frozenset[str] = frozenset()
    task_types: frozenset[str] = frozenset()  # empty = any task
    price: Price | None = None
    source: str = "settings"  # org | platform | request | settings
    max_output_tokens: int | None = None
    temperature: float | None = None
    context_window: int | None = None
    config_id: str | None = None

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.model}"


@dataclass(frozen=True)
class RoutingPolicy:
    allow_external: bool = False
    allowed_providers: frozenset[str] | None = None  # None = no allowlist
    denied_providers: frozenset[str] = frozenset()


@dataclass(frozen=True)
class RoutingRequest:
    task_type: str
    complexity: str = "medium"  # low | medium | high
    tier: str | None = None
    latency_budget_ms: int | None = None
    cost_budget_usd: Decimal | None = None
    required_capabilities: frozenset[str] = frozenset()
    provider: str | None = None
    model: str | None = None
    estimated_input_tokens: int = 1000
    estimated_output_tokens: int = 1000


@dataclass(frozen=True)
class RankedCandidate:
    candidate: ModelCandidate
    estimated_cost_usd: Decimal | None
    tier_distance: int


@dataclass(frozen=True)
class Rejection:
    key: str
    reason: str


@dataclass(frozen=True)
class RoutingDecision:
    tier: str
    ranked: tuple[RankedCandidate, ...]
    rejected: tuple[Rejection, ...]
    reason: str
    tier_reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def primary(self) -> RankedCandidate | None:
        return self.ranked[0] if self.ranked else None

    @property
    def fallbacks(self) -> tuple[RankedCandidate, ...]:
        return self.ranked[1:]


def base_tier(task_type: str) -> Tier:
    return TASK_TIERS.get(task_type, Tier.DEFAULT)


def _shift(tier: Tier, steps: int) -> Tier:
    index = min(max(TIER_ORDER.index(tier) + steps, 0), len(TIER_ORDER) - 1)
    return TIER_ORDER[index]


def target_tier(
    task_type: str, complexity: str = "medium", latency_budget_ms: int | None = None, explicit: str | None = None
) -> tuple[Tier, list[str]]:
    """The tier a request should run on, plus the reasons (for the route explanation)."""
    if explicit:
        tier = Tier(explicit)
        return tier, [f"explicit tier {tier.value}"]
    tier = base_tier(task_type)
    reasons = [f"{task_type}→{tier.value}"]
    if complexity == "high" and tier is not Tier.REASONING:
        tier = _shift(tier, 1)
        reasons.append(f"high complexity→{tier.value}")
    elif complexity == "low" and tier is not Tier.FAST:
        tier = _shift(tier, -1)
        reasons.append(f"low complexity→{tier.value}")
    if latency_budget_ms is not None and latency_budget_ms < FAST_LATENCY_BUDGET_MS and tier is not Tier.FAST:
        tier = Tier.FAST
        reasons.append(f"latency budget {latency_budget_ms}ms→fast")
    return tier, reasons


def _tier_index(tier: str) -> int:
    try:
        return TIER_ORDER.index(Tier(tier))
    except ValueError:
        return TIER_ORDER.index(Tier.DEFAULT)


def _reject_reason(candidate: ModelCandidate, request: RoutingRequest, policy: RoutingPolicy) -> str | None:
    if candidate.tier not in ROUTABLE_TIERS:
        return f"tier {candidate.tier} is not used for generation"
    if request.provider and candidate.provider != request.provider:
        return f"provider pinned to {request.provider}"
    if request.model and candidate.model != request.model:
        return f"model pinned to {request.model}"
    if candidate.task_types and request.task_type not in candidate.task_types:
        return f"not enabled for {request.task_type}"
    if not candidate.local and not policy.allow_external:
        return "external processing not allowed (local models only)"
    if policy.allowed_providers is not None and candidate.provider not in policy.allowed_providers:
        return "provider not in allowed list"
    if candidate.provider in policy.denied_providers:
        return "provider denied by policy"
    missing = sorted(request.required_capabilities - candidate.capabilities)
    if missing:
        return f"missing capability {', '.join(missing)}"
    return None


def route(
    request: RoutingRequest, candidates: Iterable[ModelCandidate], policy: RoutingPolicy | None = None
) -> RoutingDecision:
    """Rank candidates for ``request``. Deterministic for identical inputs."""
    policy = policy or RoutingPolicy()
    tier, tier_reasons = target_tier(request.task_type, request.complexity, request.latency_budget_ms, request.tier)
    want = TIER_ORDER.index(tier)
    prefer_lower = request.latency_budget_ms is not None and request.latency_budget_ms < FAST_LATENCY_BUDGET_MS

    ranked: list[tuple[tuple[object, ...], RankedCandidate]] = []
    rejected: list[Rejection] = []
    seen: set[tuple[str, str, str]] = set()
    for candidate in sorted(candidates, key=lambda c: (SOURCE_RANK.get(c.source, 9), c.provider, c.model, c.tier)):
        identity = (candidate.provider, candidate.model, candidate.tier)
        if identity in seen:  # the higher-precedence source (org > platform > settings) wins
            continue
        seen.add(identity)
        reason = _reject_reason(candidate, request, policy)
        max_out = request.estimated_output_tokens
        if candidate.max_output_tokens:
            max_out = min(max_out, candidate.max_output_tokens)
        cost = estimate_cost(candidate.price, request.estimated_input_tokens, max_out, local=candidate.local)
        if reason is None and request.cost_budget_usd is not None:
            if cost is None:
                reason = "no price configured (a cost budget requires a known price)"
            elif cost > request.cost_budget_usd:
                reason = f"estimated ${cost} exceeds cost budget ${request.cost_budget_usd}"
        if reason is not None:
            rejected.append(Rejection(candidate.key, reason))
            continue
        diff = _tier_index(candidate.tier) - want
        # Equal distance: prefer the lower tier under a latency budget, otherwise the stronger model.
        tie_break = (0 if diff < 0 else 1) if prefer_lower else (0 if diff > 0 else 1)
        sort_key = (
            abs(diff),
            tie_break if diff else 0,
            candidate.priority,
            SOURCE_RANK.get(candidate.source, 9),
            cost if cost is not None else Decimal("Infinity"),
            candidate.provider,
            candidate.model,
        )
        ranked.append(
            (sort_key, RankedCandidate(candidate=candidate, estimated_cost_usd=cost, tier_distance=abs(diff)))
        )
    ranked.sort(key=lambda item: item[0])
    ordered = tuple(item[1] for item in ranked)
    return RoutingDecision(
        tier=tier.value,
        ranked=ordered,
        rejected=tuple(rejected),
        reason=explain(request, tier, tier_reasons, ordered, rejected),
        tier_reasons=tuple(tier_reasons),
    )


def explain(
    request: RoutingRequest,
    tier: Tier,
    tier_reasons: Sequence[str],
    ranked: Sequence[RankedCandidate],
    rejected: Sequence[Rejection],
) -> str:
    head = "; ".join(tier_reasons)
    if not ranked:
        why = "; ".join(f"{r.key}: {r.reason}" for r in rejected[:4]) or "no candidates configured"
        return _clip(f"{head}; no eligible model ({why})")
    first = ranked[0]
    match = "tier match" if first.tier_distance == 0 else f"nearest tier {first.candidate.tier}"
    cost = f", est ${first.estimated_cost_usd}" if first.estimated_cost_usd is not None else ""
    text = (
        f"{head}; selected {first.candidate.key} ({match}, priority {first.candidate.priority}, "
        f"{first.candidate.source}{cost}); {len(ranked) - 1} fallback(s)"
    )
    if rejected:
        text += f"; {len(rejected)} rejected"
    return _clip(text)


def _clip(text: str) -> str:
    return text if len(text) <= MAX_REASON_CHARS else text[: MAX_REASON_CHARS - 1] + "…"
