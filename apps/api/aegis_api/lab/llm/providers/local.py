"""Local model adapter (Ollama). Runs on infrastructure the organization controls — data never leaves the
organization, so it is usable without external data-processing consent. Reasoning traces are disabled and
``<think>`` blocks stripped by the engine class (chain-of-thought is never stored)."""

from __future__ import annotations

import httpx

from aegis_api.lab.llm.base import OLLAMA_CAPABILITIES, ProviderCapabilities
from aegis_api.lab.llm.errors import LLMUnavailableError
from aegis_api.lab.llm.providers._adapter import EngineChatProvider, SingleAttemptPost
from engines.providers.ollama import OllamaProvider


class _OllamaEngine(SingleAttemptPost, OllamaProvider):
    pass


class OllamaChatProvider(EngineChatProvider):
    kind = "ollama"
    capabilities: ProviderCapabilities = OLLAMA_CAPABILITIES

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float,
        default_max_tokens: int,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not base_url:
            raise LLMUnavailableError("Ollama base URL is not configured", code="llm_not_configured", provider="ollama")
        engine = _OllamaEngine(base_url=base_url, timeout=timeout, client=self.engine_client(timeout, transport))
        super().__init__(engine, default_max_tokens=default_max_tokens)
