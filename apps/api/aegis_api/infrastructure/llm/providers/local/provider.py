"""Local models via Ollama (``/api/chat``): data never leaves the organization. Structured output uses Ollama's
JSON-schema ``format``; reasoning traces are disabled (``think: false``) and never stored."""

from __future__ import annotations

import time
from typing import Any

import httpx

from aegis_api.infrastructure.llm.base import LLMProvider
from aegis_api.infrastructure.llm.jsonschema_utils import inline_refs, parse_json_output
from aegis_api.infrastructure.llm.providers.http_common import post_json
from aegis_api.infrastructure.llm.schemas import LLMRequest, LLMResponse, ToolCallRequest, Usage
from engines.lab.routing import Feature
from engines.providers.base import strip_reasoning

PROVIDER = "ollama"


class OllamaProvider(LLMProvider):
    name = PROVIDER
    data_leaves_organization = False
    features = frozenset({Feature.STRUCTURED_OUTPUT, Feature.TOOLS})

    def __init__(self, *, base_url: str, timeout: float = 180.0, transport: httpx.BaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=5.0), follow_redirects=False, transport=transport
        )

    def generate(self, request: LLMRequest, *, model: str) -> LLMResponse:
        messages: list[dict[str, Any]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "user", "content": request.input})
        for turn in request.history:
            if turn.kind in ("user", "model"):
                messages.append({"role": "user" if turn.kind == "user" else "assistant", "content": turn.text or ""})
            elif turn.kind == "tool_call" and turn.tool_call:
                messages.append(
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {"function": {"name": turn.tool_call.name, "arguments": turn.tool_call.arguments}}
                        ],
                    }
                )
            elif turn.kind == "tool_result" and turn.tool_result:
                messages.append({"role": "tool", "content": turn.tool_result.result})
        options: dict[str, Any] = {"num_predict": request.max_output_tokens}
        if request.temperature is not None:
            options["temperature"] = request.temperature
        if request.seed is not None:
            options["seed"] = request.seed
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "think": False,
            "options": options,
        }
        if request.response_schema:
            body["format"] = inline_refs(request.response_schema)
        if request.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {"name": t.name, "description": t.description, "parameters": inline_refs(t.parameters)},
                }
                for t in request.tools
            ]
        started = time.perf_counter()
        data = post_json(
            self._client, f"{self.base_url}/api/chat", body, provider=PROVIDER, timeout=request.timeout_seconds
        )
        message = data.get("message") or {}
        text = strip_reasoning(str(message.get("content") or ""))
        calls = [
            ToolCallRequest(
                id=f"call_{i}",
                name=str((tc.get("function") or {}).get("name", "")),
                arguments=(tc.get("function") or {}).get("arguments") or {},
            )
            for i, tc in enumerate(message.get("tool_calls") or [])
        ]
        return LLMResponse(
            text=text,
            parsed=parse_json_output(text) if request.response_schema and text else None,
            tool_calls=calls,
            usage=Usage(
                input_tokens=int(data.get("prompt_eval_count") or 0),
                output_tokens=int(data.get("eval_count") or 0),
                total_tokens=int((data.get("prompt_eval_count") or 0) + (data.get("eval_count") or 0)),
            ),
            provider=PROVIDER,
            model=str(data.get("model") or model),
            finish_reason=data.get("done_reason"),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        data = post_json(
            self._client, f"{self.base_url}/api/embed", {"model": model, "input": texts}, provider=PROVIDER
        )
        return [list(map(float, v)) for v in data.get("embeddings", [])]

    def health(self) -> tuple[bool, str]:
        try:
            r = self._client.get(f"{self.base_url}/api/tags", timeout=3)
        except httpx.HTTPError as exc:
            return False, f"Ollama unreachable: {type(exc).__name__}"
        return (r.status_code == 200, f"Ollama HTTP {r.status_code}")
