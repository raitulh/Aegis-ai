"""Google Gemini provider (Generative Language REST API)."""

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

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"


class GeminiProvider(ModelProvider):
    kind = "gemini"

    def __init__(self, api_key: str, base_url: str = GEMINI_BASE, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if not api_key:
            raise ProviderUnavailable("Gemini API key is not configured")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    @property
    def _headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self.api_key, "content-type": "application/json"}

    def _payload(self, request: GenerationRequest) -> dict[str, Any]:
        contents = [
            {"role": "model" if m.role == "assistant" else "user", "parts": [{"text": m.content}]}
            for m in request.messages
            if m.role != "system"
        ]
        system_parts = [m.content for m in request.messages if m.role == "system"]
        if request.system:
            system_parts.insert(0, request.system)
        config: dict[str, Any] = {"temperature": request.temperature, "maxOutputTokens": request.max_tokens}
        if request.seed is not None:
            config["seed"] = request.seed
        if request.json_mode:
            config["responseMimeType"] = "application/json"
        payload: dict[str, Any] = {"contents": contents, "generationConfig": config}
        if system_parts:
            payload["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
        return payload

    @staticmethod
    def _text(data: dict[str, Any]) -> tuple[str, str | None]:
        candidates = data.get("candidates") or []
        if not candidates:
            feedback = data.get("promptFeedback", {})
            return "", f"blocked:{feedback.get('blockReason', 'unknown')}"
        first = candidates[0]
        parts = (first.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        return text, first.get("finishReason")

    def generate(self, request: GenerationRequest) -> GenerationResult:
        model = self._model(request)
        started = time.perf_counter()
        response = self._post(
            f"{self.base_url}/models/{model}:generateContent", json=self._payload(request), headers=self._headers
        )
        data = response.json()
        text, finish = self._text(data)
        usage = data.get("usageMetadata") or {}
        return GenerationResult(
            text=text,
            provider=self.kind,
            model=data.get("modelVersion", model),
            input_tokens=usage.get("promptTokenCount"),
            output_tokens=usage.get("candidatesTokenCount"),
            latency_ms=elapsed_ms(started),
            finish_reason=finish,
        )

    def stream(self, request: GenerationRequest) -> Iterator[str]:
        model = self._model(request)
        try:
            with self.client.stream(
                "POST",
                f"{self.base_url}/models/{model}:streamGenerateContent?alt=sse",
                json=self._payload(request),
                headers=self._headers,
            ) as response:
                if response.status_code >= 400:
                    raise ProviderError(f"gemini returned HTTP {response.status_code}", status=response.status_code)
                for line in response.iter_lines():
                    if line.startswith("data:"):
                        text, _ = self._text(json.loads(line[5:].strip()))
                        if text:
                            yield text
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise ProviderUnavailable("gemini is unreachable") from exc

    def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        model = model or "gemini-embedding-001"
        requests = [{"model": f"models/{model}", "content": {"parts": [{"text": t}]}} for t in texts]
        response = self._post(
            f"{self.base_url}/models/{model}:batchEmbedContents", json={"requests": requests}, headers=self._headers
        )
        return [list(map(float, e.get("values", []))) for e in response.json().get("embeddings", [])]

    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            kind=self.kind,
            name="Google Gemini",
            default_model=self.default_model,
            supports_streaming=True,
            supports_embeddings=True,
            local=False,
            data_leaves_organization=True,
            notes="Prompts and outputs are sent to Google. Requires explicit data-processing consent.",
        )

    def health_check(self) -> HealthStatus:
        started = time.perf_counter()
        try:
            response = self.client.get(
                f"{self.base_url}/models", params={"pageSize": 50}, headers=self._headers, timeout=8
            )
        except httpx.HTTPError as exc:
            return HealthStatus(ok=False, detail=f"Gemini unreachable: {exc.__class__.__name__}")
        if response.status_code in (400, 401, 403):
            return HealthStatus(ok=False, detail="Gemini rejected the API key")
        if response.status_code != 200:
            return HealthStatus(ok=False, detail=f"Gemini returned HTTP {response.status_code}")
        models = [m.get("name", "").removeprefix("models/") for m in response.json().get("models", [])]
        return HealthStatus(ok=True, detail="Connected to Gemini API.", models=models, latency_ms=elapsed_ms(started))
