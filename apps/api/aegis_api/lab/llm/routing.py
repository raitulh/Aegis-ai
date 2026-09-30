"""Model routing for the gateway: builds candidates from settings, registered providers and ``ModelConfig`` rows,
then delegates the decision to the pure engine (:mod:`engines.lab.routing`).

Candidate sources (highest precedence first when the same provider/model/tier appears twice):

* ``org`` — enabled ``model_configs`` rows of the organization;
* ``platform`` — enabled platform-wide ``model_configs`` rows (``organization_id`` NULL);
* ``settings`` — the tier → model mapping from environment configuration (e.g. ``GEMINI_FAST_MODEL``) for
  every provider that is configured (API key / base URL present).

Model ids come only from settings or the database — never from code.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from aegis_api.config import Settings, get_settings
from aegis_api.lab.llm.base import BUILTIN_CAPABILITIES, ProviderCapabilities
from aegis_api.lab.llm.costs import ModelConfigView, price_from_config, settings_price
from aegis_api.lab.llm.schemas import RejectedRoute, RouteDecision, RouteOption
from engines.lab import routing as engine
from engines.lab.routing import ModelCandidate, RoutingDecision, RoutingPolicy, RoutingRequest

DEFAULT_PRIORITY = 100
SECONDARY_PRIORITY = 200


@dataclass(frozen=True)
class ProviderProfile:
    """What the router needs to know about a usable provider kind (no credentials)."""

    kind: str
    capabilities: ProviderCapabilities
    models: Mapping[str, str] = field(default_factory=dict)  # tier → model id
    priority: int = DEFAULT_PRIORITY

    @property
    def local(self) -> bool:
        return self.capabilities.local


def settings_models(kind: str, settings: Settings) -> dict[str, str]:
    """Tier → model mapping configured in the environment for a built-in provider."""
    pairs: dict[str, str | None]
    if kind == "gemini":
        pairs = {
            "fast": settings.gemini_fast_model,
            "default": settings.effective_gemini_default_model,
            "reasoning": settings.gemini_reasoning_model,
        }
    elif kind == "openai":
        pairs = {"default": settings.openai_model, "reasoning": settings.openai_reasoning_model}
    elif kind == "anthropic":
        pairs = {"default": settings.anthropic_model, "reasoning": settings.anthropic_reasoning_model}
    elif kind == "ollama":
        pairs = {"fast": settings.ollama_model}
    else:
        pairs = {}
    return {tier: model for tier, model in pairs.items() if model}


def settings_profiles(kinds: Iterable[str], settings: Settings | None = None) -> list[ProviderProfile]:
    s = settings or get_settings()
    return [
        ProviderProfile(
            kind=kind,
            capabilities=BUILTIN_CAPABILITIES[kind],
            models=settings_models(kind, s),
            priority=DEFAULT_PRIORITY if kind == s.llm_default_provider else SECONDARY_PRIORITY,
        )
        for kind in kinds
        if kind in BUILTIN_CAPABILITIES
    ]


def _config_capabilities(view: ModelConfigView, base: ProviderCapabilities) -> frozenset[str]:
    caps = set(base.as_set())
    for name, enabled in (view.capabilities or {}).items():
        if name not in engine.CAPABILITIES:
            continue
        if enabled:
            caps.add(name)
        else:
            caps.discard(name)
    return frozenset(caps)


class ModelRouter:
    """Routes a task to a primary model plus fallbacks. Pure given its inputs (no IO)."""

    def __init__(
        self,
        profiles: Sequence[ProviderProfile],
        configs: Sequence[ModelConfigView] = (),
        *,
        pricing: Mapping[str, Any] | None = None,
    ) -> None:
        self.profiles = {p.kind: p for p in profiles}
        self.configs = list(configs)
        self.pricing = pricing if pricing is not None else get_settings().model_pricing
        self.unconfigured: list[RejectedRoute] = []
        self._candidates = self._build_candidates()

    @classmethod
    def from_settings(
        cls, configs: Sequence[ModelConfigView] = (), *, kinds: Iterable[str] | None = None
    ) -> ModelRouter:
        from aegis_api.lab.llm.providers import is_configured

        settings = get_settings()
        usable = [k for k in (kinds if kinds is not None else BUILTIN_CAPABILITIES) if is_configured(k, settings)]
        return cls(settings_profiles(usable, settings), configs)

    # -- candidates ----------------------------------------------------------------------------
    def _build_candidates(self) -> list[ModelCandidate]:
        candidates: list[ModelCandidate] = []
        for profile in self.profiles.values():
            for tier, model in profile.models.items():
                candidates.append(
                    ModelCandidate(
                        provider=profile.kind,
                        model=model,
                        tier=tier,
                        priority=profile.priority,
                        local=profile.local,
                        capabilities=profile.capabilities.as_set(),
                        price=settings_price(profile.kind, [model], self.pricing),
                        source="settings",
                    )
                )
        for view in self.configs:
            if not view.enabled:
                continue
            config_profile = self.profiles.get(view.provider_kind)
            if config_profile is None:
                self.unconfigured.append(
                    RejectedRoute(
                        candidate=f"{view.provider_kind}:{view.model}", reason="provider not configured on this server"
                    )
                )
                continue
            candidates.append(
                ModelCandidate(
                    provider=view.provider_kind,
                    model=view.model,
                    tier=view.tier,
                    priority=view.priority,
                    local=config_profile.local,
                    capabilities=_config_capabilities(view, config_profile.capabilities),
                    task_types=frozenset(view.task_types),
                    price=price_from_config(view) or settings_price(view.provider_kind, [view.model], self.pricing),
                    source="org" if view.organization_id is not None else "platform",
                    max_output_tokens=view.max_output_tokens,
                    temperature=view.temperature,
                    context_window=view.context_window,
                    config_id=view.id,
                )
            )
        return candidates

    @property
    def candidates(self) -> list[ModelCandidate]:
        return list(self._candidates)

    def _pinned_candidate(self, provider: str | None, model: str | None, tier: str) -> ModelCandidate | None:
        """A request may pin a model id (e.g. from an agent version's model policy) that no configuration lists;
        it is routable only on a configured provider, with that provider's capabilities."""
        if not model:
            return None
        kinds = [provider] if provider else list(self.profiles)
        if any(c.model == model and (not provider or c.provider == provider) for c in self._candidates):
            return None
        if len(kinds) != 1 or kinds[0] not in self.profiles:
            return None
        profile = self.profiles[kinds[0]]
        return ModelCandidate(
            provider=profile.kind,
            model=model,
            tier=tier,
            priority=profile.priority,
            local=profile.local,
            capabilities=profile.capabilities.as_set(),
            price=settings_price(profile.kind, [model], self.pricing),
            source="request",
        )

    # -- routing -------------------------------------------------------------------------------
    def decide(
        self,
        task_type: str,
        complexity: str = "medium",
        latency_budget: int | None = None,
        cost_budget: Decimal | float | None = None,
        policy: RoutingPolicy | None = None,
        *,
        tier: str | None = None,
        required_capabilities: Iterable[str] = (),
        provider: str | None = None,
        model: str | None = None,
        estimated_input_tokens: int = 1000,
        estimated_output_tokens: int = 1000,
    ) -> RoutingDecision:
        request = RoutingRequest(
            task_type=str(task_type),
            complexity=complexity,
            tier=tier,
            latency_budget_ms=latency_budget,
            cost_budget_usd=Decimal(str(cost_budget)) if cost_budget is not None else None,
            required_capabilities=frozenset(required_capabilities),
            provider=provider,
            model=model,
            estimated_input_tokens=estimated_input_tokens,
            estimated_output_tokens=estimated_output_tokens,
        )
        candidates = list(self._candidates)
        target, _ = engine.target_tier(request.task_type, complexity, latency_budget, tier)
        pinned = self._pinned_candidate(provider, model, target.value)
        if pinned is not None:
            candidates.append(pinned)
        return engine.route(request, candidates, policy)

    def route(
        self,
        task_type: str,
        complexity: str = "medium",
        latency_budget: int | None = None,
        cost_budget: Decimal | float | None = None,
        policy: RoutingPolicy | None = None,
        **kwargs: Any,
    ) -> RouteDecision:
        decision = self.decide(task_type, complexity, latency_budget, cost_budget, policy, **kwargs)
        return to_route_decision(
            str(task_type),
            decision,
            extra_rejected=self.unconfigured,
            required=sorted(kwargs.get("required_capabilities") or ()),
            external_allowed=(policy or RoutingPolicy()).allow_external,
        )


def _option(ranked: engine.RankedCandidate) -> RouteOption:
    c = ranked.candidate
    return RouteOption(
        provider=c.provider,
        model=c.model,
        tier=c.tier,
        priority=c.priority,
        source=c.source,
        local=c.local,
        estimated_cost_usd=ranked.estimated_cost_usd,
    )


def to_route_decision(
    task_type: str,
    decision: RoutingDecision,
    *,
    extra_rejected: Sequence[RejectedRoute] = (),
    required: Sequence[str] = (),
    external_allowed: bool = False,
) -> RouteDecision:
    primary = decision.primary
    return RouteDecision(
        task_type=task_type,
        tier=decision.tier,
        provider=primary.candidate.provider if primary else None,
        model=primary.candidate.model if primary else None,
        reason=decision.reason,
        primary=_option(primary) if primary else None,
        fallbacks=[_option(r) for r in decision.fallbacks],
        rejected=[RejectedRoute(candidate=r.key, reason=r.reason) for r in decision.rejected] + list(extra_rejected),
        required_capabilities=list(required),
        external_allowed=external_allowed,
    )
