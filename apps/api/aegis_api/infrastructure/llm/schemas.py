"""Provider-neutral LLM request/response contracts used by the model gateway."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ToolDefinition(BaseModel):
    """A function the model may request. Execution always happens in the ToolBroker, never in the provider."""

    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_.:-]{0,63}$")
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})


class ToolCallRequest(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResultMessage(BaseModel):
    call_id: str
    name: str
    result: str
    is_error: bool = False


class Turn(BaseModel):
    """Stateless conversation history for multi-step tool loops (providers are called with store=false)."""

    kind: Literal["user", "model", "tool_call", "tool_result"]
    text: str | None = None
    tool_call: ToolCallRequest | None = None
    tool_result: ToolResultMessage | None = None


class LLMRequest(BaseModel):
    task_type: str
    system: str | None = None
    input: str = ""
    history: list[Turn] = Field(default_factory=list)
    response_schema: dict[str, Any] | None = None
    tools: list[ToolDefinition] = Field(default_factory=list)
    # Provider-native tools, e.g. {"type": "google_search"}, {"type": "code_execution"}, {"type": "url_context"},
    # {"type": "mcp_server", "name": ..., "url": ..., "headers": {...}, "allowed_tools": [...]}.
    builtin_tools: list[dict[str, Any]] = Field(default_factory=list)
    temperature: float | None = None
    max_output_tokens: int = 4096
    seed: int | None = None
    thinking_level: Literal["minimal", "low", "medium", "high"] | None = None
    timeout_seconds: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Citation(BaseModel):
    url: str
    title: str | None = None
    start_index: int | None = None
    end_index: int | None = None


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    thought_tokens: int = 0
    tool_use_tokens: int = 0
    total_tokens: int = 0


class LLMResponse(BaseModel):
    text: str
    parsed: dict[str, Any] | None = None
    tool_calls: list[ToolCallRequest] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    provider: str
    model: str
    model_revision: str | None = None
    finish_reason: str | None = None
    interaction_id: str | None = None
    latency_ms: int = 0
    retries: int = 0
    step_types: list[str] = Field(default_factory=list)


class InteractionSnapshot(BaseModel):
    """State of a (background) agent interaction, e.g. Gemini Deep Research."""

    id: str
    status: Literal["in_progress", "requires_action", "completed", "failed", "cancelled", "unknown"]
    text: str = ""
    citations: list[Citation] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    step_types: list[str] = Field(default_factory=list)
    agent: str | None = None


class InteractionEvent(BaseModel):
    event_id: str | None = None
    event_type: str
    data: dict[str, Any] = Field(default_factory=dict)
