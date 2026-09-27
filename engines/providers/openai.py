"""OpenAI provider (Chat Completions + Embeddings REST API)."""

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


class OpenAIProvider(ModelProvider):
    kind = "openai"

    def __init__(self, api_key: str, base_url: str = "https://api.openai.com/v1", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if not api_key:
            raise ProviderUnavailable("OpenAI API key is not configured")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    @property
    def _headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self.api_key}"}

    def _payload(self, request: GenerationRequest, stream: bool = False) -> dict[str, Any]:
        messages = [m.model_dump() for m in request.messages]
        if request.system:
            messages.insert(0, {"role": "system", "content": request.system})
        payload: dict[str, Any] = {
            "model": self._model(request),
            "messages": messages,
            "temperature": request.temperature,
            "max_completion_tokens": request.max_tokens,
            "stream": stream,
        }
        if request.seed is not None:
            payload["seed"] = request.seed
        if request.json_mode:
            payload["response_format"] = {"type": "json_object"}
        return payload

    def generate(self, request: GenerationRequest) -> GenerationResult:
        started = time.perf_counter()
        response = self._post(f"{self.base_url}/chat/completions", json=self._payload(request), headers=self._headers)
        data = response.json()
        choice = (data.get("choices") or [{}])[0]
        usage = data.get("usage") or {}
        return GenerationResult(
            text=(choice.get("message") or {}).get("content") or "",
            provider=self.kind,
            model=data.get("model", self._model(request)),
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            latency_ms=elapsed_ms(started),
            finish_reason=choice.get("finish_reason"),
        )

    def stream(self, request: GenerationRequest) -> Iterator[str]:
        try:
            with self.client.stream(
                "POST", f"{self.base_url}/chat/completions", json=self._payload(request, True), headers=self._headers
            ) as response:
                if response.status_code >= 400:
                    raise ProviderError(f"openai returned HTTP {response.status_code}", status=response.status_code)
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    body = line[5:].strip()
                    if body == "[DONE]":
                        break
                    delta = (json.loads(body).get("choices") or [{}])[0].get("delta", {}).get("content")
                    if delta:
                        yield delta
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise ProviderUnavailable("openai is unreachable") from exc

    def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        response = self._post(
            f"{self.base_url}/embeddings",
            json={"model": model or "text-embedding-3-small", "input": texts},
            headers=self._headers,
        )
        rows = sorted(response.json().get("data", []), key=lambda r: r.get("index", 0))
        return [list(map(float, r.get("embedding", []))) for r in rows]

    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            kind=self.kind,
            name="OpenAI",
            default_model=self.default_model,
            supports_streaming=True,
            supports_embeddings=True,
            local=False,
            data_leaves_organization=True,
            notes="Prompts and outputs are sent to OpenAI. Requires explicit data-processing consent.",
        )

    def health_check(self) -> HealthStatus:
        started = time.perf_counter()
        try:
            response = self.client.get(f"{self.base_url}/models", headers=self._headers, timeout=8)
        except httpx.HTTPError as exc:
            return HealthStatus(ok=False, detail=f"OpenAI unreachable: {exc.__class__.__name__}")
        if response.status_code in (401, 403):
            return HealthStatus(ok=False, detail="OpenAI rejected the API key")
        if response.status_code != 200:
            return HealthStatus(ok=False, detail=f"OpenAI returned HTTP {response.status_code}")
        models = [m.get("id", "") for m in response.json().get("data", [])][:50]
        return HealthStatus(ok=True, detail="Connected to OpenAI API.", models=models, latency_ms=elapsed_ms(started))
