"""OpenAI Chat Completions adapter (structured output via ``json_schema``, function tools, usage mapping)."""

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

PROVIDER = "openai"


class OpenAIProvider(LLMProvider):
    name = PROVIDER
    data_leaves_organization = True
    features = frozenset({Feature.STRUCTURED_OUTPUT, Feature.TOOLS, Feature.STREAMING})

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 120.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise LLMError("OpenAI API key is not configured", kind=LLMErrorKind.AUTH, provider=PROVIDER)
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=10.0),
            follow_redirects=False,
            transport=transport,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )

    def _messages(self, request: LLMRequest) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "user", "content": request.input})
        for turn in request.history:
            if turn.kind == "user":
                messages.append({"role": "user", "content": turn.text or ""})
            elif turn.kind == "model":
                messages.append({"role": "assistant", "content": turn.text or ""})
            elif turn.kind == "tool_call" and turn.tool_call:
                call = turn.tool_call
                messages.append(
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": call.id,
                                "type": "function",
                                "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                            }
                        ],
                    }
                )
            elif turn.kind == "tool_result" and turn.tool_result:
                messages.append(
                    {"role": "tool", "tool_call_id": turn.tool_result.call_id, "content": turn.tool_result.result}
                )
        return messages

    def generate(self, request: LLMRequest, *, model: str) -> LLMResponse:
        body: dict[str, Any] = {
            "model": model,
            "messages": self._messages(request),
            "max_completion_tokens": request.max_output_tokens,
        }
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.seed is not None:
            body["seed"] = request.seed
        if request.response_schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "output", "schema": inline_refs(request.response_schema), "strict": False},
            }
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
            self._client, f"{self.base_url}/chat/completions", body, provider=PROVIDER, timeout=request.timeout_seconds
        )
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        if message.get("refusal"):
            raise LLMError("OpenAI refused the request", kind=LLMErrorKind.POLICY, provider=PROVIDER)
        text = str(message.get("content") or "")
        calls = []
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append(
                ToolCallRequest(
                    id=str(tc.get("id", "")),
                    name=str(fn.get("name", "")),
                    arguments=args if isinstance(args, dict) else {},
                )
            )
        usage = data.get("usage") or {}
        return LLMResponse(
            text=text,
            parsed=parse_json_output(text) if request.response_schema and text else None,
            tool_calls=calls,
            usage=Usage(
                input_tokens=int(usage.get("prompt_tokens") or 0),
                output_tokens=int(usage.get("completion_tokens") or 0),
                cached_tokens=int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0),
                thought_tokens=int((usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0),
                total_tokens=int(usage.get("total_tokens") or 0),
            ),
            provider=PROVIDER,
            model=str(data.get("model") or model),
            model_revision=data.get("system_fingerprint"),
            finish_reason=choice.get("finish_reason"),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        data = post_json(
            self._client, f"{self.base_url}/embeddings", {"model": model, "input": texts}, provider=PROVIDER
        )
        return [list(map(float, d.get("embedding", []))) for d in data.get("data", [])]

    def health(self) -> tuple[bool, str]:
        try:
            r = self._client.get(f"{self.base_url}/models", timeout=8)
        except httpx.HTTPError as exc:
            return False, f"OpenAI unreachable: {type(exc).__name__}"
        return (r.status_code == 200, f"OpenAI HTTP {r.status_code}")
