"""Ollama provider (local models, e.g. ``qwen3:1.7b``). Data never leaves the host running Ollama."""

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
    strip_reasoning,
)


class OllamaProvider(ModelProvider):
    kind = "ollama"

    def __init__(self, base_url: str = "http://localhost:11434", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.base_url = base_url.rstrip("/")

    def _payload(self, request: GenerationRequest, stream: bool) -> dict[str, Any]:
        messages = [m.model_dump() for m in request.messages]
        if request.system:
            messages.insert(0, {"role": "system", "content": request.system})
        options: dict[str, Any] = {"temperature": request.temperature, "num_predict": request.max_tokens}
        if request.seed is not None:
            options["seed"] = request.seed
        payload: dict[str, Any] = {
            "model": self._model(request),
            "messages": messages,
            "stream": stream,
            "options": options,
            # Reasoning models (e.g. Qwen3) — disable thinking; Aegis never stores chain-of-thought.
            "think": False,
        }
        if request.json_mode:
            payload["format"] = "json"
        return payload

    def generate(self, request: GenerationRequest) -> GenerationResult:
        started = time.perf_counter()
        response = self._post(f"{self.base_url}/api/chat", json=self._payload(request, stream=False))
        data = response.json()
        text = strip_reasoning((data.get("message") or {}).get("content", ""))
        return GenerationResult(
            text=text,
            provider=self.kind,
            model=data.get("model", self._model(request)),
            input_tokens=data.get("prompt_eval_count"),
            output_tokens=data.get("eval_count"),
            latency_ms=elapsed_ms(started),
            finish_reason=data.get("done_reason"),
            raw_metadata={k: data.get(k) for k in ("total_duration", "load_duration", "eval_duration")},
        )

    def stream(self, request: GenerationRequest) -> Iterator[str]:
        try:
            with self.client.stream(
                "POST", f"{self.base_url}/api/chat", json=self._payload(request, stream=True)
            ) as response:
                if response.status_code >= 400:
                    raise ProviderError(f"ollama returned HTTP {response.status_code}", status=response.status_code)
                in_think = False
                for line in response.iter_lines():
                    if not line:
                        continue
                    chunk = json.loads(line)
                    piece = (chunk.get("message") or {}).get("content", "")
                    if "<think>" in piece:
                        in_think = True
                    if not in_think and piece:
                        yield piece
                    if "</think>" in piece:
                        in_think = False
                    if chunk.get("done"):
                        break
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise ProviderUnavailable("ollama is unreachable") from exc

    def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        response = self._post(f"{self.base_url}/api/embed", json={"model": model or "nomic-embed-text", "input": texts})
        return [list(map(float, v)) for v in response.json().get("embeddings", [])]

    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            kind=self.kind,
            name="Ollama (local)",
            default_model=self.default_model,
            supports_streaming=True,
            supports_embeddings=True,
            local=True,
            data_leaves_organization=False,
            notes="Runs models on infrastructure you control.",
        )

    def health_check(self) -> HealthStatus:
        started = time.perf_counter()
        try:
            response = self.client.get(f"{self.base_url}/api/tags", timeout=5)
        except httpx.HTTPError as exc:
            return HealthStatus(ok=False, detail=f"Ollama unreachable at {self.base_url}: {exc.__class__.__name__}")
        if response.status_code != 200:
            return HealthStatus(ok=False, detail=f"Ollama returned HTTP {response.status_code}")
        models = [m.get("name", "") for m in response.json().get("models", [])]
        detail = f"Connected. {len(models)} model(s) available."
        if self.default_model and self.default_model not in models:
            detail += f" Model '{self.default_model}' is not pulled (run: ollama pull {self.default_model})."
        return HealthStatus(ok=True, detail=detail, models=models, latency_ms=elapsed_ms(started))
