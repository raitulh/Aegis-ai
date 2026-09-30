"""Provider registry + model catalogue built from configuration (never hard-coded model IDs).

Sources, in precedence order:
1. organization ``lab.model_configs`` rows (enable/disable, pricing, tier, latency overrides);
2. ``MODEL_CATALOG_JSON`` (deployment-wide extra entries/overrides);
3. provider settings: ``GEMINI_{FAST,DEFAULT,REASONING}_MODEL`` / ``GEMINI_DEEP_RESEARCH_AGENT``,
   ``OPENAI_MODEL``, ``ANTHROPIC_MODEL``, ``OLLAMA_MODEL``; prices from ``MODEL_PRICING_JSON``.

The pure ``engines.lab.routing.ModelRouter`` ranks the resulting candidates; this module only instantiates
providers and caches their health.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

import structlog

from aegis_api.config import get_settings
from aegis_api.infrastructure.llm.base import LLMError, LLMProvider
from engines.lab.enums import ModelTier
from engines.lab.routing import Feature, ModelCandidate

log = structlog.get_logger("aegis.llm")

HEALTH_TTL_SECONDS = 60.0


@dataclass
class ProviderRegistry:
    providers: dict[str, LLMProvider]
    candidates: list[ModelCandidate]
    _health: dict[str, tuple[float, bool]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def provider(self, name: str) -> LLMProvider:
        if name not in self.providers:
            raise KeyError(f"provider '{name}' is not configured")
        return self.providers[name]

    def healthy(self, name: str) -> bool:
        now = time.monotonic()
        with self._lock:
            cached = self._health.get(name)
            if cached and now - cached[0] < HEALTH_TTL_SECONDS:
                return cached[1]
        ok = False
        if name in self.providers:
            try:
                ok = self.providers[name].health()[0]
            except Exception:
                ok = False
        with self._lock:
            self._health[name] = (now, ok)
        return ok

    def available_candidates(self, *, check_health: bool = True) -> list[ModelCandidate]:
        out = []
        for cand in self.candidates:
            if cand.provider not in self.providers:
                continue
            if check_health and cand.provider == "ollama" and not self.healthy("ollama"):
                continue  # local daemons are often absent; remote APIs are checked lazily via errors
            out.append(cand)
        return out


def _price(pricing: dict[str, dict[str, float]], provider: str, model: str) -> tuple[float | None, float | None]:
    entry = pricing.get(f"{provider}:{model}") or pricing.get(provider)
    if not entry:
        return None, None
    return float(entry.get("input_per_mtok", 0.0)), float(entry.get("output_per_mtok", 0.0))


def _default_candidates(providers: dict[str, LLMProvider]) -> list[ModelCandidate]:
    s = get_settings()
    pricing = s.model_pricing
    out: list[ModelCandidate] = []
    if "gemini" in providers:
        feats = providers["gemini"].features - {Feature.AGENT}
        for tier_name, model in s.gemini_models.items():
            if model:
                inp, outp = _price(pricing, "gemini", model)
                out.append(ModelCandidate("gemini", model, ModelTier(tier_name), feats, inp, outp, None, True))
        if s.gemini_deep_research_agent:
            out.append(
                ModelCandidate(
                    "gemini",
                    s.gemini_deep_research_agent,
                    ModelTier.DEEP_RESEARCH,
                    frozenset({Feature.BACKGROUND, Feature.AGENT, Feature.WEB_SEARCH}),
                    None,
                    None,
                    None,
                    True,
                    is_agent=True,
                )
            )
    if "openai" in providers and s.openai_model:
        inp, outp = _price(pricing, "openai", s.openai_model)
        out.append(
            ModelCandidate(
                "openai", s.openai_model, ModelTier.DEFAULT, providers["openai"].features, inp, outp, None, True
            )
        )
    if "anthropic" in providers and s.anthropic_model:
        inp, outp = _price(pricing, "anthropic", s.anthropic_model)
        out.append(
            ModelCandidate(
                "anthropic",
                s.anthropic_model,
                ModelTier.DEFAULT,
                providers["anthropic"].features,
                inp,
                outp,
                None,
                True,
            )
        )
    if "ollama" in providers and s.ollama_model:
        out.append(
            ModelCandidate(
                "ollama", s.ollama_model, ModelTier.FAST, providers["ollama"].features, 0.0, 0.0, None, False
            )
        )
    return out


def _apply_overrides(candidates: list[ModelCandidate], overrides: Iterable[dict[str, Any]]) -> list[ModelCandidate]:
    by_key: dict[tuple[str, str, str], ModelCandidate] = {(c.provider, c.model, c.tier.value): c for c in candidates}
    for o in overrides:
        provider, model = str(o.get("provider", "")), str(o.get("model", ""))
        if not provider or not model:
            continue
        tier = ModelTier(str(o.get("tier", "default")))
        key = (provider, model, tier.value)
        if o.get("enabled") is False:
            by_key = {k: v for k, v in by_key.items() if not (k[0] == provider and k[1] == model)}
            continue
        base = by_key.get(key)
        features = frozenset(o.get("features") or (base.features if base else ()))
        by_key[key] = ModelCandidate(
            provider=provider,
            model=model,
            tier=tier,
            features=features,
            input_per_mtok=o.get("input_per_mtok", base.input_per_mtok if base else None),
            output_per_mtok=o.get("output_per_mtok", base.output_per_mtok if base else None),
            typical_latency_ms=o.get("typical_latency_ms", base.typical_latency_ms if base else None),
            data_leaves_organization=provider != "ollama",
            is_agent=bool(o.get("is_agent", base.is_agent if base else False)),
        )
    return sorted(by_key.values(), key=lambda c: (c.provider, c.model, c.tier.rank))


_BASE: ProviderRegistry | None = None
_BASE_LOCK = threading.Lock()
_OVERRIDE: Callable[[], ProviderRegistry] | None = None


def _build_base() -> ProviderRegistry:
    s = get_settings()
    providers: dict[str, LLMProvider] = {}
    try:
        if s.gemini_api_key:
            from aegis_api.infrastructure.llm.providers.gemini.provider import GeminiProvider

            providers["gemini"] = GeminiProvider(
                api_key=s.gemini_api_key,
                base_url=s.gemini_base_url,
                api_revision=s.gemini_api_revision,
                timeout=s.gemini_timeout_seconds,
                embed_model=s.gemini_embed_model,
            )
        if s.openai_api_key:
            from aegis_api.infrastructure.llm.providers.openai.provider import OpenAIProvider

            providers["openai"] = OpenAIProvider(api_key=s.openai_api_key, timeout=s.model_timeout_seconds)
        if s.anthropic_api_key:
            from aegis_api.infrastructure.llm.providers.anthropic.provider import AnthropicProvider

            providers["anthropic"] = AnthropicProvider(api_key=s.anthropic_api_key, timeout=s.model_timeout_seconds)
        if s.ollama_base_url:
            from aegis_api.infrastructure.llm.providers.local.provider import OllamaProvider

            providers["ollama"] = OllamaProvider(
                base_url=s.ollama_base_url, timeout=max(s.model_timeout_seconds, 120.0)
            )
    except LLMError as exc:
        log.warning("llm_provider_init_failed", provider=exc.provider, kind=exc.kind.value)
    candidates = _apply_overrides(_default_candidates(providers), s.model_catalog)
    return ProviderRegistry(providers=providers, candidates=candidates)


def get_registry(org_overrides: Iterable[dict[str, Any]] = ()) -> ProviderRegistry:
    """Deployment registry, with organization catalogue overrides applied on top (providers are shared)."""
    global _BASE
    if _OVERRIDE is not None:
        base = _OVERRIDE()
    else:
        with _BASE_LOCK:
            if _BASE is None:
                _BASE = _build_base()
            base = _BASE
    overrides = list(org_overrides)
    if not overrides:
        return base
    return ProviderRegistry(
        providers=base.providers, candidates=_apply_overrides(list(base.candidates), overrides), _health=base._health
    )


def set_registry_override(factory: Callable[[], ProviderRegistry] | None) -> None:
    """Dependency injection hook (tests / embedded deployments)."""
    global _OVERRIDE, _BASE
    _OVERRIDE = factory
    _BASE = None


def reset_registry() -> None:
    global _BASE
    _BASE = None
