"""Adapter from the existing ``engines.providers`` chat classes to the lab :class:`LLMProvider` contract.

The engine classes do their own retrying in ``_post``; the gateway owns retries here, so the adapted classes
override ``_post`` with a single attempt that raises typed LLM errors. Structured output uses the provider's
JSON mode plus the JSON Schema stated in the system instruction (the gateway validates the result).
These providers do not implement function calling or search, so their capabilities say so and the router
never sends them requests that need those features.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any

import httpx

from aegis_api.lab.llm.base import ProviderCapabilities
from aegis_api.lab.llm.errors import (
    LLMError,
    LLMPermanentError,
    LLMPolicyError,
    LLMTransientError,
    LLMUnavailableError,
    LLMValidationError,
)
from aegis_api.lab.llm.providers._http import build_client, map_http_error, map_transport_error
from aegis_api.lab.llm.schemas import LLMRequest, LLMResponse, StreamChunk, Usage
from engines.providers.base import (
    GenerationRequest,
    Message,
    ModelProvider,
    ProviderError,
    ProviderUnavailable,
)

SCHEMA_INSTRUCTION = (
    "Respond with a single JSON object that conforms to the following JSON Schema. Return only the JSON "
    "object, with no prose and no code fences.\nJSON Schema:\n{schema}"
)
FINISH_MAP = {
    "stop": "stop",
    "end_turn": "stop",
    "stop_sequence": "stop",
    "length": "max_tokens",
    "max_tokens": "max_tokens",
}
POLICY_FINISH = frozenset({"content_filter", "refusal", "safety"})


class SingleAttemptPost:
    """Mixin for ``engines.providers.base.ModelProvider`` subclasses: one request, typed errors."""

    kind: str
    client: httpx.Client

    def _post(self, url: str, *, json: dict[str, Any], headers: dict[str, str] | None = None) -> httpx.Response:
        try:
            response = self.client.post(url, json=json, headers=headers)
        except httpx.HTTPError as exc:
            raise map_transport_error(self.kind, exc) from None
        if response.status_code >= 300:
            raise map_http_error(self.kind, response, secret=getattr(self, "api_key", None))
        return response


def to_generation_request(request: LLMRequest, *, model: str, default_max_tokens: int) -> GenerationRequest:
    if request.previous_turns:
        raise LLMValidationError(
            "This provider cannot replay provider-native turns (function calling is not supported)",
            code="llm_unsupported_feature",
        )
    system_parts = [request.system] if request.system else []
    messages: list[Message] = []
    for message in request.messages:
        if message.role == "system":
            system_parts.append(message.content)
        elif message.role == "tool":
            label = message.name or message.tool_call_id or "tool"
            messages.append(Message(role="user", content=f"[tool result: {label}]\n{message.content}"))
        else:
            messages.append(Message(role=message.role, content=message.content))
    schema = request.effective_response_schema()
    if schema is not None:
        system_parts.append(SCHEMA_INSTRUCTION.format(schema=json.dumps(schema, sort_keys=True)))
    return GenerationRequest(
        messages=messages,
        system="\n\n".join(p for p in system_parts if p) or None,
        model=model,
        temperature=request.temperature if request.temperature is not None else 0.0,
        max_tokens=request.max_output_tokens or default_max_tokens,
        seed=request.seed,
        json_mode=schema is not None,
    )


def _map_engine_error(kind: str, exc: ProviderError) -> LLMError:
    if isinstance(exc, ProviderUnavailable):
        return LLMUnavailableError(f"{kind} is unreachable", code="llm_unreachable", provider=kind)
    status = exc.status or 0
    if status == 429 or status >= 500:
        return LLMTransientError(f"{kind} HTTP {status}", code="llm_provider_unavailable", provider=kind, status=status)
    if status in (401, 403):
        return LLMPermanentError(
            f"{kind} rejected the credentials", code="llm_auth_failed", provider=kind, status=status
        )
    if status == 404:
        return LLMPermanentError(f"{kind} HTTP 404", code="llm_model_not_found", provider=kind, status=status)
    return LLMValidationError(f"{kind} HTTP {status}", code="llm_invalid_request", provider=kind, status=status)


class EngineChatProvider:
    """Wraps an engine ``ModelProvider`` (constructed with a single-attempt ``_post``)."""

    kind: str = "engine"
    capabilities: ProviderCapabilities = ProviderCapabilities()

    def __init__(self, engine: ModelProvider, *, default_max_tokens: int) -> None:
        self._engine = engine
        self._default_max_tokens = default_max_tokens

    def __repr__(self) -> str:
        return f"{type(self).__name__}(kind={self.kind!r})"

    @staticmethod
    def engine_client(timeout: float, transport: httpx.BaseTransport | None) -> httpx.Client:
        return build_client(timeout, transport)

    def close(self) -> None:
        self._engine.close()

    def _finish(self, reason: str | None) -> str | None:
        if reason is None:
            return None
        if reason in POLICY_FINISH:
            raise LLMPolicyError(
                f"The provider withheld the response ({reason})", code="llm_response_blocked", provider=self.kind
            )
        return FINISH_MAP.get(reason, reason)

    def generate(self, request: LLMRequest, *, model: str) -> LLMResponse:
        gen = to_generation_request(request, model=model, default_max_tokens=self._default_max_tokens)
        started = time.perf_counter()
        try:
            result = self._engine.generate(gen)
        except ProviderError as exc:
            raise _map_engine_error(self.kind, exc) from None
        except (ValueError, KeyError) as exc:  # malformed provider JSON
            raise LLMTransientError(
                f"{self.kind} returned an unexpected response ({type(exc).__name__})",
                code="llm_bad_response",
                provider=self.kind,
            ) from None
        return LLMResponse(
            text=result.text,
            usage=Usage(input_tokens=result.input_tokens or 0, output_tokens=result.output_tokens or 0),
            provider=self.kind,
            model=model,
            model_version=result.model if result.model != model else None,
            latency_ms=int((time.perf_counter() - started) * 1000),
            finish_reason=self._finish(result.finish_reason),
            raw_assistant_turn={"role": "assistant", "content": result.text},
        )

    def stream(self, request: LLMRequest, *, model: str) -> Iterator[StreamChunk]:
        gen = to_generation_request(request, model=model, default_max_tokens=self._default_max_tokens)
        try:
            for piece in self._engine.stream(gen):
                if piece:
                    yield StreamChunk(text_delta=piece, provider=self.kind, model=model)
        except ProviderError as exc:
            raise _map_engine_error(self.kind, exc) from None
        except httpx.HTTPError as exc:
            raise map_transport_error(self.kind, exc) from None
        # The engine streams do not surface token usage; the final chunk reports none (cost unknown).
        yield StreamChunk(done=True, usage=Usage(), provider=self.kind, model=model)
