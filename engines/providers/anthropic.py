"""Anthropic provider (Messages REST API)."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any

import httpx

from engines.providers.base import (
    GenerationRequest,
    GenerationResult,
    HealthStatus,
    ModelProvider,
    ProviderError,
    ProviderMetadata,
    ProviderUnavailable,
    elapsed_ms,
)

ANTHROPIC_VERSION = "2023-06-01"


class AnthropicProvider(ModelProvider):
    kind = "anthropic"

    def __init__(self, api_key: str, base_url: str = "https://api.anthropic.com/v1", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if not api_key:
            raise ProviderUnavailable("Anthropic API key is not configured")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    @property
    def _headers(self) -> dict[str, str]:
        return {"x-api-key": self.api_key, "anthropic-version": ANTHROPIC_VERSION}

    def _payload(self, request: GenerationRequest, stream: bool = False) -> dict[str, Any]:
        system_parts = [m.content for m in request.messages if m.role == "system"]
        if request.system:
            system_parts.insert(0, request.system)
        payload: dict[str, Any] = {
            "model": self._model(request),
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "messages": [m.model_dump() for m in request.messages if m.role != "system"],
            "stream": stream,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        return payload

    def generate(self, request: GenerationRequest) -> GenerationResult:
        started = time.perf_counter()
        response = self._post(f"{self.base_url}/messages", json=self._payload(request), headers=self._headers)
        data = response.json()
        text = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
        usage = data.get("usage") or {}
        return GenerationResult(
            text=text,
            provider=self.kind,
            model=data.get("model", self._model(request)),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            latency_ms=elapsed_ms(started),
            finish_reason=data.get("stop_reason"),
        )

    def stream(self, request: GenerationRequest) -> Iterator[str]:
        try:
            with self.client.stream(
                "POST", f"{self.base_url}/messages", json=self._payload(request, True), headers=self._headers
            ) as response:
                if response.status_code >= 400:
                    raise ProviderError(f"anthropic returned HTTP {response.status_code}", status=response.status_code)
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    event = json.loads(line[5:].strip())
                    if event.get("type") == "content_block_delta":
                        text = (event.get("delta") or {}).get("text")
                        if text:
                            yield text
                    elif event.get("type") == "message_stop":
                        break
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise ProviderUnavailable("anthropic is unreachable") from exc

    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            kind=self.kind,
            name="Anthropic",
            default_model=self.default_model,
            supports_streaming=True,
            supports_embeddings=False,
            local=False,
            data_leaves_organization=True,
            notes="Prompts and outputs are sent to Anthropic. Requires explicit data-processing consent.",
        )

    def health_check(self) -> HealthStatus:
        started = time.perf_counter()
        try:
            response = self.client.get(f"{self.base_url}/models", headers=self._headers, timeout=8)
        except httpx.HTTPError as exc:
            return HealthStatus(ok=False, detail=f"Anthropic unreachable: {exc.__class__.__name__}")
        if response.status_code in (401, 403):
            return HealthStatus(ok=False, detail="Anthropic rejected the API key")
        if response.status_code != 200:
            return HealthStatus(ok=False, detail=f"Anthropic returned HTTP {response.status_code}")
        models = [m.get("id", "") for m in response.json().get("data", [])]
        return HealthStatus(
            ok=True, detail="Connected to Anthropic API.", models=models, latency_ms=elapsed_ms(started)
        )
