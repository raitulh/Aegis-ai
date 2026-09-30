"""Gemini Interactions API client (stateful interactions, background agents, Gemini Deep Research).

Wire format: snake_case JSON, lowercase enums. An interaction is a list of ``steps`` (``user_input``,
``model_output``, ``thought``, ``function_call``, ``google_search_call`` …); the final text is the text
content of the trailing ``model_output`` step(s). The legacy ``outputs`` field and the legacy stream event
names (``interaction.start`` / ``content.delta`` / ``interaction.complete``) are still tolerated.

Deep Research runs in the background: create it with :meth:`GeminiInteractionsClient.create_deep_research`,
then poll :meth:`get` (or :meth:`wait`) until the status is terminal, or follow :meth:`stream` (resumable
with ``last_event_id``). Collaborative planning: create with ``collaborative_planning=True`` → the
interaction completes with the *plan* as its text; refine by creating again with ``previous_interaction_id``
and ``collaborative_planning=True``; approve by creating with ``previous_interaction_id`` and
``collaborative_planning=False``.

Thought content (``thought`` steps and ``thought_summary`` deltas) is never exposed: snapshots carry only a
compact ``{type, summary}`` list of steps and ``raw`` has thought summaries removed.

Retries: reads (``get``, ``stream`` reconnects, ``cancel``, ``delete``) are retried on transient errors with
bounded, jittered backoff. ``create`` starts paid background work, so it is retried only when the provider
explicitly did not accept it (HTTP 429/503) — never after a timeout.
"""

from __future__ import annotations

import json
import random
import re
import time
from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
from pydantic import BaseModel, Field

from aegis_api.config import get_settings
from aegis_api.lab.llm.errors import (
    LLMError,
    LLMTransientError,
    LLMUnavailableError,
    LLMValidationError,
)
from aegis_api.lab.llm.providers._http import (
    build_client,
    iter_sse,
    map_transport_error,
    raise_for_stream_status,
    raw_json_error,
    request_json,
)
from aegis_api.lab.llm.schemas import Citation, ToolCall, Usage

TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "incomplete", "budget_exceeded"})
DEFAULT_DEEP_RESEARCH_TOOLS: tuple[dict[str, str], ...] = (
    {"type": "google_search"},
    {"type": "url_context"},
    {"type": "code_execution"},
)
LEGACY_EVENT_NAMES = {
    "interaction.start": "interaction.created",
    "interaction.complete": "interaction.completed",
    "content.start": "step.start",
    "content.delta": "step.delta",
    "content.stop": "step.stop",
}
_MARKDOWN_LINK = re.compile(r"\[([^\]]{1,300})\]\((https?://[^\s)]+)\)")
_THOUGHT_KEYS = ("summary", "content", "text", "thought", "thoughts")


# --- models -------------------------------------------------------------------------------------
class InteractionSnapshot(BaseModel):
    id: str
    status: str
    text: str = ""
    citations: list[Citation] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    steps: list[dict[str, Any]] = Field(default_factory=list)
    error: dict[str, Any] | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
    model: str | None = None
    agent: str | None = None
    previous_interaction_id: str | None = None
    function_calls: list[ToolCall] = Field(default_factory=list)

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def requires_action(self) -> bool:
        return self.status == "requires_action"


class InteractionEvent(BaseModel):
    event_type: str
    raw_event_type: str | None = None
    event_id: str | None = None
    interaction_id: str | None = None
    index: int | None = None
    status: str | None = None
    delta_type: str | None = None
    text_delta: str | None = None
    annotations: list[Citation] = Field(default_factory=list)
    snapshot: InteractionSnapshot | None = None
    error: dict[str, Any] | None = None
    data: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        return self.event_type in ("interaction.completed", "error") or (
            self.event_type == "interaction.status_update" and (self.status or "") in TERMINAL_STATUSES
        )


