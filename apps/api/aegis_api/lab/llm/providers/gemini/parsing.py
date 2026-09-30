"""Gemini ``generateContent`` wire format: request building and response parsing (no IO).

Field names follow the v1beta REST API (camelCase JSON, UPPER_SNAKE enums):

* structured output uses ``responseMimeType="application/json"`` + ``responseJsonSchema`` (never together
  with ``responseSchema``); function declarations use ``parametersJsonSchema``;
* built-in tools are *separate* tool objects (``googleSearch`` / ``urlContext`` / ``codeExecution``);
* thought parts are excluded from the text and **never** persisted (chain-of-thought is not stored); the
  assistant turn is returned verbatim for function-call round trips (so ``thoughtSignature`` parts are echoed
  back), except that the text of thought-summary parts is removed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from aegis_api.lab.llm.errors import LLMPolicyError, LLMTransientError, LLMValidationError
from aegis_api.lab.llm.schemas import Citation, LLMMessage, LLMRequest, ToolCall, Usage

SUPPORTED_SCHEMA_KEYS = frozenset(
    {
        "$defs",
        "$ref",
        "type",
        "format",
        "title",
        "description",
        "enum",
        "items",
        "prefixItems",
        "minItems",
        "maxItems",
        "minimum",
        "maximum",
        "anyOf",
        "oneOf",
        "properties",
        "additionalProperties",
        "required",
        "propertyOrdering",
    }
)
_NAME_MAPS = frozenset({"properties", "$defs"})  # keys of these objects are names, not keywords
POLICY_FINISH_REASONS = frozenset(
    {"SAFETY", "RECITATION", "PROHIBITED_CONTENT", "SPII", "BLOCKLIST", "IMAGE_SAFETY", "IMAGE_PROHIBITED_CONTENT"}
)
THINKING_LEVELS = frozenset({"minimal", "low", "medium", "high"})
BUILTIN_TOOL_OBJECTS = {"google_search": "googleSearch", "url_context": "urlContext", "code_execution": "codeExecution"}
SYNTHETIC_CALL_PREFIX = "gemini-call-"


def to_gemini_schema(schema: Any) -> Any:
    """Reduce a JSON Schema to the keywords Gemini's ``responseJsonSchema`` supports.

    ``const`` becomes a one-value ``enum``; unsupported keywords (``default``, ``pattern``, ``examples`` …)
    are dropped — the gateway validates the full schema locally after the call anyway.
    """
    if isinstance(schema, list):
        return [to_gemini_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    result: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "const":
            result.setdefault("enum", [value])
        elif key in _NAME_MAPS and isinstance(value, dict):
            result[key] = {name: to_gemini_schema(sub) for name, sub in value.items()}
        elif key in ("enum", "required", "propertyOrdering") or (
            key == "additionalProperties" and isinstance(value, bool)
        ):
            result[key] = value
        elif key in SUPPORTED_SCHEMA_KEYS:
            result[key] = to_gemini_schema(value)
    return result


def _tool_result_payload(content: str) -> dict[str, Any]:
    try:
        value = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return {"result": content}
    if isinstance(value, dict) and set(value) == {"error"}:
        return value
    return {"result": value}


def _call_names(turn: dict[str, Any]) -> dict[str, str]:
    names: dict[str, str] = {}
    for part in turn.get("parts") or []:
        call = part.get("functionCall") if isinstance(part, dict) else None
        if isinstance(call, dict) and call.get("name"):
            names[str(call.get("id") or call["name"])] = str(call["name"])
    return names


def build_contents(request: LLMRequest) -> tuple[str | None, list[dict[str, Any]]]:
    """(system instruction text, contents). Tool-result groups are paired with ``previous_turns`` in order."""
    system_parts = [request.system] if request.system else []
    contents: list[dict[str, Any]] = []
    turns = list(request.previous_turns)
    turn_index = 0
    messages = request.messages
    last_was_assistant_text = False
    i = 0
    while i < len(messages):
        message = messages[i]
        if message.role == "system":
            system_parts.append(message.content)
            i += 1
            continue
        if message.role == "tool":
            group: list[LLMMessage] = []
            while i < len(messages) and messages[i].role == "tool":
                group.append(messages[i])
                i += 1
            if turn_index >= len(turns):
                raise LLMValidationError(
                    "Function results need the provider-native assistant turn: pass response.raw_assistant_turn "
                    "in previous_turns",
                    code="llm_missing_assistant_turn",
                    provider="gemini",
                )
            turn = turns[turn_index]
            turn_index += 1
            if not isinstance(turn, dict) or not isinstance(turn.get("parts"), list):
                raise LLMValidationError(
                    "previous_turns entry is not a Gemini content object",
                    code="llm_bad_previous_turn",
                    provider="gemini",
                )
            if last_was_assistant_text:
                contents.pop()  # superseded by the verbatim provider turn
            contents.append({**turn, "role": turn.get("role") or "model"})
            names = _call_names(turn)
            parts = []
            for tool_message in group:
                call_id = tool_message.tool_call_id
                name = tool_message.name or (names.get(call_id) if call_id else None)
                if not name:
                    raise LLMValidationError(
                        "Tool result messages need the function name", code="llm_bad_tool_result", provider="gemini"
                    )
                response: dict[str, Any] = {"name": name, "response": _tool_result_payload(tool_message.content)}
                if call_id and not call_id.startswith(SYNTHETIC_CALL_PREFIX):
                    response = {"id": call_id, **response}
                parts.append({"functionResponse": response})
            contents.append({"role": "user", "parts": parts})
            last_was_assistant_text = False
            continue
        role = "model" if message.role == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": message.content}]})
        last_was_assistant_text = message.role == "assistant"
        i += 1
    if turn_index < len(turns):
        raise LLMValidationError(
            f"previous_turns has {len(turns)} entries but only {turn_index} tool-result group(s) follow them",
            code="llm_bad_previous_turn",
            provider="gemini",
        )
    system = "\n\n".join(part for part in system_parts if part) or None
    return system, contents


def build_generate_body(request: LLMRequest, *, response_schema: dict[str, Any] | None) -> dict[str, Any]:
    system, contents = build_contents(request)
    body: dict[str, Any] = {"contents": contents}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    config: dict[str, Any] = {}
    if request.temperature is not None:
        config["temperature"] = request.temperature
    if request.max_output_tokens is not None:
        config["maxOutputTokens"] = request.max_output_tokens
    if request.seed is not None:
        config["seed"] = request.seed
    if response_schema is not None:
        config["responseMimeType"] = "application/json"
        config["responseJsonSchema"] = to_gemini_schema(response_schema)
    level = request.metadata.get("thinking_level")
    if level is not None:
        level_text = str(level).lower()
        if level_text not in THINKING_LEVELS:
            raise LLMValidationError(
                f"thinking_level must be one of {sorted(THINKING_LEVELS)}",
                code="llm_invalid_request",
                provider="gemini",
            )
        config["thinkingConfig"] = {"thinkingLevel": level_text.upper()}
    if config:
        body["generationConfig"] = config
    tools: list[dict[str, Any]] = []
    if request.tools:
        tools.append(
            {
                "functionDeclarations": [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "parametersJsonSchema": to_gemini_schema(tool.parameters),
                    }
                    for tool in request.tools
                ]
            }
        )
        body["toolConfig"] = {"functionCallingConfig": {"mode": request.tool_choice.upper()}}
    for builtin in request.builtin_tools:
        tools.append({BUILTIN_TOOL_OBJECTS[builtin]: {}})
    if tools:
        body["tools"] = tools
    return body


# --- responses ----------------------------------------------------------------------------------
@dataclass
class ParsedGeneration:
    text: str
    tool_calls: list[ToolCall]
    citations: list[Citation]
    usage: Usage
    finish_reason: str | None
    model_version: str | None
    request_id: str | None
    raw_assistant_turn: dict[str, Any] | None
    metadata: dict[str, Any] = field(default_factory=dict)


def parse_usage(usage: dict[str, Any] | None) -> Usage:
    usage = usage or {}
    return Usage(
        input_tokens=int(usage.get("promptTokenCount") or 0) + int(usage.get("toolUsePromptTokenCount") or 0),
        output_tokens=int(usage.get("candidatesTokenCount") or 0),
        cached_tokens=int(usage.get("cachedContentTokenCount") or 0),
        thinking_tokens=int(usage.get("thoughtsTokenCount") or 0),
    )


def _byte_to_char(text_bytes: bytes, offset: Any) -> int | None:
    if not isinstance(offset, int) or offset < 0:
        return None
    return len(text_bytes[:offset].decode("utf-8", errors="ignore"))


def parse_citations(candidate: dict[str, Any], text: str) -> list[Citation]:
    """Web grounding chunks (with the first supporting segment, converted from UTF-8 byte offsets to
    character offsets) followed by successfully retrieved URL-context URLs. Deduplicated by URL."""
    grounding = candidate.get("groundingMetadata") or {}
    chunks = grounding.get("groundingChunks") or []
    first_segment: dict[int, dict[str, Any]] = {}
    for support in grounding.get("groundingSupports") or []:
        segment = support.get("segment") or {}
        for index in support.get("groundingChunkIndices") or []:
            if isinstance(index, int):
                first_segment.setdefault(index, segment)
    text_bytes = text.encode("utf-8")
    citations: list[Citation] = []
    seen: set[str] = set()
    for index, chunk in enumerate(chunks):
        web = chunk.get("web") if isinstance(chunk, dict) else None
        if not isinstance(web, dict) or not web.get("uri"):
            continue
        url = str(web["uri"])
        if url in seen:
            continue
        seen.add(url)
        segment = first_segment.get(index, {})
        start = _byte_to_char(text_bytes, segment.get("startIndex", 0 if segment else None))
        end = _byte_to_char(text_bytes, segment.get("endIndex"))
        citations.append(Citation(url=url, title=web.get("title"), start_index=start, end_index=end))
    url_context = candidate.get("urlContextMetadata") or {}
    for item in url_context.get("urlMetadata") or []:
        if not isinstance(item, dict) or not item.get("retrievedUrl"):
            continue
        status = str(item.get("urlRetrievalStatus") or "URL_RETRIEVAL_STATUS_SUCCESS")
        url = str(item["retrievedUrl"])
        if status.endswith("SUCCESS") and url not in seen:
            seen.add(url)
            citations.append(Citation(url=url))
    return citations


def sanitize_assistant_turn(content: dict[str, Any] | None) -> dict[str, Any] | None:
    """The candidate content for replay: verbatim, minus the *text* of thought-summary parts (a part that
    only carried thought text is dropped; signatures are kept)."""
    if not isinstance(content, dict):
        return None
    parts = []
    for part in content.get("parts") or []:
        if isinstance(part, dict) and part.get("thought"):
            if part.get("thoughtSignature"):
                parts.append({key: value for key, value in part.items() if key != "text"})
            continue
        parts.append(part)
    return {**content, "role": content.get("role") or "model", "parts": parts}


def normalize_finish(reason: str | None, has_tool_calls: bool) -> str | None:
    if has_tool_calls:
        return "tool_calls"
    if reason is None:
        return None
    return {"STOP": "stop", "MAX_TOKENS": "max_tokens", "FINISH_REASON_UNSPECIFIED": None}.get(reason, reason.lower())


def check_blocked(data: dict[str, Any]) -> dict[str, Any]:
    """Return ``candidates[0]`` or raise the mapped error for blocked/empty responses."""
    candidates = data.get("candidates") or []
    if not candidates:
        block = (data.get("promptFeedback") or {}).get("blockReason")
        if block:
            raise LLMPolicyError(
                f"The provider blocked the prompt ({block})", code="llm_prompt_blocked", provider="gemini"
            )
        raise LLMTransientError("Gemini returned no candidates", code="llm_empty_response", provider="gemini")
    candidate = candidates[0] if isinstance(candidates[0], dict) else {}
    reason = candidate.get("finishReason")
    if reason in POLICY_FINISH_REASONS:
        raise LLMPolicyError(
            f"The provider withheld the response ({reason})", code="llm_response_blocked", provider="gemini"
        )
    if reason == "MALFORMED_FUNCTION_CALL":
        raise LLMTransientError(
            "Gemini produced a malformed function call", code="llm_malformed_function_call", provider="gemini"
        )
    if reason == "MISSING_THOUGHT_SIGNATURE":
        raise LLMValidationError(
            "Gemini requires the previous assistant turn with its thought signatures",
            code="llm_missing_thought_signature",
            provider="gemini",
        )
    return candidate


def text_of(candidate: dict[str, Any]) -> str:
    parts = (candidate.get("content") or {}).get("parts") or []
    return "".join(
        str(part["text"]) for part in parts if isinstance(part, dict) and "text" in part and not part.get("thought")
    )


def parse_generate_response(data: dict[str, Any]) -> ParsedGeneration:
    candidate = check_blocked(data)
    content = candidate.get("content") or {}
    parts = [p for p in content.get("parts") or [] if isinstance(p, dict)]
    text = text_of(candidate)
    tool_calls: list[ToolCall] = []
    code_execution: list[dict[str, Any]] = []
    for index, part in enumerate(parts):
        if part.get("thought"):
            continue
        call = part.get("functionCall")
        if isinstance(call, dict) and call.get("name"):
            args = call.get("args")
            tool_calls.append(
                ToolCall(
                    id=str(call.get("id") or f"{SYNTHETIC_CALL_PREFIX}{index}"),
                    name=str(call["name"]),
                    arguments=args if isinstance(args, dict) else {},
                )
            )
        executable = part.get("executableCode")
        if isinstance(executable, dict):
            code_execution.append(
                {"kind": "code", "language": executable.get("language"), "code": executable.get("code")}
            )
        result = part.get("codeExecutionResult")
        if isinstance(result, dict):
            code_execution.append({"kind": "result", "outcome": result.get("outcome"), "output": result.get("output")})
    metadata: dict[str, Any] = {}
    if code_execution:
        metadata["provider_code_execution"] = code_execution
    queries = (candidate.get("groundingMetadata") or {}).get("webSearchQueries")
    if queries:
        metadata["search_queries"] = [str(q) for q in queries]
    return ParsedGeneration(
        text=text,
        tool_calls=tool_calls,
        citations=parse_citations(candidate, text),
        usage=parse_usage(data.get("usageMetadata")),
        finish_reason=normalize_finish(candidate.get("finishReason"), bool(tool_calls)),
        model_version=data.get("modelVersion"),
        request_id=data.get("responseId"),
        raw_assistant_turn=sanitize_assistant_turn(content) if content else None,
        metadata=metadata,
    )
