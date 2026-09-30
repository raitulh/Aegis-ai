"""The provider contract every LLM adapter implements.

Adapters are thin, synchronous and stateless per call: they translate an :class:`LLMRequest` into the
provider's wire format, perform exactly **one** HTTP request (the gateway owns retries, fallbacks, usage
recording and structured-output validation) and map failures onto :mod:`aegis_api.lab.llm.errors`.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from aegis_api.lab.llm.schemas import LLMRequest, LLMResponse, ProviderCapabilitiesOut, StreamChunk


@dataclass(frozen=True)
class ProviderCapabilities:
    structured_output: bool = False
    tools: bool = False
    search: bool = False
    code_execution: bool = False
    streaming: bool = False
    background: bool = False
    # Local providers run on infrastructure the organization controls: data never leaves the organization,
    # so they do not require external data-processing consent.
    local: bool = False

    def as_set(self) -> frozenset[str]:
        names = ("structured_output", "tools", "search", "code_execution", "streaming", "background")
        return frozenset(name for name in names if getattr(self, name))

    def as_out(self) -> ProviderCapabilitiesOut:
        return ProviderCapabilitiesOut(
            structured_output=self.structured_output,
            tools=self.tools,
            search=self.search,
            code_execution=self.code_execution,
            streaming=self.streaming,
            local=self.local,
        )


@runtime_checkable
class LLMProvider(Protocol):
    kind: str
    capabilities: ProviderCapabilities

    def generate(self, request: LLMRequest, *, model: str) -> LLMResponse:
        """One call. Returns a response with provider-reported usage; cost/route fields are set by the gateway."""
        ...

    def stream(self, request: LLMRequest, *, model: str) -> Iterator[StreamChunk]:
        """Yield text deltas; the final chunk has ``done=True`` and the usage."""
        ...

    def close(self) -> None: ...


# Capability profiles of the built-in adapters (used for routing before a provider is instantiated).
GEMINI_CAPABILITIES = ProviderCapabilities(
    structured_output=True, tools=True, search=True, code_execution=True, streaming=True, background=True
)
OPENAI_CAPABILITIES = ProviderCapabilities(structured_output=True, streaming=True)
ANTHROPIC_CAPABILITIES = ProviderCapabilities(structured_output=True, streaming=True)
OLLAMA_CAPABILITIES = ProviderCapabilities(structured_output=True, streaming=True, local=True)

BUILTIN_CAPABILITIES: dict[str, ProviderCapabilities] = {
    "gemini": GEMINI_CAPABILITIES,
    "openai": OPENAI_CAPABILITIES,
    "anthropic": ANTHROPIC_CAPABILITIES,
    "ollama": OLLAMA_CAPABILITIES,
}
EXTERNAL_KINDS = frozenset({"gemini", "openai", "anthropic"})
