"""Evaluator plugin contract: ``evaluate(experiment, artifacts, context) -> EvaluationResult``.

Evaluator versions are immutable. Each evaluator class declares ``key`` and ``version``; its *fingerprint*
is a SHA-256 over its source code and default configuration. The registry refuses to register two different
fingerprints under the same (key, version), and the application layer persists the fingerprint with every
evaluation run — so a silently edited evaluator is detected instead of quietly changing historic meaning.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from engines.lab.experiments.spec import ExperimentSpec


class EvaluationContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Metric samples keyed by metric name, one value per seed/run.
    baseline: dict[str, list[float]] = Field(default_factory=dict)
    candidate: dict[str, list[float]] = Field(default_factory=dict)
    reproduction: dict[str, list[float]] | None = None
    # Measured by the execution backend (never self-reported by the experiment code).
    resources: dict[str, Any] = Field(default_factory=dict)
    self_reported: bool = False
    config: dict[str, Any] = Field(default_factory=dict)
    seed: int = 20260930


class EvaluationResult(BaseModel):
    evaluator_key: str
    evaluator_version: str
    fingerprint: str
    kind: str
    metrics: dict[str, float] = Field(default_factory=dict)
    passed: bool | None = Field(description="True/False, or None when the evidence is inconclusive")
    confidence: float = Field(ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)

    @property
    def verdict(self) -> str:
        return "inconclusive" if self.passed is None else ("pass" if self.passed else "fail")


class Evaluator(ABC):
    key: ClassVar[str]
    version: ClassVar[str]
    kind: ClassVar[str] = "deterministic"
    description: ClassVar[str] = ""
    default_config: ClassVar[dict[str, Any]] = {}

    @classmethod
    def fingerprint(cls) -> str:
        try:
            source = inspect.getsource(cls)
        except (OSError, TypeError):  # pragma: no cover - source unavailable (frozen builds)
            source = f"{cls.__module__}.{cls.__qualname__}"
        payload = json.dumps({"source": source, "config": cls.default_config}, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()

    def config(self, context: EvaluationContext) -> dict[str, Any]:
        return {**self.default_config, **context.config}

    def result(self, **kwargs: Any) -> EvaluationResult:
        return EvaluationResult(
            evaluator_key=self.key,
            evaluator_version=self.version,
            fingerprint=self.fingerprint(),
            kind=self.kind,
            **kwargs,
        )

    @abstractmethod
    def evaluate(
        self, experiment: ExperimentSpec, artifacts: Mapping[str, bytes], context: EvaluationContext
    ) -> EvaluationResult: ...


class EvaluatorRegistry:
    def __init__(self) -> None:
        self._evaluators: dict[tuple[str, str], type[Evaluator]] = {}
        self._latest: dict[str, str] = {}

    def register(self, evaluator: type[Evaluator]) -> type[Evaluator]:
        ident = (evaluator.key, evaluator.version)
        existing = self._evaluators.get(ident)
        if existing is not None and existing.fingerprint() != evaluator.fingerprint():
            raise ValueError(
                f"Evaluator {evaluator.key}@{evaluator.version} is already registered with a different implementation; "
                "bump the version instead of editing a released evaluator"
            )
        self._evaluators[ident] = evaluator
        current = self._latest.get(evaluator.key)
        if current is None or _version_tuple(evaluator.version) > _version_tuple(current):
            self._latest[evaluator.key] = evaluator.version
        return evaluator

    def get(self, key: str, version: str | None = None) -> Evaluator:
        version = version or self._latest.get(key)
        cls = self._evaluators.get((key, version or ""))
        if cls is None:
            raise KeyError(f"Unknown evaluator {key}@{version}")
        return cls()

    def catalog(self) -> list[dict[str, Any]]:
        return [
            {
                "key": cls.key,
                "version": cls.version,
                "kind": cls.kind,
                "description": cls.description,
                "fingerprint": cls.fingerprint(),
                "default_config": cls.default_config,
            }
            for cls in sorted(self._evaluators.values(), key=lambda c: (c.key, _version_tuple(c.version)))
        ]


def _version_tuple(version: str) -> tuple[int, ...]:
    out: list[int] = []
    for part in version.split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)
