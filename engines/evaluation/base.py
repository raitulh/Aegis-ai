"""Core evaluation contracts: test specifications, system invocations, outcomes and the Evaluator plugin API."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from engines.common.types import ConfidenceLevel, ResultStatus, Severity

# ---------------------------------------------------------------------------------------------
# Test specification
# ---------------------------------------------------------------------------------------------


class TestInput(BaseModel):
    """One concrete input sent to the system under test."""

    __test__ = False
    model_config = ConfigDict(extra="forbid")

    variant: str = "base"
    prompt: str
    context: dict[str, Any] = Field(default_factory=dict)
    attributes: dict[str, Any] = Field(default_factory=dict)


class TestCaseSpec(BaseModel):
    """A generated (or manually defined) test. Deterministic given (generator, version, seed)."""

    __test__ = False
    model_config = ConfigDict(extra="forbid")

    key: str
    category: str
    test_type: str
    name: str
    inputs: list[TestInput]
    expected_behavior: str
    repetitions: int = 1
    control_ref: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    source: str = "generated"  # generated | counterfactual | adversarial | policy | regression | manual
    generator: str = "aegis.generator"
    generator_version: str = "1.0.0"
    seed: int | None = None
    severity_hint: str = Severity.MEDIUM
    group: str | None = None  # finding grouping hint (e.g. "gender", "email", "authority_framing")


# ---------------------------------------------------------------------------------------------
# System invocation (observable behaviour only — never chain-of-thought)
# ---------------------------------------------------------------------------------------------


class ToolCallRecord(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    approval_requested: bool = False
    approved_by_human: bool = False


class RetrievedDoc(BaseModel):
    doc_id: str
    title: str
    text: str
    url: str | None = None
    score: float | None = None


class TraceEventRecord(BaseModel):
    kind: str  # user_input | agent | llm | retrieval | tool_call | tool_result | action | guardrail | final_output
    name: str
    input: str | None = None
    output: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    duration_ms: int | None = None


class SystemInvocation(BaseModel):
    variant: str = "base"
    repetition: int = 0
    prompt: str
    output: str = ""
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    retrieved: list[RetrievedDoc] = Field(default_factory=list)
    trace: list[TraceEventRecord] = Field(default_factory=list)
    provider: str = "unknown"
    model: str | None = None
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    error: str | None = None
    simulated: bool = False
    attributes: dict[str, Any] = Field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None


# ---------------------------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------------------------


class ArtifactSpec(BaseModel):
    """Evidence produced by an evaluator. ``content`` must already be redacted/masked."""

    kind: str
    title: str
    content: dict[str, Any]
    sensitive: bool = False
    sensitive_raw: dict[str, Any] | None = None
    confidence_level: str = ConfidenceLevel.HIGH
    confidence_reasons: list[str] = Field(default_factory=list)
    source_uri: str | None = None


class ModelJudgment(BaseModel):
    evaluator_model: str
    prompt_version: str
    confidence: float | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
    normalized: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ClaimResult(BaseModel):
    text: str
    normalized: str
    status: str
    support_confidence: float
    contradiction_confidence: float
    reason: str
    span_start: int | None = None
    span_end: int | None = None
    source_title: str | None = None
    source_url: str | None = None
    source_excerpt: str | None = None
    source_hash: str | None = None
    retrieved_at: datetime | None = None
    verification_method: str = "lexical-numeric-v1"


class EvaluationOutcome(BaseModel):
    status: str = ResultStatus.PASSED
    score: float | None = None
    severity: str | None = None
    confidence: float = 1.0
    summary: str = ""
    observed: dict[str, Any] = Field(default_factory=dict)
    group: str | None = None
    judgments: list[ModelJudgment] = Field(default_factory=list)
    artifacts: list[ArtifactSpec] = Field(default_factory=list)
    claims: list[ClaimResult] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @property
    def failed(self) -> bool:
        return self.status == ResultStatus.FAILED


class EvaluatorInfo(BaseModel):
    key: str
    name: str
    version: str
    category: str
    kind: str
    prompt_version: str | None = None
    methodology: str
    limitations: str
    config: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------------------------

Retriever = Callable[[str, int], list[RetrievedDoc]]


@dataclass
class ControlSpec:
    control_id: str
    test_type: str
    domain: str
    severity: str = Severity.MEDIUM
    threshold: dict[str, Any] = field(default_factory=dict)
    condition: dict[str, Any] | None = None
    name: str = ""
    requirement: str | None = None


@dataclass
class SystemProfile:
    system_type: str = "llm"
    environment: str = "development"
    risk_tier: str = "limited"
    tools: dict[str, dict[str, Any]] = field(default_factory=dict)  # tool -> permission policy
    guardrails: dict[str, Any] = field(default_factory=dict)
    decision_schema: dict[str, Any] = field(default_factory=dict)
    protected_attributes: list[str] = field(default_factory=list)
    canary: str | None = None
    domain: str = "general"


@dataclass
class EvaluationContext:
    system: SystemProfile = field(default_factory=SystemProfile)
    controls: dict[str, ControlSpec] = field(default_factory=dict)
    retriever: Retriever | None = None
    judge: Any | None = None  # engines.providers.router.ModelRouter (optional; never the sole signal)
    seed: int = 1337
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    notes: list[str] = field(default_factory=list)

    def control(self, ref: str | None) -> ControlSpec | None:
        return self.controls.get(ref) if ref else None


# ---------------------------------------------------------------------------------------------
# Evaluator plugin interface
# ---------------------------------------------------------------------------------------------


class Evaluator(ABC):
    key: ClassVar[str]
    name: ClassVar[str]
    version: ClassVar[str]
    category: ClassVar[str]
    kind: ClassVar[str] = "deterministic"
    prompt_version: ClassVar[str | None] = None
    test_types: ClassVar[frozenset[str]] = frozenset()

    def supports(self, case: TestCaseSpec) -> bool:
        return case.test_type in self.test_types

    @abstractmethod
    def run(
        self, ctx: EvaluationContext, case: TestCaseSpec, invocations: list[SystemInvocation]
    ) -> EvaluationOutcome: ...

    @abstractmethod
    def explain(self) -> EvaluatorInfo: ...

    def info(self, methodology: str, limitations: str, **config: Any) -> EvaluatorInfo:
        return EvaluatorInfo(
            key=self.key,
            name=self.name,
            version=self.version,
            category=self.category,
            kind=self.kind,
            prompt_version=self.prompt_version,
            methodology=methodology,
            limitations=limitations,
            config=config,
        )


def error_outcome(message: str) -> EvaluationOutcome:
    return EvaluationOutcome(status=ResultStatus.ERROR, summary=message, confidence=0.0)
