"""Anthropic Messages adapter (external provider; schema-in-system-prompt for structured output)."""

from __future__ import annotations

import httpx

from aegis_api.lab.llm.base import ANTHROPIC_CAPABILITIES, ProviderCapabilities
from aegis_api.lab.llm.errors import LLMUnavailableError
from aegis_api.lab.llm.providers._adapter import EngineChatProvider, SingleAttemptPost
from engines.providers.anthropic import AnthropicProvider

ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"


class _AnthropicEngine(SingleAttemptPost, AnthropicProvider):
    pass


class AnthropicChatProvider(EngineChatProvider):
    kind = "anthropic"
    capabilities: ProviderCapabilities = ANTHROPIC_CAPABILITIES

    def __init__(
        self,
        api_key: str,
        *,
        timeout: float,
        default_max_tokens: int,
        base_url: str = ANTHROPIC_BASE_URL,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise LLMUnavailableError(
                "Anthropic API key is not configured", code="llm_not_configured", provider="anthropic"
            )
        engine = _AnthropicEngine(
            api_key=api_key, base_url=base_url, timeout=timeout, client=self.engine_client(timeout, transport)
        )
        super().__init__(engine, default_max_tokens=default_max_tokens)
