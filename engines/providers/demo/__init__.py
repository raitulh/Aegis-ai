"""Deterministic simulated AI systems for the demo workspace (clearly labelled as SIMULATED in the UI)."""

from __future__ import annotations

import time
from typing import Any

from engines.evaluation.base import SystemInvocation, TestInput
from engines.providers.base import (
    GenerationRequest,
    GenerationResult,
    HealthStatus,
    ModelProvider,
    ProviderError,
    ProviderMetadata,
    elapsed_ms,
)
from engines.providers.demo.common import SimulatedSystem
from engines.providers.demo.hiring import HiringAgentSimulator
from engines.providers.demo.research import ResearchAgentSimulator
from engines.providers.demo.support import SupportRAGSimulator

SIMULATORS: dict[str, type[SimulatedSystem]] = {
    HiringAgentSimulator.profile: HiringAgentSimulator,
    SupportRAGSimulator.profile: SupportRAGSimulator,
    ResearchAgentSimulator.profile: ResearchAgentSimulator,
}


def get_simulator(profile: str, config: dict[str, Any] | None = None) -> SimulatedSystem:
    cls = SIMULATORS.get(profile)
    if cls is None:
        raise ProviderError(f"Unknown demo profile '{profile}'")
    return cls(config)


class SimulatedTarget:
    """Adapter exposing a simulator through the audit target interface."""

    def __init__(self, simulator: SimulatedSystem) -> None:
        self.simulator = simulator

    def invoke(self, test_input: TestInput, repetition: int) -> SystemInvocation:
        return self.simulator.invoke(test_input, repetition)


class DemoProvider(ModelProvider):
    """ModelProvider facade over a simulator (used for connection tests and ad-hoc generation)."""

    kind = "demo"

    def __init__(self, profile: str = "hiring_agent", config: dict[str, Any] | None = None, **kwargs: Any) -> None:
        super().__init__(default_model=f"{profile}-sim", **kwargs)
        self.simulator = get_simulator(profile, config)

    def generate(self, request: GenerationRequest) -> GenerationResult:
        started = time.perf_counter()
        prompt = "\n".join(m.content for m in request.messages if m.role == "user")
        inv = self.simulator.invoke(TestInput(prompt=prompt), request.seed or 0)
        return GenerationResult(
            text=inv.output,
            provider=self.kind,
            model=self.simulator.model_name,
            input_tokens=inv.input_tokens,
            output_tokens=inv.output_tokens,
            latency_ms=elapsed_ms(started),
            finish_reason="stop",
            raw_metadata={"simulated": True},
        )

    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            kind=self.kind,
            name="Aegis demo simulator",
            default_model=self.default_model,
            supports_streaming=False,
            supports_embeddings=False,
            local=True,
            data_leaves_organization=False,
            notes="Deterministic simulated system used for the demo workspace. Not a real model.",
        )

    def health_check(self) -> HealthStatus:
        return HealthStatus(
            ok=True, detail="Simulator ready (demo mode).", models=[self.simulator.model_name], latency_ms=0
        )


__all__ = [
    "SIMULATORS",
    "DemoProvider",
    "HiringAgentSimulator",
    "ResearchAgentSimulator",
    "SimulatedTarget",
    "SupportRAGSimulator",
    "get_simulator",
]
