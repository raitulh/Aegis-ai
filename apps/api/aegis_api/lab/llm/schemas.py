"""Provider-agnostic LLM request/response types (the gateway contract).

Function-call round trips: when a response carries ``tool_calls``, execute them (through the tool broker)
and call the gateway again with the same ``tools`` and

* ``messages`` extended by one ``LLMMessage(role="tool", name=<call.name>, tool_call_id=<call.id>,
  content=<JSON result>)`` per call (an ``assistant`` message with ``response.text`` right before them is
  optional — it is superseded by the provider-native turn), and
* ``previous_turns`` extended by ``response.raw_assistant_turn``.

Each consecutive group of ``tool`` messages is paired, in order, with the next entry of ``previous_turns``;
providers replay that turn verbatim (Gemini requires its ``thoughtSignature`` parts to be echoed back).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aegis_api.schemas.common import ORMModel


class TaskType(StrEnum):
    CLASSIFICATION = "classification"
    EXTRACTION = "extraction"
    SUMMARIZATION = "summarization"
    PLANNING = "planning"
    RESEARCH = "research"
    HYPOTHESIS_GENERATION = "hypothesis_generation"
    HYPOTHESIS_CRITIQUE = "hypothesis_critique"
    EXPERIMENT_DESIGN = "experiment_design"
    CODING = "coding"
    RESULT_ANALYSIS = "result_analysis"
    FAILURE_ANALYSIS = "failure_analysis"
    STRATEGY_EVOLUTION = "strategy_evolution"
    SCIENTIFIC_REVIEW = "scientific_review"
    REPORT_GENERATION = "report_generation"
    VERIFICATION = "verification"


MessageRole = Literal["system", "user", "assistant", "tool"]
ToolChoice = Literal["auto", "any", "none"]
BuiltinTool = Literal["google_search", "url_context", "code_execution"]
TierName = Literal["fast", "default", "reasoning"]
Complexity = Literal["low", "medium", "high"]
ProviderKind = Literal["gemini", "openai", "anthropic", "ollama"]

_TOOL_NAME = r"^[A-Za-z_][A-Za-z0-9_.\-]{0,63}$"


class LLMMessage(BaseModel):
    role: MessageRole
    content: str
    name: str | None = None
    tool_call_id: str | None = None


class ToolSpec(BaseModel):
    name: str = Field(pattern=_TOOL_NAME)
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Citation(BaseModel):
    url: str
    title: str | None = None
    start_index: int | None = None
    end_index: int | None = None


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    thinking_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.thinking_tokens

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cached_tokens=self.cached_tokens + other.cached_tokens,
            thinking_tokens=self.thinking_tokens + other.thinking_tokens,
        )


class LLMRequest(BaseModel):
    """One model call. ``response_model`` (preferred) or ``response_schema`` requests structured output."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    task_type: TaskType
    messages: list[LLMMessage] = Field(default_factory=list)
    system: str | None = None
    response_model: type[BaseModel] | None = Field(default=None, exclude=True)
    response_schema: dict[str, Any] | None = None
    tools: list[ToolSpec] = Field(default_factory=list)
    tool_choice: ToolChoice = "auto"
    builtin_tools: list[BuiltinTool] = Field(default_factory=list)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_output_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    tier: TierName | None = None
    complexity: Complexity = "medium"
    latency_budget_ms: int | None = Field(default=None, ge=1)
    cost_budget_usd: Decimal | None = Field(default=None, ge=0)
    provider: str | None = None
    model: str | None = None
    seed: int | None = None
    previous_turns: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("builtin_tools")
    @classmethod
    def _unique_builtins(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def _check(self) -> LLMRequest:
        if not self.messages and not self.previous_turns:
            raise ValueError("an LLM request needs at least one message")
        names = [t.name for t in self.tools]
        if len(names) != len(set(names)):
            raise ValueError("tool names must be unique")
        return self

    def effective_response_schema(self) -> dict[str, Any] | None:
        """JSON Schema for structured output (``response_model`` wins over ``response_schema``)."""
        if self.response_model is not None:
            return self.response_model.model_json_schema()
        return self.response_schema

    @property
    def wants_structured_output(self) -> bool:
        return self.response_model is not None or self.response_schema is not None

    def required_capabilities(self) -> frozenset[str]:
        caps: set[str] = set()
        if self.wants_structured_output:
            caps.add("structured_output")
        if self.tools:
            caps.add("tools")
        if "google_search" in self.builtin_tools or "url_context" in self.builtin_tools:
            caps.add("search")
        if "code_execution" in self.builtin_tools:
            caps.add("code_execution")
        return frozenset(caps)

    def prompt_chars(self) -> int:
        return len(self.system or "") + sum(len(m.content) for m in self.messages)


class LLMResponse(BaseModel):
    text: str = ""
    parsed: dict[str, Any] | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    provider: str
    model: str
    model_version: str | None = None
    request_id: str | None = None
    latency_ms: int = 0
    cost_usd: Decimal | None = None
    cost_estimated: bool = True
    cost_basis: str | None = None
    finish_reason: str | None = None
    route_reason: str | None = None
    retry_count: int = 0
    raw_assistant_turn: dict[str, Any] | None = None
    # Provider extras that are not chain-of-thought, e.g. {"provider_code_execution": [...],
    # "search_queries": [...]}. Thought text is never stored here or anywhere else.
    metadata: dict[str, Any] = Field(default_factory=dict)


class StreamChunk(BaseModel):
    text_delta: str = ""
    done: bool = False
    usage: Usage | None = None
    finish_reason: str | None = None
    provider: str | None = None
    model: str | None = None
    model_version: str | None = None
    request_id: str | None = None
    cost_usd: Decimal | None = None


# --- routing -------------------------------------------------------------------------------------
class RouteOption(BaseModel):
    provider: str
    model: str
    tier: str
    priority: int
    source: str
    local: bool
    estimated_cost_usd: Decimal | None = None


class RejectedRoute(BaseModel):
    candidate: str
    reason: str


class RouteDecision(BaseModel):
    task_type: str
    tier: str
    provider: str | None = None
    model: str | None = None
    reason: str
    fallbacks: list[RouteOption] = Field(default_factory=list)
    primary: RouteOption | None = None
    rejected: list[RejectedRoute] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    external_allowed: bool = False


class RoutePreviewRequest(BaseModel):
    task_type: TaskType
    complexity: Complexity = "medium"
    tier: TierName | None = None
    latency_budget_ms: int | None = Field(default=None, ge=1)
    cost_budget_usd: Decimal | None = Field(default=None, ge=0)
    required_capabilities: list[Literal["structured_output", "tools", "search", "code_execution", "streaming"]] = Field(
        default_factory=list
    )
    provider: str | None = None
    model: str | None = None
    estimated_input_tokens: int = Field(default=1000, ge=0, le=10_000_000)
    estimated_output_tokens: int = Field(default=1000, ge=0, le=1_000_000)


# --- models API ----------------------------------------------------------------------------------
ConfigTier = Literal["fast", "default", "reasoning", "deep_research", "embedding"]
CapabilityName = Literal["structured_output", "tools", "code_execution", "search", "streaming", "background"]


class ProviderCapabilitiesOut(BaseModel):
    structured_output: bool
    tools: bool
    search: bool
    code_execution: bool
    streaming: bool
    local: bool


class ProviderStatusOut(BaseModel):
    kind: str
    configured: bool
    local: bool
    usable: bool = Field(description="Configured and permitted by the organization's data-processing consent")
    capabilities: ProviderCapabilitiesOut


class TierModelOut(BaseModel):
    provider: str
    model: str
    source: str
    priority: int
    local: bool
    price_configured: bool


class ModelCatalogOut(BaseModel):
    default_provider: str
    external_processing_allowed: bool
    providers: list[ProviderStatusOut]
    tiers: dict[str, list[TierModelOut]]
    deep_research_agent: str | None
    task_tiers: dict[str, str]


class ModelConfigCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    provider_kind: ProviderKind
    model: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/@\-]*$")
    tier: ConfigTier
    task_types: list[TaskType] = Field(default_factory=list)
    priority: int = Field(default=100, ge=0, le=10_000)
    enabled: bool = True
    max_output_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    input_per_mtok_usd: Decimal | None = Field(default=None, ge=0, le=100_000)
    output_per_mtok_usd: Decimal | None = Field(default=None, ge=0, le=100_000)
    cached_input_per_mtok_usd: Decimal | None = Field(default=None, ge=0, le=100_000)
    context_window: int | None = Field(default=None, ge=1, le=100_000_000)
    capabilities: dict[CapabilityName, bool] = Field(default_factory=dict)


class ModelConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    task_types: list[TaskType] | None = None
    priority: int | None = Field(default=None, ge=0, le=10_000)
    enabled: bool | None = None
    max_output_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    input_per_mtok_usd: Decimal | None = Field(default=None, ge=0, le=100_000)
    output_per_mtok_usd: Decimal | None = Field(default=None, ge=0, le=100_000)
    cached_input_per_mtok_usd: Decimal | None = Field(default=None, ge=0, le=100_000)
    context_window: int | None = Field(default=None, ge=1, le=100_000_000)
    capabilities: dict[CapabilityName, bool] | None = None


class ModelConfigOut(ORMModel):
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: str
    organization_id: str | None
    scope: Literal["organization", "platform"]
    provider_kind: str
    model: str
    tier: str
    task_types: list[str]
    priority: int
    enabled: bool
    max_output_tokens: int | None
    temperature: float | None
    input_per_mtok_usd: Decimal | None
    output_per_mtok_usd: Decimal | None
    cached_input_per_mtok_usd: Decimal | None
    context_window: int | None
    capabilities: dict[str, Any]
    created_at: datetime
    updated_at: datetime | None = None


class ModelUsageOut(ORMModel):
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: str
    project_id: str | None
    mission_id: str | None
    agent_run_id: str | None
    research_task_id: str | None
    provider: str
    model: str
    model_version: str | None
    request_id: str | None
    trace_id: str | None
    task_type: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    thinking_tokens: int
    latency_ms: int
    cost_usd: Decimal
    cost_estimated: bool
    cost_basis: str | None
    success: bool
    error_code: str | None
    retry_count: int
    route_reason: str | None
    created_at: datetime


class UsageSummaryRow(BaseModel):
    provider: str | None = None
    model: str | None = None
    task_type: str | None = None
    calls: int
    failures: int
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    thinking_tokens: int
    cost_usd: Decimal
    avg_latency_ms: float


class UsageSummaryOut(BaseModel):
    since: datetime
    until: datetime
    group_by: list[str]
    groups: list[UsageSummaryRow]
    totals: UsageSummaryRow
