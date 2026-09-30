"""OpenAI Chat Completions adapter (external provider; JSON mode for structured output)."""

from __future__ import annotations

import httpx

from aegis_api.lab.llm.base import OPENAI_CAPABILITIES, ProviderCapabilities
from aegis_api.lab.llm.errors import LLMUnavailableError
from aegis_api.lab.llm.providers._adapter import EngineChatProvider, SingleAttemptPost
from engines.providers.openai import OpenAIProvider

OPENAI_BASE_URL = "https://api.openai.com/v1"


class _OpenAIEngine(SingleAttemptPost, OpenAIProvider):
    pass


class OpenAIChatProvider(EngineChatProvider):
    kind = "openai"
    capabilities: ProviderCapabilities = OPENAI_CAPABILITIES

    def __init__(
        self,
        api_key: str,
        *,
        timeout: float,
        default_max_tokens: int,
        base_url: str = OPENAI_BASE_URL,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise LLMUnavailableError("OpenAI API key is not configured", code="llm_not_configured", provider="openai")
        engine = _OpenAIEngine(
            api_key=api_key, base_url=base_url, timeout=timeout, client=self.engine_client(timeout, transport)
        )
        super().__init__(engine, default_max_tokens=default_max_tokens)
