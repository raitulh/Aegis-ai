"""Google Gemini via the Interactions API (``POST /v1beta/interactions``, steps schema, API revision 2026-05-20).

Capabilities: model calls with system instructions, structured output (``response_format`` JSON schema),
function tools, provider built-ins (``google_search``, ``code_execution``, ``url_context``, remote
``mcp_server``), thinking level, seeds, streaming (SSE ``step.delta`` events), usage metadata (input / output /
cached / thought / tool-use tokens), citations from ``url_citation`` annotations, and background agent
interactions (Deep Research) with polling, resumable streaming (``last_event_id``) and cancellation.

Model calls are sent with ``store: false`` (no server-side retention) unless a background agent requires
storage. Model IDs are never hard-coded here: callers pass the model/agent chosen by the router from config.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any

import httpx

from aegis_api.infrastructure.llm.base import (
    AgentInteractionProvider,
    LLMError,
    LLMErrorKind,
    LLMProvider,
    classify_status,
)
from aegis_api.infrastructure.llm.jsonschema_utils import inline_refs, parse_json_output
from aegis_api.infrastructure.llm.schemas import (
    Citation,
    InteractionEvent,
    InteractionSnapshot,
    LLMRequest,
    LLMResponse,
    ToolCallRequest,
    Turn,
    Usage,
)
from engines.lab.routing import Feature

PROVIDER = "gemini"
_TOOL_STEPS = {
    "function_call",
    "function_result",
    "google_search_call",
    "google_search_result",
    "code_execution_call",
    "code_execution_result",
    "url_context_call",
    "url_context_result",
    "mcp_server_tool_call",
    "mcp_server_tool_result",
    "file_search_call",
    "file_search_result",
}


def _usage(data: dict[str, Any] | None) -> Usage:
    u = data or {}
    return Usage(
        input_tokens=int(u.get("total_input_tokens") or 0),
        output_tokens=int(u.get("total_output_tokens") or 0),
        cached_tokens=int(u.get("total_cached_tokens") or 0),
        thought_tokens=int(u.get("total_thought_tokens") or 0),
        tool_use_tokens=int(u.get("total_tool_use_tokens") or 0),
        total_tokens=int(u.get("total_tokens") or 0),
    )


def parse_steps(data: dict[str, Any]) -> tuple[str, list[ToolCallRequest], list[Citation], list[str]]:
    """Extract (output_text, tool_calls, citations, step_types). ``output_text`` mirrors the SDK accessor:
    the text of the trailing run of ``model_output`` steps."""
    steps = data.get("steps")
    if steps is None:  # tolerate the pre-May-2026 "outputs" representation
        steps = [
            {"type": "model_output", "content": [o]} if o.get("type") == "text" else o
            for o in data.get("outputs") or []
        ]
    tool_calls: list[ToolCallRequest] = []
    citations: list[Citation] = []
    step_types: list[str] = []
    trailing: list[str] = []
    for step in steps:
        stype = str(step.get("type", "unknown"))
        step_types.append(stype)
        if stype == "model_output":
            for content in step.get("content") or []:
                if content.get("type") == "text":
                    trailing.append(str(content.get("text", "")))
                    for ann in content.get("annotations") or []:
                        url = ann.get("url") or ann.get("uri")
                        if url:
                            citations.append(
                                Citation(
                                    url=str(url),
                                    title=ann.get("title"),
                                    start_index=ann.get("start_index"),
                                    end_index=ann.get("end_index"),
                                )
                            )
        elif stype == "thought":
            continue  # chain-of-thought is never stored
        else:
            if stype in _TOOL_STEPS:
                trailing = []
            if stype == "function_call":
                tool_calls.append(
                    ToolCallRequest(
                        id=str(step.get("id", "")),
                        name=str(step.get("name", "")),
                        arguments=step.get("arguments") or {},
                    )
                )
    return "".join(trailing).strip(), tool_calls, citations, step_types


def _turn_step(turn: Turn) -> dict[str, Any]:
    if turn.kind == "user":
        return {"type": "user_input", "content": [{"type": "text", "text": turn.text or ""}]}
    if turn.kind == "model":
        return {"type": "model_output", "content": [{"type": "text", "text": turn.text or ""}]}
    if turn.kind == "tool_call" and turn.tool_call:
        return {
            "type": "function_call",
            "id": turn.tool_call.id,
            "name": turn.tool_call.name,
            "arguments": turn.tool_call.arguments,
        }
    if turn.kind == "tool_result" and turn.tool_result:
        r = turn.tool_result
        return {
            "type": "function_result",
            "call_id": r.call_id,
            "name": r.name,
            "result": r.result,
            "is_error": r.is_error,
        }
    raise LLMError("malformed conversation turn", kind=LLMErrorKind.INVALID_REQUEST, provider=PROVIDER)


class GeminiProvider(LLMProvider, AgentInteractionProvider):
    name = PROVIDER
    data_leaves_organization = True
    features = frozenset(
        {
            Feature.STRUCTURED_OUTPUT,
            Feature.TOOLS,
            Feature.CODE_EXECUTION,
            Feature.STREAMING,
            Feature.BACKGROUND,
            Feature.REMOTE_MCP,
            Feature.WEB_SEARCH,
            Feature.AGENT,
        }
    )

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        api_revision: str,
        timeout: float = 120.0,
        embed_model: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise LLMError("Gemini API key is not configured", kind=LLMErrorKind.AUTH, provider=PROVIDER)
        self.base_url = base_url.rstrip("/")
        self.embed_model = embed_model
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=10.0),
            follow_redirects=False,
            transport=transport,
            headers={"x-goog-api-key": api_key, "Api-Revision": api_revision, "Content-Type": "application/json"},
        )

    def close(self) -> None:
        self._client.close()

    # -- transport ----------------------------------------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        try:
            response = self._client.request(
                method, f"{self.base_url}{path}", json=json_body, params=params, timeout=timeout
            )
        except httpx.TimeoutException as exc:
            raise LLMError("Gemini request timed out", kind=LLMErrorKind.TIMEOUT, provider=PROVIDER) from exc
        except httpx.TransportError as exc:
            raise LLMError(
                f"Gemini unreachable: {type(exc).__name__}", kind=LLMErrorKind.UNAVAILABLE, provider=PROVIDER
            ) from exc
        if response.status_code >= 400:
            self._raise(response)
        try:
            data = response.json()
        except ValueError as exc:
            raise LLMError(
                "Gemini returned a non-JSON response", kind=LLMErrorKind.TRANSIENT, provider=PROVIDER
            ) from exc
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _raise(response: httpx.Response) -> None:
        message = f"HTTP {response.status_code}"
        try:
            err = response.json().get("error") or {}
            if isinstance(err, dict) and err.get("message"):
                message = f"{message}: {str(err['message'])[:300]}"
        except ValueError:
            pass
        retry_after = response.headers.get("retry-after")
        raise LLMError(
            f"Gemini error ({message})",
            kind=classify_status(response.status_code),
            provider=PROVIDER,
            status=response.status_code,
            retry_after=float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else None,
        )

    # -- model calls --------------------------------------------------------------------------------
    def build_body(self, request: LLMRequest, *, model: str) -> dict[str, Any]:
        body: dict[str, Any] = {"model": model, "store": False}
        if request.history:
            body["input"] = [{"type": "user_input", "content": [{"type": "text", "text": request.input}]}] + [
                _turn_step(t) for t in request.history
            ]
        else:
            body["input"] = request.input
        if request.system:
            body["system_instruction"] = request.system
        gen: dict[str, Any] = {"max_output_tokens": request.max_output_tokens}
        if request.temperature is not None:
            gen["temperature"] = request.temperature
        if request.seed is not None:
            gen["seed"] = request.seed
        if request.thinking_level:
            gen["thinking_level"] = request.thinking_level
        body["generation_config"] = gen
        if request.response_schema:
            body["response_format"] = {
                "type": "text",
                "mime_type": "application/json",
                "schema": inline_refs(request.response_schema),
            }
        tools: list[dict[str, Any]] = [
            {"type": "function", "name": t.name, "description": t.description, "parameters": inline_refs(t.parameters)}
            for t in request.tools
        ]
        tools += [dict(t) for t in request.builtin_tools]
        if tools:
            body["tools"] = tools
        return body

    def generate(self, request: LLMRequest, *, model: str) -> LLMResponse:
        started = time.perf_counter()
        data = self._request(
            "POST", "/interactions", json_body=self.build_body(request, model=model), timeout=request.timeout_seconds
        )
        status = data.get("status")
        if status == "failed":
            errors = data.get("errors") or []
            detail = "; ".join(str(e.get("message", "")) for e in errors if isinstance(e, dict))[:300]
            kind = (
                LLMErrorKind.POLICY
                if "safety" in detail.lower() or "blocked" in detail.lower()
                else LLMErrorKind.TRANSIENT
            )
            raise LLMError(f"Gemini interaction failed: {detail or 'unknown error'}", kind=kind, provider=PROVIDER)
        text, tool_calls, citations, step_types = parse_steps(data)
        parsed = parse_json_output(text) if request.response_schema and text else None
        return LLMResponse(
            text=text,
            parsed=parsed,
            tool_calls=tool_calls,
            citations=citations,
            usage=_usage(data.get("usage")),
            provider=PROVIDER,
            model=str(data.get("model") or model),
            model_revision=data.get("model_version") or data.get("modelVersion"),
            finish_reason=status,
            interaction_id=data.get("id"),
            latency_ms=int((time.perf_counter() - started) * 1000),
            step_types=step_types,
        )

    def stream(self, request: LLMRequest, *, model: str) -> Iterator[InteractionEvent]:
        body = self.build_body(request, model=model)
        body["stream"] = True
        yield from self._sse("POST", "/interactions", json_body=body)

    # -- background agents (Deep Research) ----------------------------------------------------------
    def start_agent(
        self,
        *,
        agent: str,
        input_text: str,
        agent_config: dict[str, object] | None = None,
        tools: list[dict[str, object]] | None = None,
        system: str | None = None,
    ) -> InteractionSnapshot:
        body: dict[str, Any] = {"agent": agent, "input": input_text, "background": True, "store": True}
        if agent_config:
            body["agent_config"] = agent_config
        if tools:
            body["tools"] = tools
        if system:
            body["system_instruction"] = system
        return self._snapshot(self._request("POST", "/interactions", json_body=body, timeout=60))

    def get_interaction(self, interaction_id: str) -> InteractionSnapshot:
        return self._snapshot(self._request("GET", f"/interactions/{_safe_id(interaction_id)}", timeout=60))

    def stream_interaction(
        self, interaction_id: str, *, last_event_id: str | None = None
    ) -> Iterator[InteractionEvent]:
        params: dict[str, Any] = {"stream": "true"}
        if last_event_id:
            params["last_event_id"] = last_event_id
        yield from self._sse("GET", f"/interactions/{_safe_id(interaction_id)}", params=params)

    def cancel_interaction(self, interaction_id: str) -> None:
        self._request("POST", f"/interactions/{_safe_id(interaction_id)}/cancel", timeout=30)

    @staticmethod
    def _snapshot(data: dict[str, Any]) -> InteractionSnapshot:
        text, _calls, citations, step_types = parse_steps(data)
        status = str(data.get("status") or "unknown")
        if status not in ("in_progress", "requires_action", "completed", "failed", "cancelled"):
            status = "unknown"
        return InteractionSnapshot(
            id=str(data.get("id", "")),
            status=status,  # type: ignore[arg-type]
            text=text,
            citations=citations,
            usage=_usage(data.get("usage")),
            errors=[e for e in data.get("errors") or [] if isinstance(e, dict)],
            step_types=step_types,
            agent=data.get("agent"),
        )

    def _sse(
        self, method: str, path: str, *, json_body: Any = None, params: dict[str, Any] | None = None
    ) -> Iterator[InteractionEvent]:
        try:
            with self._client.stream(
                method,
                f"{self.base_url}{path}",
                json=json_body,
                params=params,
                headers={"Accept": "text/event-stream"},
                timeout=None,
            ) as response:
                if response.status_code >= 400:
                    response.read()
                    self._raise(response)
                buffer: list[str] = []
                sse_id: str | None = None
                for line in response.iter_lines():
                    if line.startswith("data:"):
                        buffer.append(line[5:].strip())
                    elif line.startswith("id:"):
                        sse_id = line[3:].strip()
                    elif line == "" and buffer:
                        payload = "\n".join(buffer)
                        buffer = []
                        try:
                            data = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(data, dict):
                            continue
                        yield InteractionEvent(
                            event_id=data.get("event_id") or sse_id,
                            event_type=str(data.get("event_type", "unknown")),
                            data=data,
                        )
        except httpx.TimeoutException as exc:
            raise LLMError("Gemini stream timed out", kind=LLMErrorKind.TIMEOUT, provider=PROVIDER) from exc
        except httpx.TransportError as exc:
            raise LLMError("Gemini stream interrupted", kind=LLMErrorKind.UNAVAILABLE, provider=PROVIDER) from exc

    # -- embeddings / health ------------------------------------------------------------------------
    def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        requests = [{"model": f"models/{model}", "content": {"parts": [{"text": t}]}} for t in texts]
        data = self._request(
            "POST", f"/models/{model}:batchEmbedContents", json_body={"requests": requests}, timeout=60
        )
        return [list(map(float, e.get("values", []))) for e in data.get("embeddings", [])]

    def health(self) -> tuple[bool, str]:
        try:
            self._request("GET", "/models", params={"pageSize": 1}, timeout=8)
            return True, "Gemini API reachable"
        except LLMError as exc:
            return False, f"Gemini unavailable ({exc.kind.value})"


def _safe_id(value: str) -> str:
    if not value or any(ch in value for ch in "/?#%\\ \x00") or len(value) > 256:
        raise LLMError("invalid interaction id", kind=LLMErrorKind.INVALID_REQUEST, provider=PROVIDER)
    return value
