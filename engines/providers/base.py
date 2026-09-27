"""Model provider abstraction: ``generate()``, ``stream()``, ``embed()``, ``metadata()``."""

from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any

import httpx
from pydantic import BaseModel, Field


class Message(BaseModel):
    role: str  # system | user | assistant
    content: str


class GenerationRequest(BaseModel):
    messages: list[Message]
    system: str | None = None
    model: str | None = None
    temperature: float = 0.0
    max_tokens: int = 1024
    seed: int | None = None
    json_mode: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def simple(cls, prompt: str, *, system: str | None = None, **kwargs: Any) -> GenerationRequest:
        return cls(messages=[Message(role="user", content=prompt)], system=system, **kwargs)


class GenerationResult(BaseModel):
    text: str
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int
    finish_reason: str | None = None
    raw_metadata: dict[str, Any] = Field(default_factory=dict)


class ProviderMetadata(BaseModel):
    kind: str
    name: str
    default_model: str | None
    supports_streaming: bool
    supports_embeddings: bool
    local: bool
    data_leaves_organization: bool
    notes: str | None = None


class HealthStatus(BaseModel):
    ok: bool
    detail: str
    models: list[str] = Field(default_factory=list)
    latency_ms: int | None = None


class ProviderError(Exception):
    """A provider returned an error response."""

    def __init__(self, message: str, *, status: int | None = None, retryable: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class ProviderUnavailable(ProviderError):
    """The provider could not be reached (network error, not running, not configured)."""


class ProviderNotSupported(ProviderError):
    """The operation is not supported by this provider."""


_THINK_BLOCK = re.compile(r"<think>.*?</think>\s*", re.S | re.I)


def strip_reasoning(text: str) -> str:
    """Remove model reasoning blocks. Aegis never stores chain-of-thought, only observable output."""
    return _THINK_BLOCK.sub("", text).strip()


class ModelProvider(ABC):
    kind: str = "base"

    def __init__(self, *, default_model: str | None = None, timeout: float = 60.0, client: httpx.Client | None = None):
        self.default_model = default_model
        self.timeout = timeout
        self._client = client

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout, follow_redirects=False)
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    @abstractmethod
    def generate(self, request: GenerationRequest) -> GenerationResult: ...

    def stream(self, request: GenerationRequest) -> Iterator[str]:
        """Default: non-streaming fallback yielding a single chunk."""
        yield self.generate(request).text

    def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        raise ProviderNotSupported(f"{self.kind} does not support embeddings")

    @abstractmethod
    def metadata(self) -> ProviderMetadata: ...

    @abstractmethod
    def health_check(self) -> HealthStatus: ...

    # -- helpers ---------------------------------------------------------------------------------
    def _model(self, request: GenerationRequest) -> str:
        model = request.model or self.default_model
        if not model:
            raise ProviderError(f"No model configured for provider {self.kind}")
        return model

    def _post(self, url: str, *, json: dict[str, Any], headers: dict[str, str] | None = None) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                response = self.client.post(url, json=json, headers=headers)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                raise ProviderUnavailable(f"{self.kind} is unreachable: {exc.__class__.__name__}") from exc
            except httpx.TimeoutException as exc:
                last_exc = exc
                time.sleep(0.5 * (attempt + 1))
                continue
            if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(0.75 * (attempt + 1))
                continue
            if response.status_code in (401, 403):
                raise ProviderError(f"{self.kind} rejected the credentials", status=response.status_code)
            if response.status_code >= 400:
                raise ProviderError(
                    f"{self.kind} returned HTTP {response.status_code}: {response.text[:300]}",
                    status=response.status_code,
                    retryable=response.status_code >= 500,
                )
            return response
        raise ProviderUnavailable(f"{self.kind} timed out") from last_exc


def elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
