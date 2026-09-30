"""Model routing (pure): pick a provider/model for a task under latency, cost and policy constraints.

The router never hard-codes model IDs. The application layer builds the candidate catalogue from
configuration (``GEMINI_FAST_MODEL``, ``GEMINI_DEFAULT_MODEL``, ...) and the organization's registered
providers; this module only ranks and filters them deterministically.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from engines.lab.enums import ModelTier, TaskClass


class Feature:
    STRUCTURED_OUTPUT = "structured_output"
    TOOLS = "tools"
    CODE_EXECUTION = "code_execution"
    STREAMING = "streaming"
    BACKGROUND = "background"
    REMOTE_MCP = "remote_mcp"
    WEB_SEARCH = "web_search"
    AGENT = "agent"


@dataclass(frozen=True)
class ModelCandidate:
    provider: str
    model: str
    tier: ModelTier
    features: frozenset[str] = frozenset()
    input_per_mtok: float | None = None
    output_per_mtok: float | None = None
    typical_latency_ms: int | None = None
    data_leaves_organization: bool = True
    is_agent: bool = False
    available: bool = True

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.model}"

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float | None:
        if self.input_per_mtok is None or self.output_per_mtok is None:
            return None
        return round(input_tokens / 1e6 * self.input_per_mtok + output_tokens / 1e6 * self.output_per_mtok, 6)


@dataclass(frozen=True)
class TaskProfile:
    tier: ModelTier
    required: frozenset[str] = frozenset()
    typical_input_tokens: int = 2_000
    typical_output_tokens: int = 800


TASK_PROFILES: dict[TaskClass, TaskProfile] = {
    TaskClass.CLASSIFICATION: TaskProfile(ModelTier.FAST, frozenset({Feature.STRUCTURED_OUTPUT}), 800, 150),
    TaskClass.EXTRACTION: TaskProfile(ModelTier.FAST, frozenset({Feature.STRUCTURED_OUTPUT}), 4_000, 800),
    TaskClass.SUMMARIZATION: TaskProfile(ModelTier.FAST, frozenset(), 6_000, 600),
    TaskClass.PLANNING: TaskProfile(ModelTier.REASONING, frozenset({Feature.STRUCTURED_OUTPUT}), 3_000, 1_500),
    TaskClass.RESEARCH: TaskProfile(ModelTier.DEEP_RESEARCH, frozenset({Feature.BACKGROUND}), 2_000, 8_000),
    TaskClass.HYPOTHESIS_GENERATION: TaskProfile(
        ModelTier.REASONING, frozenset({Feature.STRUCTURED_OUTPUT}), 6_000, 2_500
    ),
    TaskClass.HYPOTHESIS_CRITIQUE: TaskProfile(
        ModelTier.REASONING, frozenset({Feature.STRUCTURED_OUTPUT}), 5_000, 2_000
    ),
    TaskClass.EXPERIMENT_DESIGN: TaskProfile(ModelTier.REASONING, frozenset({Feature.STRUCTURED_OUTPUT}), 5_000, 2_500),
    TaskClass.CODING: TaskProfile(ModelTier.DEFAULT, frozenset({Feature.STRUCTURED_OUTPUT}), 6_000, 4_000),
    TaskClass.RESULT_ANALYSIS: TaskProfile(ModelTier.DEFAULT, frozenset({Feature.STRUCTURED_OUTPUT}), 4_000, 1_200),
    TaskClass.FAILURE_ANALYSIS: TaskProfile(ModelTier.DEFAULT, frozenset({Feature.STRUCTURED_OUTPUT}), 5_000, 1_200),
    TaskClass.STRATEGY_EVOLUTION: TaskProfile(
        ModelTier.REASONING, frozenset({Feature.STRUCTURED_OUTPUT}), 4_000, 1_500
    ),
    TaskClass.SCIENTIFIC_REVIEW: TaskProfile(ModelTier.REASONING, frozenset({Feature.STRUCTURED_OUTPUT}), 8_000, 2_000),
    TaskClass.REPORT_GENERATION: TaskProfile(ModelTier.DEFAULT, frozenset({Feature.STRUCTURED_OUTPUT}), 10_000, 4_000),
    TaskClass.VERIFICATION: TaskProfile(ModelTier.REASONING, frozenset({Feature.STRUCTURED_OUTPUT}), 5_000, 1_000),
}


@dataclass(frozen=True)
class RoutingPolicy:
    allow_external: bool = True
    allowed_providers: frozenset[str] | None = None
    denied_models: frozenset[str] = frozenset()
    preferred_provider: str | None = None
    max_tier: ModelTier | None = None
    allow_unpriced: bool = True
    # Pin a specific model for a task (reproducibility): {"coding": "gemini:model-x"}
    pinned: dict[str, str] = field(default_factory=dict)


@dataclass
class RouteDecision:
    task_type: str
    candidate: ModelCandidate | None
    tier: ModelTier
    reason: str
    estimated_cost_usd: float | None
    estimated_input_tokens: int
    estimated_output_tokens: int
    alternatives: list[str] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.candidate is not None


def _target_tier(profile: TaskProfile, complexity: float, max_tier: ModelTier | None) -> ModelTier:
    tier = profile.tier
    ordered = [ModelTier.FAST, ModelTier.DEFAULT, ModelTier.REASONING]
    if tier in ordered:
        idx = ordered.index(tier)
        if complexity >= 0.75:
            idx = min(idx + 1, len(ordered) - 1)
        elif complexity <= 0.2:
            idx = max(idx - 1, 0)
        tier = ordered[idx]
    if max_tier is not None and tier.rank > max_tier.rank:
        tier = max_tier
    return tier


class ModelRouter:
    """``route(task_type, complexity, latency_budget, cost_budget, policy)`` over a candidate catalogue."""

    def __init__(self, candidates: Sequence[ModelCandidate]) -> None:
        self.candidates = list(candidates)

    def route(
        self,
        task_type: str,
        complexity: float = 0.5,
        latency_budget_ms: int | None = None,
        cost_budget_usd: float | None = None,
        policy: RoutingPolicy | None = None,
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        required_features: frozenset[str] = frozenset(),
    ) -> RouteDecision:
        policy = policy or RoutingPolicy()
        task = TaskClass(task_type)
        profile = TASK_PROFILES[task]
        complexity = min(max(complexity, 0.0), 1.0)
        tier = _target_tier(profile, complexity, policy.max_tier)
        in_tok = input_tokens if input_tokens is not None else profile.typical_input_tokens
        out_tok = output_tokens if output_tokens is not None else profile.typical_output_tokens
        required = profile.required | required_features
        decision = RouteDecision(
            task_type=task.value,
            candidate=None,
            tier=tier,
            reason="",
            estimated_cost_usd=None,
            estimated_input_tokens=in_tok,
            estimated_output_tokens=out_tok,
        )

        pinned = policy.pinned.get(task.value)
        eligible: list[tuple[ModelCandidate, float | None]] = []
        for cand in sorted(self.candidates, key=lambda c: (c.provider, c.model)):
            why = self._reject_reason(cand, tier, required, policy, pinned)
            cost = cand.estimate_cost(in_tok, out_tok)
            if (
                why is None
                and latency_budget_ms is not None
                and cand.typical_latency_ms is not None
                and cand.typical_latency_ms > latency_budget_ms
            ):
                why = f"typical latency {cand.typical_latency_ms}ms exceeds budget {latency_budget_ms}ms"
            if why is None and cost_budget_usd is not None:
                if cost is None and not policy.allow_unpriced:
                    why = "no price configured and policy forbids unpriced models under a cost budget"
                elif cost is not None and cost > cost_budget_usd:
                    why = f"estimated cost ${cost} exceeds budget ${cost_budget_usd}"
            if why:
                decision.rejected.append((cand.key, why))
            else:
                eligible.append((cand, cost))

        if not eligible:
            decision.reason = f"No eligible model for task '{task.value}' at tier '{tier.value}'"
            return decision

        def score(item: tuple[ModelCandidate, float | None]) -> tuple[int, int, int, float, int]:
            cand, cost = item
            tier_distance = abs(cand.tier.rank - tier.rank)
            below_penalty = 1 if cand.tier.rank < tier.rank else 0
            preferred = 0 if policy.preferred_provider in (None, cand.provider) else 1
            return (
                preferred,
                tier_distance,
                below_penalty,
                cost if cost is not None else 1e9,
                cand.typical_latency_ms or 10**9,
            )

        eligible.sort(key=score)
        chosen, cost = eligible[0]
        decision.candidate = chosen
        decision.estimated_cost_usd = cost
        decision.alternatives = [c.key for c, _ in eligible[1:4]]
        decision.reason = (
            f"{task.value} (complexity {complexity:.2f}) → tier {tier.value} → {chosen.key}"
            + (" [pinned]" if pinned else "")
            + (f"; est. ${cost}" if cost is not None else "; cost unpriced")
        )
        return decision

    @staticmethod
    def _reject_reason(
        cand: ModelCandidate,
        tier: ModelTier,
        required: frozenset[str],
        policy: RoutingPolicy,
        pinned: str | None,
    ) -> str | None:
        if not cand.available:
            return "provider unavailable"
        if pinned and cand.key != pinned:
            return f"task pinned to {pinned}"
        if cand.key in policy.denied_models:
            return "model denied by policy"
        if policy.allowed_providers is not None and cand.provider not in policy.allowed_providers:
            return "provider not allowed by policy"
        if cand.data_leaves_organization and not policy.allow_external:
            return "external providers not permitted (no data-processing consent)"
        missing = required - cand.features
        if missing:
            return f"missing features: {', '.join(sorted(missing))}"
        if tier == ModelTier.DEEP_RESEARCH and not cand.is_agent:
            return "deep research requires a research agent"
        if tier != ModelTier.DEEP_RESEARCH and cand.is_agent:
            return "agents are only used for deep research tasks"
        if policy.max_tier is not None and cand.tier.rank > policy.max_tier.rank:
            return f"tier {cand.tier.value} above policy maximum"
        return None
