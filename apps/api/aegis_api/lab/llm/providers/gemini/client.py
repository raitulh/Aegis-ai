"""Gemini ``generateContent`` / ``streamGenerateContent`` adapter (sync httpx, one request per call).

The API key travels only in the ``x-goog-api-key`` header (never in URLs, logs or error messages).
Retries, fallbacks, cost and usage recording are the gateway's job.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator
from typing import Any

import httpx

from aegis_api.lab.llm.base import GEMINI_CAPABILITIES, ProviderCapabilities
from aegis_api.lab.llm.errors import LLMTransientError, LLMUnavailableError, LLMValidationError
from aegis_api.lab.llm.providers._http import (
    build_client,
    iter_sse,
    map_transport_error,
    raise_for_stream_status,
    raw_json_error,
    request_json,
)
from aegis_api.lab.llm.providers.gemini.parsing import (
    build_generate_body,
    check_blocked,
    normalize_finish,
    parse_generate_response,
    parse_usage,
    text_of,
)
from aegis_api.lab.llm.schemas import LLMRequest, LLMResponse, StreamChunk

_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,159}$")


def model_path(model: str) -> str:
    name = model.removeprefix("models/")
    if not _MODEL_ID.match(name):
        raise LLMValidationError("Invalid Gemini model id", code="llm_invalid_model", provider="gemini")
    return f"models/{name}"


class GeminiClient:
    kind = "gemini"
    capabilities: ProviderCapabilities = GEMINI_CAPABILITIES

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str,
        timeout: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise LLMUnavailableError("Gemini API key is not configured", code="llm_not_configured", provider="gemini")
        self._api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._client = build_client(timeout, transport)

    def __repr__(self) -> str:  # never expose the key
        return f"GeminiClient(base_url={self.base_url!r})"

    @property
    def _headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self._api_key, "content-type": "application/json"}

    def close(self) -> None:
        self._client.close()

    def generate(self, request: LLMRequest, *, model: str) -> LLMResponse:
        body = build_generate_body(request, response_schema=request.effective_response_schema())
        started = time.perf_counter()
        data = request_json(
            self._client,
            "POST",
            f"{self.base_url}/{model_path(model)}:generateContent",
            provider=self.kind,
            headers=self._headers,
            body=body,
            secret=self._api_key,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        parsed = parse_generate_response(data)
        return LLMResponse(
            text=parsed.text,
            tool_calls=parsed.tool_calls,
            citations=parsed.citations,
            usage=parsed.usage,
            provider=self.kind,
            model=model,
            model_version=parsed.model_version,
            request_id=parsed.request_id,
            latency_ms=latency_ms,
            finish_reason=parsed.finish_reason,
            raw_assistant_turn=parsed.raw_assistant_turn,
            metadata=parsed.metadata,
        )

    def stream(self, request: LLMRequest, *, model: str) -> Iterator[StreamChunk]:
        body = build_generate_body(request, response_schema=request.effective_response_schema())
        url = f"{self.base_url}/{model_path(model)}:streamGenerateContent"
        last: dict[str, Any] = {}
        finish: str | None = None
        try:
            with self._client.stream("POST", url, params={"alt": "sse"}, json=body, headers=self._headers) as response:
                raise_for_stream_status(self.kind, response, secret=self._api_key)
                for event in iter_sse(response.iter_lines()):
                    if event.raw:
                        error = raw_json_error(self.kind, event.data, secret=self._api_key)
                        if error is not None:
                            raise error
                        continue
                    try:
                        data = json.loads(event.data)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(data, dict) and isinstance(data.get("error"), dict):
                        error = raw_json_error(self.kind, event.data, secret=self._api_key)
                        raise error or LLMTransientError("Gemini stream error", provider=self.kind)
                    if not isinstance(data, dict):
                        continue
                    if data.get("candidates") or data.get("promptFeedback"):
                        candidate = check_blocked(data)
                        finish = candidate.get("finishReason") or finish
                        delta = text_of(candidate)
                        if delta:
                            yield StreamChunk(text_delta=delta, provider=self.kind, model=model)
                    for key in ("usageMetadata", "modelVersion", "responseId"):
                        if data.get(key):
                            last[key] = data[key]
        except httpx.HTTPError as exc:
            raise map_transport_error(self.kind, exc) from None
        yield StreamChunk(
            done=True,
            usage=parse_usage(last.get("usageMetadata")),
            finish_reason=normalize_finish(finish, False),
            provider=self.kind,
            model=model,
            model_version=last.get("modelVersion"),
            request_id=last.get("responseId"),
        )
