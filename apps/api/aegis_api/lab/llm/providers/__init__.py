"""Provider factory: build LLM provider adapters from settings (API keys/URLs never leave this layer)."""

from __future__ import annotations

from collections.abc import Callable

import httpx

from aegis_api.config import Settings, get_settings
from aegis_api.lab.llm.base import LLMProvider
from aegis_api.lab.llm.providers.anthropic import AnthropicChatProvider
from aegis_api.lab.llm.providers.gemini import GeminiClient
from aegis_api.lab.llm.providers.local import OllamaChatProvider
from aegis_api.lab.llm.providers.openai import OpenAIChatProvider

SettingsFactory = Callable[[Settings], LLMProvider | None]


def _gemini(settings: Settings, transport: httpx.BaseTransport | None = None) -> LLMProvider | None:
    if not settings.gemini_api_key:
        return None
    return GeminiClient(
        settings.gemini_api_key,
        base_url=settings.gemini_base_url,
        timeout=settings.gemini_timeout_seconds,
        transport=transport,
    )


def _openai(settings: Settings, transport: httpx.BaseTransport | None = None) -> LLMProvider | None:
    if not settings.openai_api_key:
        return None
    return OpenAIChatProvider(
        settings.openai_api_key,
        timeout=settings.llm_timeout_seconds,
        default_max_tokens=settings.llm_max_output_tokens,
        transport=transport,
    )


def _anthropic(settings: Settings, transport: httpx.BaseTransport | None = None) -> LLMProvider | None:
    if not settings.anthropic_api_key:
        return None
    return AnthropicChatProvider(
        settings.anthropic_api_key,
        timeout=settings.llm_timeout_seconds,
        default_max_tokens=settings.llm_max_output_tokens,
        transport=transport,
    )


def _ollama(settings: Settings, transport: httpx.BaseTransport | None = None) -> LLMProvider | None:
    if not settings.ollama_base_url:
        return None
    return OllamaChatProvider(
        settings.ollama_base_url,
        timeout=settings.llm_timeout_seconds,
        default_max_tokens=settings.llm_max_output_tokens,
        transport=transport,
    )


SETTINGS_FACTORIES: dict[str, Callable[..., LLMProvider | None]] = {
    "gemini": _gemini,
    "openai": _openai,
    "anthropic": _anthropic,
    "ollama": _ollama,
}


def settings_fingerprint(kind: str, settings: Settings) -> tuple[object, ...]:
    """Values that, when changed, require rebuilding the provider (compared, never logged)."""
    if kind == "gemini":
        return (settings.gemini_api_key, settings.gemini_base_url, settings.gemini_timeout_seconds)
    if kind == "openai":
        return (settings.openai_api_key, settings.llm_timeout_seconds, settings.llm_max_output_tokens)
    if kind == "anthropic":
        return (settings.anthropic_api_key, settings.llm_timeout_seconds, settings.llm_max_output_tokens)
    if kind == "ollama":
        return (settings.ollama_base_url, settings.llm_timeout_seconds, settings.llm_max_output_tokens)
    return ()


def is_configured(kind: str, settings: Settings | None = None) -> bool:
    s = settings or get_settings()
    return bool(
        {
            "gemini": s.gemini_api_key,
            "openai": s.openai_api_key,
            "anthropic": s.anthropic_api_key,
            "ollama": s.ollama_base_url,
        }.get(kind)
    )


def build_provider(
    kind: str, settings: Settings | None = None, *, transport: httpx.BaseTransport | None = None
) -> LLMProvider | None:
    """Build the adapter for ``kind`` from settings, or ``None`` when it is not configured."""
    factory = SETTINGS_FACTORIES.get(kind)
    if factory is None:
        return None
    return factory(settings or get_settings(), transport)


__all__ = [
    "SETTINGS_FACTORIES",
    "AnthropicChatProvider",
    "GeminiClient",
    "OllamaChatProvider",
    "OpenAIChatProvider",
    "build_provider",
    "is_configured",
    "settings_fingerprint",
]
