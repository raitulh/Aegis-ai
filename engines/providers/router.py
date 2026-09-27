"""Model router: lightweight tasks → local model (Ollama/Qwen); heavy tasks → configured external provider.

The router is only ever used for *model-assisted* judgments. Every caller must have a deterministic
fallback: when no model is available, ``judge()`` returns ``None`` and the audit records that the
model-assisted step was skipped (it is never silently marked as passed).
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from engines.evaluation.base import ModelJudgment
from engines.providers.base import GenerationRequest, GenerationResult, ModelProvider, ProviderError

LIGHTWEIGHT_TASKS = frozenset({"classification", "claim_extraction", "summarization"})
HEAVY_TASKS = frozenset({"policy_extraction", "evaluation", "remediation"})


@dataclass
class RouteDecision:
    provider: ModelProvider | None
    model: str | None
    reason: str


@dataclass
class ModelRouter:
    local: ModelProvider | None = None
    external: ModelProvider | None = None
    external_allowed: bool = False  # organization consented to send data to the external provider
    preferred: str | None = None  # "local" | "external" override for evaluator model
    on_call: Callable[[str, GenerationResult | None, str | None], None] | None = None
    _health_cache: dict[str, tuple[float, bool]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _available(self, provider: ModelProvider | None) -> bool:
        if provider is None:
            return False
        key = f"{provider.kind}:{id(provider)}"
        now = time.time()
        with self._lock:
            cached = self._health_cache.get(key)
            if cached and now - cached[0] < 60:
                return cached[1]
        ok = provider.health_check().ok
        with self._lock:
            self._health_cache[key] = (now, ok)
        return ok

    def route(self, task: str) -> RouteDecision:
        external_ok = self.external is not None and self.external_allowed
        order: list[tuple[ModelProvider | None, str]]
        if self.preferred == "external" or (task in HEAVY_TASKS and self.preferred != "local"):
            order = [(self.external if external_ok else None, "external"), (self.local, "local")]
        else:
            order = [(self.local, "local"), (self.external if external_ok else None, "external")]
        for provider, label in order:
            if provider is not None and self._available(provider):
                return RouteDecision(provider, provider.default_model, f"{task} → {label} ({provider.kind})")
        return RouteDecision(None, None, f"{task}: no model provider available")

    def available(self) -> bool:
        return self.route("classification").provider is not None

    def complete(
        self, task: str, prompt: str, *, system: str | None = None, json_mode: bool = False
    ) -> GenerationResult | None:
        decision = self.route(task)
        if decision.provider is None:
            return None
        try:
            result = decision.provider.generate(
                GenerationRequest.simple(prompt, system=system, temperature=0.0, max_tokens=800, json_mode=json_mode)
            )
        except ProviderError as exc:
            if self.on_call:
                self.on_call(task, None, str(exc))
            return None
        if self.on_call:
            self.on_call(task, result, None)
        return result

    def judge(self, task: str, prompt: str, *, prompt_version: str, system: str | None = None) -> ModelJudgment | None:
        """Ask a model for a JSON verdict. Returns None when no model is available or output is invalid."""
        result = self.complete(task, prompt, system=system, json_mode=True)
        if result is None:
            return None
        parsed = parse_json_object(result.text)
        if parsed is None:
            return None
        confidence = parsed.get("confidence")
        return ModelJudgment(
            evaluator_model=f"{result.provider}:{result.model}",
            prompt_version=prompt_version,
            confidence=float(confidence) if isinstance(confidence, int | float) else None,
            raw={"text": result.text[:4000]},
            normalized=parsed,
        )


def parse_json_object(text: str) -> dict[str, Any] | None:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None
