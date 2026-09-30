"""LLM provider contract and error taxonomy.

Errors are classified so callers never blindly retry: transient/timeout/rate-limit/unavailable errors are
retryable with bounded exponential backoff; auth, invalid-request, quota and policy (safety block) errors are not.
"""

from __future__ import annotations

import random
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from enum import StrEnum

from aegis_api.infrastructure.llm.schemas import InteractionEvent, InteractionSnapshot, LLMRequest, LLMResponse


class LLMErrorKind(StrEnum):
    TRANSIENT = "transient"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"
    QUOTA = "quota"
    AUTH = "auth"
    INVALID_REQUEST = "invalid_request"
    POLICY = "policy"
    NOT_SUPPORTED = "not_supported"
    OUTPUT_INVALID = "output_invalid"


RETRYABLE = frozenset(
    {LLMErrorKind.TRANSIENT, LLMErrorKind.TIMEOUT, LLMErrorKind.RATE_LIMITED, LLMErrorKind.UNAVAILABLE}
)


class LLMError(Exception):
    def __init__(
        self,
        message: str,
        *,
        kind: LLMErrorKind,
        provider: str,
        status: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.provider = provider
        self.status = status
        self.retry_after = retry_after

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE

    @property
    def code(self) -> str:
        return f"llm_{self.kind.value}"


def classify_status(status: int) -> LLMErrorKind:
    if status == 429:
        return LLMErrorKind.RATE_LIMITED
    if status in (401, 403):
        return LLMErrorKind.AUTH
    if status == 402:
        return LLMErrorKind.QUOTA
    if status in (408, 504):
        return LLMErrorKind.TIMEOUT
    if status >= 500:
        return LLMErrorKind.TRANSIENT
    return LLMErrorKind.INVALID_REQUEST


def call_with_retry[T](
    fn: Callable[[], T],
    *,
    max_retries: int,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
) -> tuple[T, int]:
    """Bounded exponential backoff with full jitter; only retryable ``LLMError`` kinds are retried."""
    rng = rng or random.Random()
    attempt = 0
    while True:
        try:
            return fn(), attempt
        except LLMError as exc:
            if not exc.retryable or attempt >= max_retries:
                raise
            delay = min(max_delay, base_delay * (2**attempt))
            delay = rng.uniform(0, delay)
            if exc.retry_after is not None:
                delay = max(delay, min(exc.retry_after, max_delay * 4))
            sleep(delay)
            attempt += 1


class LLMProvider(ABC):
    name: str = "base"
    data_leaves_organization: bool = True
    features: frozenset[str] = frozenset()

    @abstractmethod
    def generate(self, request: LLMRequest, *, model: str) -> LLMResponse: ...

    def stream(self, request: LLMRequest, *, model: str) -> Iterator[InteractionEvent]:
        response = self.generate(request, model=model)
        yield InteractionEvent(event_type="text", data={"text": response.text})
        yield InteractionEvent(event_type="completed", data={"usage": response.usage.model_dump()})

    def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        raise LLMError(f"{self.name} does not support embeddings", kind=LLMErrorKind.NOT_SUPPORTED, provider=self.name)

    @abstractmethod
    def health(self) -> tuple[bool, str]: ...


class AgentInteractionProvider(ABC):
    """Background agent interactions (e.g. Gemini Deep Research via the Interactions API)."""

    @abstractmethod
    def start_agent(
        self,
        *,
        agent: str,
        input_text: str,
        agent_config: dict[str, object] | None = None,
        tools: list[dict[str, object]] | None = None,
        system: str | None = None,
    ) -> InteractionSnapshot: ...

    @abstractmethod
    def get_interaction(self, interaction_id: str) -> InteractionSnapshot: ...

    @abstractmethod
    def stream_interaction(
        self, interaction_id: str, *, last_event_id: str | None = None
    ) -> Iterator[InteractionEvent]: ...

    @abstractmethod
    def cancel_interaction(self, interaction_id: str) -> None: ...
