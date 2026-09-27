"""Audit targets: how the system under test is invoked."""

from __future__ import annotations

import time
from typing import Protocol

from engines.evaluation.base import SystemInvocation, TestInput, TraceEventRecord
from engines.providers.base import GenerationRequest, ModelProvider, ProviderError, ProviderUnavailable


class Target(Protocol):
    def invoke(self, test_input: TestInput, repetition: int) -> SystemInvocation: ...


class ProviderTarget:
    """An LLM application defined by a provider + model + system instructions."""

    def __init__(
        self,
        provider: ModelProvider,
        *,
        model: str | None = None,
        system_instructions: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 700,
    ) -> None:
        self.provider = provider
        self.model = model
        self.system_instructions = system_instructions
        self.temperature = temperature
        self.max_tokens = max_tokens

    def invoke(self, test_input: TestInput, repetition: int) -> SystemInvocation:
        inv = SystemInvocation(
            variant=test_input.variant,
            repetition=repetition,
            prompt=test_input.prompt,
            provider=self.provider.kind,
            model=self.model or self.provider.default_model,
        )
        prompt = test_input.prompt
        documents = test_input.context.get("documents")
        if documents:
            joined = "\n\n".join(f"[{d.get('title', 'doc')}]\n{d.get('text', '')}" for d in documents)
            prompt = f"Context documents:\n{joined}\n\nUser: {prompt}"
        inv.trace.append(TraceEventRecord(kind="user_input", name="user_input", input=test_input.prompt[:2000]))
        started = time.perf_counter()
        try:
            result = self.provider.generate(
                GenerationRequest.simple(
                    prompt,
                    system=self.system_instructions,
                    model=self.model,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    seed=repetition,
                )
            )
        except ProviderUnavailable as exc:
            inv.error = f"provider_unavailable: {exc}"
            return inv
        except ProviderError as exc:
            inv.error = f"provider_error: {exc}"
            return inv
        inv.output = result.text
        inv.model = result.model
        inv.latency_ms = result.latency_ms or int((time.perf_counter() - started) * 1000)
        inv.input_tokens = result.input_tokens
        inv.output_tokens = result.output_tokens
        inv.trace.append(
            TraceEventRecord(
                kind="llm",
                name="generate",
                output=result.text[:2000],
                attributes={"model": result.model},
                duration_ms=result.latency_ms,
            )
        )
        inv.trace.append(TraceEventRecord(kind="final_output", name="final_output", output=result.text[:2000]))
        return inv
