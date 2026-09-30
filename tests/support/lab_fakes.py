"""Test doubles for the lab (tests only): a scripted LLM provider and a registry factory.

The scripted provider answers each agent role with schema-valid JSON so the full mission pipeline can run
deterministically without network access. It records every request for assertions (e.g. that untrusted
content is fenced and tools are offered only to roles allowed to use them).
"""

from __future__ import annotations

import json
from typing import Any

from aegis_api.infrastructure.llm.base import LLMProvider
from aegis_api.infrastructure.llm.router import ProviderRegistry
from aegis_api.infrastructure.llm.schemas import LLMRequest, LLMResponse, ToolCallRequest, Usage
from engines.lab.enums import ModelTier
from engines.lab.routing import Feature, ModelCandidate

ANNEAL = {"method": "anneal", "evaluations": 2000, "dim": 5, "step": 0.5, "temperature": 10.0, "restarts": 4}


def _role(request: LLMRequest) -> str:
    return str(request.metadata.get("role") or request.task_type)


class ScriptedProvider(LLMProvider):
    name = "scripted"
    data_leaves_organization = False
    features = frozenset({Feature.STRUCTURED_OUTPUT, Feature.TOOLS})

    def __init__(self, overrides: dict[str, Any] | None = None) -> None:
        self.requests: list[LLMRequest] = []
        self.overrides = overrides or {}

    def health(self) -> tuple[bool, str]:
        return True, "scripted"

    def generate(self, request: LLMRequest, *, model: str) -> LLMResponse:
        self.requests.append(request)
        role = _role(request)
        if role in self.overrides:
            value = self.overrides[role]
            payload = value(request) if callable(value) else value
        else:
            payload = self._answer(role, request)
        if isinstance(payload, ToolCallRequest):
            return LLMResponse(
                text="", tool_calls=[payload], provider=self.name, model=model,
                usage=Usage(input_tokens=100, output_tokens=10),
            )
        text = json.dumps(payload)
        return LLMResponse(
            text=text, parsed=payload, provider=self.name, model=model, usage=Usage(input_tokens=200, output_tokens=120)
        )

    def _answer(self, role: str, request: LLMRequest) -> dict[str, Any]:
        if role == "quest":
            return {"restated_objective": "Beat random search on Rastrigin", "measurable_success_criteria": ["≥10% lower value"]}
        if role == "planner":
            return {"summary": "Pilot comparison then verification", "phases": [{"name": "experiments", "goal": "compare"}],
                    "search_queries": ["simulated annealing rastrigin"], "hypothesis_directions": ["annealing"]}
        if role == "literature":
            return {"queries": ["q"], "findings": [], "gaps": [], "contradictions": []}
        if role == "hypothesis":
            return {"hypotheses": [
                {"statement": "Simulated annealing with 4 restarts reaches lower Rastrigin values than random search",
                 "rationale": "Local refinement exploits structure", "expected_outcome": "lower objective_value",
                 "measurable_prediction": {"metric": "objective_value", "direction": "decrease", "magnitude": 0.2},
                 "feasibility": 0.9, "confidence": 0.6, "parameters": ANNEAL},
                {"statement": "Very high temperature annealing behaves like random search on Rastrigin",
                 "rationale": "High temperature accepts almost everything", "expected_outcome": "no improvement",
                 "measurable_prediction": {"metric": "objective_value", "direction": "no_change"},
                 "feasibility": 0.8, "confidence": 0.4, "parameters": {**ANNEAL, "temperature": 49.0}},
            ]}
        if role == "hypothesis_critic":
            return {"critiques": [
                {"hypothesis_index": 0, "falsifiable": True, "novelty": 0.3, "feasibility": 0.9, "risk": 0.1,
                 "score": 0.8, "recommendation": "select"},
                {"hypothesis_index": 1, "falsifiable": True, "novelty": 0.2, "feasibility": 0.8, "risk": 0.1,
                 "score": 0.4, "recommendation": "revise"},
            ]}
        if role == "statistical_analyst":
            return {"interpretation": "The difference is consistent across seeds.", "caveats": ["single function"]}
        if role == "failure_analyzer":
            return {"root_cause": "see classification", "confidence": 0.5, "lesson": "check inputs"}
        if role == "verifier":
            return {"assessment": "supports", "reasons": ["reproduced"], "confidence": 0.7}
        if role == "report":
            return {"executive_summary": "The mission compared two optimizers.", "sections": {}, "cited_evidence_ids": []}
        if role == "scientific_reviewer":
            return {"checks": [], "overall": "acceptable", "overclaiming_flags": []}
        raise AssertionError(f"no scripted answer for role {role}")


def registry_with(provider: ScriptedProvider) -> Any:
    candidates = [
        ModelCandidate("scripted", f"scripted-{tier.value}", tier, provider.features, 0.1, 0.4, 50, False)
        for tier in (ModelTier.FAST, ModelTier.DEFAULT, ModelTier.REASONING)
    ]

    def factory(_overrides: Any = ()) -> ProviderRegistry:
        return ProviderRegistry(providers={"scripted": provider}, candidates=candidates)

    return factory