# --- parsing ------------------------------------------------------------------------------------
def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _legacy_outputs_to_steps(outputs: list[Any]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for output in outputs:
        if not isinstance(output, dict):
            continue
        kind = output.get("type")
        if kind in ("text", "image", "audio", "document"):
            if steps and steps[-1].get("type") == "model_output" and steps[-1].get("_legacy"):
                steps[-1]["content"].append(output)
            else:
                steps.append({"type": "model_output", "content": [output], "_legacy": True})
        else:
            steps.append(output)
    for step in steps:
        step.pop("_legacy", None)
    return steps


def _steps_of(data: dict[str, Any]) -> list[dict[str, Any]]:
    steps = data.get("steps")
    if isinstance(steps, list):
        return [s for s in steps if isinstance(s, dict)]
    outputs = data.get("outputs")
    if isinstance(outputs, list):
        return _legacy_outputs_to_steps(outputs)
    return []


def _text_items(step: dict[str, Any]) -> list[dict[str, Any]]:
    content = step.get("content")
    if isinstance(content, dict):
        content = [content]
    if not isinstance(content, list):
        return []
    return [c for c in content if isinstance(c, dict) and c.get("type") == "text" and isinstance(c.get("text"), str)]


def _trailing_output_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    trailing: list[dict[str, Any]] = []
    for step in reversed(steps):
        kind = step.get("type")
        if kind == "model_output":
            trailing.append(step)
        elif kind == "thought" and not trailing:
            continue
        elif trailing:
            break
        else:
            break
    return list(reversed(trailing))


def _annotation_citations(items: list[dict[str, Any]], offsets: list[int]) -> list[Citation]:
    citations: list[Citation] = []
    for item, offset in zip(items, offsets, strict=True):
        for annotation in item.get("annotations") or []:
            if (
                not isinstance(annotation, dict)
                or annotation.get("type") != "url_citation"
                or not annotation.get("url")
            ):
                continue
            start = annotation.get("start_index")
            end = annotation.get("end_index")
            citations.append(
                Citation(
                    url=str(annotation["url"]),
                    title=annotation.get("title"),
                    start_index=start + offset if isinstance(start, int) else None,
                    end_index=end + offset if isinstance(end, int) else None,
                )
            )
    return citations


def _dedupe(citations: list[Citation]) -> list[Citation]:
    seen: set[str] = set()
    unique: list[Citation] = []
    for citation in citations:
        if citation.url in seen:
            continue
        seen.add(citation.url)
        unique.append(citation)
    return unique


def _result_citations(steps: list[dict[str, Any]]) -> list[Citation]:
    citations: list[Citation] = []
    for step in steps:
        if step.get("type") not in ("url_context_result", "google_search_result"):
            continue
        result = step.get("result")
        items = result if isinstance(result, list) else [result]
        for item in items:
            if not isinstance(item, dict) or not item.get("url"):
                continue
            status = str(item.get("status") or "success").lower()
            if "success" in status or status in ("ok", "200"):
                citations.append(Citation(url=str(item["url"]), title=item.get("title")))
    return citations


def _clip(text: str, limit: int = 200) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def summarize_step(step: dict[str, Any]) -> dict[str, Any]:
    """Compact, thought-free description of a step for timelines and persistence."""
    kind = str(step.get("type") or "unknown")
    arguments = _as_dict(step.get("arguments"))
    if kind == "user_input":
        summary = "user input"
    elif kind == "model_output":
        chars = sum(len(c["text"]) for c in _text_items(step))
        summary = f"model output ({chars} chars)" + (" with error" if step.get("error") else "")
    elif kind == "thought":
        summary = "thinking"
    elif kind == "function_call":
        summary = f"function call {step.get('name', '?')}"
    elif kind == "function_result":
        summary = f"function result {step.get('name') or step.get('call_id', '?')}"
        summary += " (error)" if step.get("is_error") else ""
    elif kind == "google_search_call":
        queries = arguments.get("queries") or []
        summary = "google search: " + _clip("; ".join(str(q) for q in queries))
    elif kind == "google_search_result":
        summary = "google search results"
    elif kind == "url_context_call":
        urls = arguments.get("urls") or []
        summary = "url context: " + _clip(", ".join(str(u) for u in urls))
    elif kind == "url_context_result":
        results = _as_list(step.get("result"))
        ok = sum(1 for r in results if "success" in str(_as_dict(r).get("status", "success")).lower())
        summary = f"url context results ({ok}/{len(results)} retrieved)"
    elif kind == "code_execution_call":
        summary = f"code execution ({arguments.get('language') or 'python'})"
    elif kind == "code_execution_result":
        summary = "code execution result" + (" (error)" if step.get("is_error") else "")
    elif kind == "mcp_server_tool_call":
        summary = f"mcp {step.get('server_name', '?')}.{step.get('name', '?')}"
    elif kind == "mcp_server_tool_result":
        summary = f"mcp result {step.get('server_name') or ''}.{step.get('name') or step.get('call_id', '?')}".strip(
            "."
        )
    else:
        summary = kind
    return {"type": kind, "summary": summary}


def redact_thoughts(data: dict[str, Any]) -> dict[str, Any]:
    """Copy of an interaction payload with every thought summary removed (signatures kept)."""
    redacted = dict(data)
    for key in ("steps", "outputs"):
        items = data.get(key)
        if not isinstance(items, list):
            continue
        cleaned = []
        for item in items:
            if isinstance(item, dict) and item.get("type") == "thought":
                cleaned.append({k: v for k, v in item.items() if k not in _THOUGHT_KEYS})
            else:
                cleaned.append(item)
        redacted[key] = cleaned
    return redacted


def parse_usage(usage: dict[str, Any] | None) -> Usage:
    usage = usage or {}
    return Usage(
        input_tokens=int(usage.get("total_input_tokens") or 0) + int(usage.get("total_tool_use_tokens") or 0),
        output_tokens=int(usage.get("total_output_tokens") or 0),
        cached_tokens=int(usage.get("total_cached_tokens") or 0),
        thinking_tokens=int(usage.get("total_thought_tokens") or 0),
    )


def parse_interaction(data: dict[str, Any]) -> InteractionSnapshot:
    steps = _steps_of(data)
    trailing = _trailing_output_steps(steps)
    items: list[dict[str, Any]] = []
    for step in trailing:
        items.extend(_text_items(step))
    offsets: list[int] = []
    cursor = 0
    for item in items:
        offsets.append(cursor)
        cursor += len(item["text"]) + 2
    text = "\n\n".join(item["text"] for item in items)

    citations = _annotation_citations(items, offsets)
    if not citations:
        all_items = [item for step in steps if step.get("type") == "model_output" for item in _text_items(step)]
        citations = _annotation_citations(all_items, [0] * len(all_items))
        citations = [c.model_copy(update={"start_index": None, "end_index": None}) for c in citations]
    if not citations:
        citations = _result_citations(steps)
    if not citations:
        citations = [Citation(url=m.group(2), title=m.group(1)) for m in _MARKDOWN_LINK.finditer(text)]

    errors = data.get("errors")
    error: dict[str, Any] | None = None
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        error = {"code": errors[0].get("code"), "message": errors[0].get("message")}
    elif isinstance(data.get("error"), dict):
        error = {"code": data["error"].get("code"), "message": data["error"].get("message")}
    status = str(data.get("status") or "unknown")
    if status == "failed" and error is None:
        error = {"code": "failed", "message": "The interaction failed without an error message"}

    results = {str(s.get("call_id")) for s in steps if s.get("type") == "function_result"}
    calls = [
        ToolCall(
            id=str(s.get("id")),
            name=str(s.get("name")),
            arguments=_as_dict(s.get("arguments")),
        )
        for s in steps
        if s.get("type") == "function_call" and s.get("name") and str(s.get("id")) not in results
    ]
    return InteractionSnapshot(
        id=str(data.get("id") or ""),
        status=status,
        text=text,
        citations=_dedupe(citations),
        usage=parse_usage(data.get("usage")),
        steps=[summarize_step(step) for step in steps],
        error=error,
        raw=redact_thoughts(data),
        model=data.get("model"),
        agent=data.get("agent"),
        previous_interaction_id=data.get("previous_interaction_id"),
        function_calls=calls,
    )


def parse_event(data: dict[str, Any], *, sse_event: str | None = None, sse_id: str | None = None) -> InteractionEvent:
    raw_type = str(data.get("event_type") or data.get("type") or sse_event or "unknown")
    event_type = LEGACY_EVENT_NAMES.get(raw_type, raw_type)
    event = InteractionEvent(
        event_type=event_type,
        raw_event_type=raw_type,
        event_id=str(data["event_id"]) if data.get("event_id") is not None else sse_id,
        interaction_id=data.get("interaction_id"),
        index=data.get("index") if isinstance(data.get("index"), int) else None,
        status=data.get("status"),
    )
    safe = dict(data)
    if event_type in ("interaction.created", "interaction.completed") and isinstance(data.get("interaction"), dict):
        snapshot = parse_interaction(data["interaction"])
        event.snapshot = snapshot
        event.interaction_id = event.interaction_id or snapshot.id
        event.status = event.status or snapshot.status
        safe["interaction"] = snapshot.raw
    elif event_type == "step.delta":
        delta = _as_dict(data.get("delta"))
        delta_type = str(delta.get("type") or "")
        event.delta_type = delta_type or None
        if delta_type == "text" and isinstance(delta.get("text"), str):
            event.text_delta = delta["text"]
        elif delta_type == "text_annotation_delta":
            annotations = delta.get("annotations") or []
            event.annotations = _annotation_citations([{"annotations": annotations}], [0])
        elif delta_type.startswith("thought"):
            safe["delta"] = {"type": delta_type}  # never expose thought summaries
    elif event_type == "step.start" and isinstance(data.get("step"), dict):
        step = data["step"]
        safe["step"] = summarize_step(step) if step.get("type") == "thought" else step
    elif event_type == "error":
        err = _as_dict(data.get("error"))
        event.error = {"code": err.get("code"), "message": err.get("message")}
    event.data = safe
    return event


# --- client -------------------------------------------------------------------------------------
class GeminiInteractionsClient:
    """Sync client for ``{base}/interactions`` (defaults from settings)."""

    kind = "gemini"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        max_retries: int | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        settings = get_settings()
        key = api_key or settings.gemini_api_key
        if not key:
            raise LLMUnavailableError("Gemini API key is not configured", code="llm_not_configured", provider="gemini")
        self._api_key = key
        self.base_url = (base_url or settings.gemini_base_url).rstrip("/")
        self.max_retries = settings.gemini_max_retries if max_retries is None else max(max_retries, 0)
        self.default_agent = settings.gemini_deep_research_agent
        self._sleep = sleep
        self._client = build_client(timeout or settings.gemini_timeout_seconds, transport)

    def __repr__(self) -> str:
        return f"GeminiInteractionsClient(base_url={self.base_url!r})"

    def close(self) -> None:
        self._client.close()

    # -- helpers -------------------------------------------------------------------------------
    @property
    def _headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self._api_key, "content-type": "application/json"}

    def _url(self, *parts: str) -> str:
        return "/".join([self.base_url, "interactions", *(quote(p, safe="") for p in parts)])

    def _backoff(self, attempt: int, retry_after: float | None) -> float:
        delay = random.uniform(0, min(30.0, 1.0 * 2**attempt))
        return max(delay, min(retry_after or 0.0, 60.0))

    def _call(
        self,
        method: str,
        url: str,
        *,
        body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        retry_on: Callable[[LLMError], bool],
    ) -> dict[str, Any]:
        attempt = 0
        while True:
            try:
                return request_json(
                    self._client,
                    method,
                    url,
                    provider=self.kind,
                    headers=self._headers,
                    body=body,
                    params=params,
                    secret=self._api_key,
                )
            except LLMError as exc:
                if attempt >= self.max_retries or not retry_on(exc):
                    raise
                self._sleep(self._backoff(attempt, exc.retry_after))
                attempt += 1

    @staticmethod
    def _retry_reads(exc: LLMError) -> bool:
        return exc.retryable

    @staticmethod
    def _retry_create(exc: LLMError) -> bool:
        return isinstance(exc, LLMTransientError) and exc.status in (429, 503)

    # -- tool helpers --------------------------------------------------------------------------
    @staticmethod
    def mcp_server_tool(
        name: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        allowed_tools: list[str] | None = None,
    ) -> dict[str, Any]:
        """An ``mcp_server`` tool for an *approved* server (the tool broker decides which servers qualify).

        Only HTTPS URLs are accepted: the provider connects to the server, and credentials in ``headers``
        must never travel in clear text.
        """
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
            raise LLMValidationError(
                "MCP server URLs must be https:// without embedded credentials", code="llm_invalid_mcp_server"
            )
        if not re.match(r"^[A-Za-z0-9_\-]{1,64}$", name):
            raise LLMValidationError("Invalid MCP server name", code="llm_invalid_mcp_server")
        tool: dict[str, Any] = {"type": "mcp_server", "name": name, "url": url}
        if headers:
            tool["headers"] = dict(headers)
        if allowed_tools is not None:
            tool["allowed_tools"] = [{"mode": "auto", "tools": list(allowed_tools)}]
        return tool

    @staticmethod
    def function_result_input(call_id: str, name: str, result: Any) -> dict[str, Any]:
        return {"type": "function_result", "call_id": call_id, "name": name, "result": result}

    @staticmethod
    def _plain_allowed_tools(body: dict[str, Any]) -> dict[str, Any] | None:
        """Fallback body with ``allowed_tools`` flattened to a string list (older API revisions)."""
        tools = body.get("tools")
        if not isinstance(tools, list):
            return None
        changed = False
        flattened: list[Any] = []
        for tool in tools:
            if (
                isinstance(tool, dict)
                and tool.get("type") == "mcp_server"
                and isinstance(tool.get("allowed_tools"), list)
            ):
                names: list[str] = []
                for entry in tool["allowed_tools"]:
                    if isinstance(entry, dict):
                        names.extend(str(n) for n in entry.get("tools") or [])
                        changed = True
                    else:
                        names.append(str(entry))
                flattened.append({**tool, "allowed_tools": names})
            else:
                flattened.append(tool)
        return {**body, "tools": flattened} if changed else None

    # -- API -----------------------------------------------------------------------------------
    def create(self, **body: Any) -> InteractionSnapshot:
        """POST /interactions with the given body (model form or agent form)."""
        if bool(body.get("model")) == bool(body.get("agent")):
            raise LLMValidationError("Exactly one of 'model' or 'agent' is required", code="llm_invalid_request")
        if "input" not in body:
            raise LLMValidationError("'input' is required", code="llm_invalid_request")
        if body.get("stream"):
            raise LLMValidationError(
                "Use background=True and stream(id) to follow an interaction", code="llm_invalid_request"
            )
        if body.get("store") is False and (body.get("background") or body.get("previous_interaction_id")):
            raise LLMValidationError(
                "store=false is incompatible with background and previous_interaction_id", code="llm_invalid_request"
            )
        try:
            data = self._call("POST", self._url(), body=body, retry_on=self._retry_create)
        except LLMValidationError as exc:
            fallback = self._plain_allowed_tools(body) if exc.status == 400 else None
            if fallback is None:
                raise
            data = self._call("POST", self._url(), body=fallback, retry_on=self._retry_create)
        return parse_interaction(data)

    def create_deep_research(
        self,
        query: str,
        *,
        agent: str | None = None,
        collaborative_planning: bool = False,
        previous_interaction_id: str | None = None,
        thinking_summaries: str = "auto",
        tools: list[dict[str, Any]] | None = None,
        visualization: str = "auto",
    ) -> InteractionSnapshot:
        """Start (or continue) a background Deep Research interaction."""
        if not query or not query.strip():
            raise LLMValidationError("A research query is required", code="llm_invalid_request")
        if thinking_summaries not in ("auto", "none"):
            raise LLMValidationError("thinking_summaries must be 'auto' or 'none'", code="llm_invalid_request")
        body: dict[str, Any] = {
            "agent": agent or self.default_agent,
            "input": query,
            "background": True,
            "agent_config": {
                "type": "deep-research",
                "thinking_summaries": thinking_summaries,
                "collaborative_planning": bool(collaborative_planning),
                "visualization": visualization,
            },
            "tools": [dict(t) for t in (tools if tools is not None else DEFAULT_DEEP_RESEARCH_TOOLS)],
        }
        if previous_interaction_id:
            body["previous_interaction_id"] = previous_interaction_id
        return self.create(**body)

    def get(self, interaction_id: str, *, include_input: bool = False) -> InteractionSnapshot:
        params = {"include_input": "true"} if include_input else None
        data = self._call("GET", self._url(interaction_id), params=params, retry_on=self._retry_reads)
        return parse_interaction(data)

    def cancel(self, interaction_id: str) -> InteractionSnapshot:
        data = self._call("POST", self._url(interaction_id, "cancel"), retry_on=self._retry_reads)
        if not data.get("id"):
            data = {**data, "id": interaction_id, "status": data.get("status") or "cancelled"}
        return parse_interaction(data)

    def delete(self, interaction_id: str) -> None:
        self._call("DELETE", self._url(interaction_id), retry_on=self._retry_reads)

    def wait(
        self,
        interaction_id: str,
        *,
        poll_interval: float = 10.0,
        timeout: float = 3600.0,
        is_cancelled: Callable[[], bool] | None = None,
        on_poll: Callable[[InteractionSnapshot], None] | None = None,
    ) -> InteractionSnapshot:
        """Poll until the interaction is terminal (or ``requires_action``). Raises ``LLMTransientError`` on
        timeout; stops early when ``is_cancelled()`` returns true (the interaction is then cancelled)."""
        deadline = time.monotonic() + timeout
        while True:
            snapshot = self.get(interaction_id)
            if on_poll is not None:
                on_poll(snapshot)
            if snapshot.is_terminal or snapshot.requires_action:
                return snapshot
            if is_cancelled is not None and is_cancelled():
                return self.cancel(interaction_id)
            if time.monotonic() >= deadline:
                raise LLMTransientError(
                    "Timed out waiting for the interaction to finish",
                    code="llm_interaction_timeout",
                    provider=self.kind,
                )
            self._sleep(poll_interval)

    def stream(self, interaction_id: str, last_event_id: str | None = None) -> Iterator[InteractionEvent]:
        """Follow an interaction's event stream (resumable). Reconnects with ``last_event_id`` after transient
        disconnects (bounded); stops after ``interaction.completed``, an ``error`` event or ``[DONE]``."""
        cursor = last_event_id
        attempt = 0
        while True:
            params: dict[str, str] = {"stream": "true"}
            if cursor:
                params["last_event_id"] = cursor
            try:
                with self._client.stream(
                    "GET",
                    self._url(interaction_id),
                    params=params,
                    headers={**self._headers, "accept": "text/event-stream"},
                ) as response:
                    raise_for_stream_status(self.kind, response, secret=self._api_key)
                    for sse in iter_sse(response.iter_lines()):
                        if sse.raw:
                            error = raw_json_error(self.kind, sse.data, secret=self._api_key)
                            if error is not None:
                                raise error
                            continue
                        if sse.data.strip() == "[DONE]":
                            return
                        try:
                            payload = json.loads(sse.data)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(payload, dict):
                            continue
                        event = parse_event(payload, sse_event=sse.event, sse_id=sse.id)
                        if event.event_id:
                            cursor = event.event_id
                        attempt = 0
                        yield event
                        if event.is_terminal:
                            return
                return  # the server closed the stream normally
            except httpx.HTTPError as exc:  # disconnects: resume from the last event id
                if attempt >= self.max_retries:
                    raise map_transport_error(self.kind, exc) from None
                self._sleep(self._backoff(attempt, None))
                attempt += 1
            except LLMError as exc:
                if not exc.retryable or attempt >= self.max_retries:
                    raise
                self._sleep(self._backoff(attempt, exc.retry_after))
                attempt += 1
