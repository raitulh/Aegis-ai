"""Anthropic Messages API adapter. Structured output is obtained by forcing a single schema-typed tool
(``emit_output``); regular tools map to ``tool_use`` / ``tool_result`` content blocks."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from aegis_api.infrastructure.llm.base import LLMError, LLMErrorKind, LLMProvider
from aegis_api.infrastructure.llm.jsonschema_utils import inline_refs, parse_json_output
from aegis_api.infrastructure.llm.providers.http_common import post_json
from aegis_api.infrastructure.llm.schemas import LLMRequest, LLMResponse, ToolCallRequest, Usage
from engines.lab.routing import Feature

PROVIDER = "anthropic"
OUTPUT_TOOL = "emit_output"
API_VERSION = "2023-06-01"


class AnthropicProvider(LLMProvider):
    name = PROVIDER
    data_leaves_organization = True
    features = frozenset({Feature.STRUCTURED_OUTPUT, Feature.TOOLS, Feature.STREAMING})

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.anthropic.com/v1",
        timeout: float = 120.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise LLMError("Anthropic API key is not configured", kind=LLMErrorKind.AUTH, provider=PROVIDER)
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=10.0),
            follow_redirects=False,
            transport=transport,
            headers={"x-api-key": api_key, "anthropic-version": API_VERSION, "content-type": "application/json"},
        )

    def _messages(self, request: LLMRequest) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = [{"role": "user", "content": request.input}]
        for turn in request.history:
            if turn.kind == "user":
                messages.append({"role": "user", "content": turn.text or ""})
            elif turn.kind == "model":
                messages.append({"role": "assistant", "content": turn.text or ""})
            elif turn.kind == "tool_call" and turn.tool_call:
                c = turn.tool_call
                messages.append(
                    {
                        "role": "assistant",
                        "content": [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}],
                    }
                )
            elif turn.kind == "tool_result" and turn.tool_result:
                r = turn.tool_result
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": r.call_id,
                                "content": r.result,
                                "is_error": r.is_error,
                            }
                        ],
                    }
                )
        return messages

    def generate(self, request: LLMRequest, *, model: str) -> LLMResponse:
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": request.max_output_tokens,
            "messages": self._messages(request),
        }
        if request.system:
            body["system"] = request.system
        if request.temperature is not None:
            body["temperature"] = request.temperature
        tools = [
            {"name": t.name, "description": t.description, "input_schema": inline_refs(t.parameters)}
            for t in request.tools
        ]
        if request.response_schema:
            tools.append(
                {
                    "name": OUTPUT_TOOL,
                    "description": "Return the final answer as structured JSON.",
                    "input_schema": inline_refs(request.response_schema),
                }
            )
            if not request.tools:
                body["tool_choice"] = {"type": "tool", "name": OUTPUT_TOOL}
        if tools:
            body["tools"] = tools
        started = time.perf_counter()
        data = post_json(
            self._client, f"{self.base_url}/messages", body, provider=PROVIDER, timeout=request.timeout_seconds
        )
        texts: list[str] = []
        calls: list[ToolCallRequest] = []
        parsed: dict[str, Any] | None = None
        for block in data.get("content") or []:
            if block.get("type") == "text":
                texts.append(str(block.get("text", "")))
            elif block.get("type") == "tool_use":
                if block.get("name") == OUTPUT_TOOL:
                    parsed = block.get("input") if isinstance(block.get("input"), dict) else None
                else:
                    calls.append(
                        ToolCallRequest(
                            id=str(block.get("id", "")),
                            name=str(block.get("name", "")),
                            arguments=block.get("input") or {},
                        )
                    )
        text = "".join(texts)
        if parsed is None and request.response_schema and text:
            parsed = parse_json_output(text)
        usage = data.get("usage") or {}
        return LLMResponse(
            text=text if parsed is None else json.dumps(parsed),
            parsed=parsed,
            tool_calls=calls,
            usage=Usage(
                input_tokens=int(usage.get("input_tokens") or 0),
                output_tokens=int(usage.get("output_tokens") or 0),
                cached_tokens=int(usage.get("cache_read_input_tokens") or 0),
                total_tokens=int((usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)),
            ),
            provider=PROVIDER,
            model=str(data.get("model") or model),
            finish_reason=data.get("stop_reason"),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    def health(self) -> tuple[bool, str]:
        try:
            r = self._client.get(f"{self.base_url}/models", timeout=8)
        except httpx.HTTPError as exc:
            return False, f"Anthropic unreachable: {type(exc).__name__}"
        return (r.status_code == 200, f"Anthropic HTTP {r.status_code}")
