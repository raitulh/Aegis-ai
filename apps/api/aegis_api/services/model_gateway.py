"""Construct provider instances and model routers from stored provider/system configuration."""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.models import AISystem, Provider
from aegis_api.security.ssrf import validate_outbound_url
from aegis_api.services import secrets_service
from engines.providers.anthropic import AnthropicProvider
from engines.providers.base import ModelProvider, ProviderUnavailable
from engines.providers.demo import DemoProvider
from engines.providers.embeddings import Embedder, HashEmbedder, ProviderEmbedder
from engines.providers.gemini import GeminiProvider
from engines.providers.ollama import OllamaProvider
from engines.providers.openai import OpenAIProvider
from engines.providers.router import ModelRouter

log = structlog.get_logger("aegis.gateway")


def build_provider(session: Session, provider: Provider) -> ModelProvider:
    settings = get_settings()
    api_key = secrets_service.resolve_optional(session, provider.secret_id, provider.organization_id)
    timeout = settings.model_timeout_seconds
    if provider.kind == "ollama":
        base = provider.base_url or settings.ollama_base_url or "http://localhost:11434"
        return OllamaProvider(
            base_url=base, default_model=provider.default_model or settings.ollama_model, timeout=timeout
        )
    if provider.kind == "gemini":
        return GeminiProvider(
            api_key=api_key or settings.gemini_api_key or "",
            default_model=provider.default_model or settings.gemini_model,
            timeout=timeout,
        )
    if provider.kind == "openai":
        return OpenAIProvider(
            api_key=api_key or settings.openai_api_key or "",
            default_model=provider.default_model or settings.openai_model,
            timeout=timeout,
        )
    if provider.kind == "anthropic":
        return AnthropicProvider(
            api_key=api_key or settings.anthropic_api_key or "",
            default_model=provider.default_model or settings.anthropic_model,
            timeout=timeout,
        )
    if provider.kind == "demo":
        profile = (provider.settings or {}).get("profile", "hiring_agent")
        return DemoProvider(profile=profile)
    raise ProviderUnavailable(f"Unsupported provider kind '{provider.kind}'")


def build_judge_router(
    session: Session, organization_id: uuid.UUID, *, preference: str | None = None, on_call=None
) -> ModelRouter:
    """Build a model router for model-assisted evaluation from the org's configured providers."""
    settings = get_settings()
    local: ModelProvider | None = None
    external: ModelProvider | None = None
    external_allowed = False
    providers = session.query(Provider).filter(Provider.organization_id == organization_id).all()
    for provider in providers:
        try:
            instance = build_provider(session, provider)
        except ProviderUnavailable:
            continue
        if provider.kind == "ollama" and local is None:
            local = instance
        elif provider.kind in ("gemini", "openai", "anthropic") and external is None:
            external = instance
            external_allowed = provider.allow_data_processing
    # Fall back to environment-configured providers when none stored.
    if local is None and settings.ollama_base_url:
        local = OllamaProvider(
            base_url=settings.ollama_base_url,
            default_model=settings.ollama_model,
            timeout=settings.model_timeout_seconds,
        )
    if external is None and settings.gemini_api_key:
        external = GeminiProvider(api_key=settings.gemini_api_key, default_model=settings.gemini_model)
        external_allowed = False  # env key present but org has not explicitly consented per-run
    return ModelRouter(
        local=local, external=external, external_allowed=external_allowed, preferred=preference, on_call=on_call
    )


def build_target_provider(session: Session, system: AISystem) -> ModelProvider | None:
    if system.is_demo or (system.config or {}).get("demo_profile"):
        profile = (system.config or {}).get("demo_profile", "hiring_agent")
        return DemoProvider(profile=profile, config={"guardrails": (system.config or {}).get("guardrails", {})})
    if system.provider_id:
        provider = session.get(Provider, system.provider_id)
        if provider:
            return build_provider(session, provider)
    if system.endpoint_url:
        validate_outbound_url(system.endpoint_url)
        return None  # handled by HTTPEndpointTarget in context_builder
    return None


def build_embedder(session: Session, organization_id: uuid.UUID) -> Embedder:
    settings = get_settings()
    if settings.embedding_provider == "hash":
        return HashEmbedder(dim=settings.embedding_dim)
    kind = settings.embedding_provider
    provider = (
        session.query(Provider).filter(Provider.organization_id == organization_id, Provider.kind == kind).first()
    )
    try:
        if provider:
            instance = build_provider(session, provider)
        elif kind == "ollama" and settings.ollama_base_url:
            instance = OllamaProvider(base_url=settings.ollama_base_url)
        elif kind == "gemini" and settings.gemini_api_key:
            instance = GeminiProvider(api_key=settings.gemini_api_key)
        else:
            return HashEmbedder(dim=settings.embedding_dim)
        model = settings.ollama_embed_model if kind == "ollama" else settings.gemini_embed_model
        return ProviderEmbedder(instance, model=model, dim=settings.embedding_dim)
    except Exception as exc:
        log.warning("embedder_fallback", error=str(exc))
        return HashEmbedder(dim=settings.embedding_dim)
