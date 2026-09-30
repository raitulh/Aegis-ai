"""Gemini generateContent wire format: request building, response parsing, error mapping, SSE parsing."""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import BaseModel, Field

from aegis_api.lab.llm.errors import (
    LLMPermanentError,
    LLMPolicyError,
    LLMTransientError,
    LLMUnavailableError,
    LLMValidationError,
)
from aegis_api.lab.llm.providers._http import iter_sse, map_http_error, map_transport_error, parse_retry_after
from aegis_api.lab.llm.providers.gemini.parsing import (
    build_contents,
    build_generate_body,
    parse_generate_response,
    to_gemini_schema,
)
from aegis_api.lab.llm.schemas import LLMMessage, LLMRequest, ToolSpec


class Finding(BaseModel):
    title: str = Field(default="x", pattern="^[A-Z]")
    score: float
    kind: str = Field(json_schema_extra={"const": "finding"})


class Report(BaseModel):
    findings: list[Finding]


def request(**kwargs) -> LLMRequest:
    base = {"task_type": "summarization", "messages": [LLMMessage(role="user", content="hello")]}
    base.update(kwargs)
    return LLMRequest(**base)


# --- request building ------------------------------------------------------------------------------
def test_body_structured_output_uses_response_json_schema_only():
    req = request(system="be precise", response_model=Report, temperature=0.2, max_output_tokens=512, seed=7)
    body = build_generate_body(req, response_schema=req.effective_response_schema())
    assert body["systemInstruction"] == {"parts": [{"text": "be precise"}]}
    assert body["contents"] == [{"role": "user", "parts": [{"text": "hello"}]}]
    config = body["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert "responseSchema" not in config
    assert config["temperature"] == 0.2 and config["maxOutputTokens"] == 512 and config["seed"] == 7
    assert "thinkingConfig" not in config
    schema = config["responseJsonSchema"]
    assert "$defs" in schema and schema["properties"]["findings"]["items"] == {"$ref": "#/$defs/Finding"}
    finding = schema["$defs"]["Finding"]
    assert "default" not in json.dumps(finding) and "pattern" not in finding["properties"]["title"]
    assert finding["properties"]["kind"]["enum"] == ["finding"]


def test_body_thinking_level_only_when_requested():
    body = build_generate_body(request(metadata={"thinking_level": "high"}), response_schema=None)
    assert body["generationConfig"] == {"thinkingConfig": {"thinkingLevel": "HIGH"}}
    with pytest.raises(LLMValidationError):
        build_generate_body(request(metadata={"thinking_level": "extreme"}), response_schema=None)


def test_body_tools_are_separate_objects():
    req = request(
        tools=[
            ToolSpec(
                name="lookup",
                description="Look up",
                parameters={"type": "object", "properties": {"q": {"type": "string", "default": ""}}},
            )
        ],
        tool_choice="any",
        builtin_tools=["google_search", "url_context", "code_execution"],
    )
    body = build_generate_body(req, response_schema=None)
    tools = body["tools"]
    assert tools[0] == {
        "functionDeclarations": [
            {
                "name": "lookup",
                "description": "Look up",
                "parametersJsonSchema": {"type": "object", "properties": {"q": {"type": "string"}}},
            }
        ]
    }
    assert tools[1:] == [{"googleSearch": {}}, {"urlContext": {}}, {"codeExecution": {}}]
    assert body["toolConfig"] == {"functionCallingConfig": {"mode": "ANY"}}


def test_system_messages_are_merged_into_system_instruction():
    req = request(system="A", messages=[LLMMessage(role="system", content="B"), LLMMessage(role="user", content="q")])
    system, contents = build_contents(req)
    assert system == "A\n\nB" and contents == [{"role": "user", "parts": [{"text": "q"}]}]


def test_function_round_trip_replays_raw_turn():
    raw = {
        "role": "model",
        "parts": [
            {"functionCall": {"id": "fc_1", "name": "lookup", "args": {"q": "x"}}, "thoughtSignature": "c2ln"},
            {"functionCall": {"name": "legacy", "args": {}}},
        ],
    }
    req = request(
        messages=[
            LLMMessage(role="user", content="find x"),
            LLMMessage(role="assistant", content=""),
            LLMMessage(role="tool", tool_call_id="fc_1", content='{"answer": 42}'),
            LLMMessage(role="tool", name="legacy", tool_call_id="gemini-call-1", content="plain text"),
        ],
        previous_turns=[raw],
    )
    _, contents = build_contents(req)
    assert contents[0] == {"role": "user", "parts": [{"text": "find x"}]}
    assert contents[1] == raw  # verbatim, the empty assistant text message is superseded
    assert contents[2] == {
        "role": "user",
        "parts": [
            {"functionResponse": {"id": "fc_1", "name": "lookup", "response": {"result": {"answer": 42}}}},
            {"functionResponse": {"name": "legacy", "response": {"result": "plain text"}}},
        ],
    }


def test_tool_results_require_previous_turn():
    req = request(messages=[LLMMessage(role="user", content="q"), LLMMessage(role="tool", name="t", content="{}")])
    with pytest.raises(LLMValidationError, match="raw_assistant_turn"):
        build_contents(req)
    with pytest.raises(LLMValidationError):
        build_contents(request(previous_turns=[{"role": "model", "parts": []}]))


def test_schema_reduction_keeps_property_names_that_look_like_keywords():
    schema = {"type": "object", "properties": {"default": {"type": "string", "default": "a"}}, "examples": [1]}
    assert to_gemini_schema(schema) == {"type": "object", "properties": {"default": {"type": "string"}}}


# --- response parsing ------------------------------------------------------------------------------
FULL_RESPONSE = {
    "candidates": [
        {
            "content": {
                "role": "model",
                "parts": [
                    {"text": "private reasoning summary", "thought": True},
                    {"thought": True, "thoughtSignature": "sig-thought", "text": "more reasoning"},
                    {"text": "Café results: "},
                    {"text": "accuracy 91%."},
                    {"executableCode": {"language": "PYTHON", "code": "print(1)"}},
                    {"codeExecutionResult": {"outcome": "OUTCOME_OK", "output": "1"}},
                    {
                        "functionCall": {"id": "fc_9", "name": "fetch", "args": {"url": "u"}},
                        "thoughtSignature": "sig-fc",
                    },
                ],
            },
            "finishReason": "STOP",
            "groundingMetadata": {
                "webSearchQueries": ["accuracy benchmark"],
                "groundingChunks": [
                    {"web": {"uri": "https://a.example", "title": "A"}},
                    {"web": {"uri": "https://b.example", "title": "B"}},
                    {"web": {"uri": "https://a.example", "title": "A again"}},
                    {"retrievedContext": {"uri": "gs://x"}},
                ],
                "groundingSupports": [
                    {
                        "segment": {"startIndex": 15, "endIndex": 28, "text": "accuracy 91%."},
                        "groundingChunkIndices": [1],
                    },
                    {"segment": {"endIndex": 14}, "groundingChunkIndices": [0, 2]},
                ],
            },
            "urlContextMetadata": {
                "urlMetadata": [
                    {"retrievedUrl": "https://c.example", "urlRetrievalStatus": "URL_RETRIEVAL_STATUS_SUCCESS"},
                    {"retrievedUrl": "https://d.example", "urlRetrievalStatus": "URL_RETRIEVAL_STATUS_ERROR"},
                ]
            },
        }
    ],
    "usageMetadata": {
        "promptTokenCount": 100,
        "candidatesTokenCount": 20,
        "cachedContentTokenCount": 40,
        "thoughtsTokenCount": 55,
        "toolUsePromptTokenCount": 5,
        "totalTokenCount": 180,
    },
    "modelVersion": "gemini-3.8-flash-001",
    "responseId": "resp-123",
}


def test_parse_excludes_thoughts_and_extracts_everything():
    parsed = parse_generate_response(FULL_RESPONSE)
    assert parsed.text == "Café results: accuracy 91%."
    assert "reasoning" not in parsed.text
    assert [(c.id, c.name, c.arguments) for c in parsed.tool_calls] == [("fc_9", "fetch", {"url": "u"})]
    assert parsed.finish_reason == "tool_calls"
    assert parsed.usage.input_tokens == 105 and parsed.usage.output_tokens == 20
    assert parsed.usage.cached_tokens == 40 and parsed.usage.thinking_tokens == 55
    assert parsed.model_version == "gemini-3.8-flash-001" and parsed.request_id == "resp-123"
    assert parsed.metadata["provider_code_execution"] == [
        {"kind": "code", "language": "PYTHON", "code": "print(1)"},
        {"kind": "result", "outcome": "OUTCOME_OK", "output": "1"},
    ]
    assert parsed.metadata["search_queries"] == ["accuracy benchmark"]
    urls = [c.url for c in parsed.citations]
    assert urls == ["https://a.example", "https://b.example", "https://c.example"]
    a, b = parsed.citations[0], parsed.citations[1]
    assert (a.start_index, a.end_index) == (0, 13)  # "Café" has a 2-byte é: byte 14 → char 13
    assert (b.start_index, b.end_index) == (14, 27)
    assert parsed.text[b.start_index : b.end_index] == "accuracy 91%."


def test_raw_turn_keeps_signatures_but_never_thought_text():
    turn = parse_generate_response(FULL_RESPONSE).raw_assistant_turn
    assert turn is not None and turn["role"] == "model"
    dumped = json.dumps(turn)
    assert "reasoning" not in dumped
    assert "sig-thought" in dumped and "sig-fc" in dumped
    assert turn["parts"][0] == {"thought": True, "thoughtSignature": "sig-thought"}
    assert turn["parts"][-1]["functionCall"]["id"] == "fc_9"


def test_blocked_prompt_is_a_policy_error():
    with pytest.raises(LLMPolicyError, match="SAFETY") as info:
        parse_generate_response({"promptFeedback": {"blockReason": "SAFETY"}})
    assert info.value.code == "llm_prompt_blocked" and not info.value.retryable and not info.value.fallback


@pytest.mark.parametrize("reason", ["SAFETY", "RECITATION", "PROHIBITED_CONTENT", "SPII", "BLOCKLIST"])
def test_policy_finish_reasons(reason):
    data = {"candidates": [{"content": {"parts": [{"text": "x"}]}, "finishReason": reason}]}
    with pytest.raises(LLMPolicyError):
        parse_generate_response(data)


def test_max_tokens_sets_finish_reason():
    data = {"candidates": [{"content": {"parts": [{"text": '{"a": '}]}, "finishReason": "MAX_TOKENS"}]}
    parsed = parse_generate_response(data)
    assert parsed.finish_reason == "max_tokens" and parsed.text == '{"a": '


def test_empty_and_malformed_responses_are_transient():
    with pytest.raises(LLMTransientError):
        parse_generate_response({})
    with pytest.raises(LLMTransientError):
        parse_generate_response({"candidates": [{"finishReason": "MALFORMED_FUNCTION_CALL"}]})
    with pytest.raises(LLMValidationError):
        parse_generate_response({"candidates": [{"finishReason": "MISSING_THOUGHT_SIGNATURE"}]})


def test_function_call_without_id_gets_synthetic_id():
    data = {
        "candidates": [{"content": {"parts": [{"functionCall": {"name": "f", "args": {}}}]}, "finishReason": "STOP"}]
    }
    call = parse_generate_response(data).tool_calls[0]
    assert call.id.startswith("gemini-call-") and call.name == "f"


# --- errors ----------------------------------------------------------------------------------------
def response(status: int, body: object = None, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, json=body if body is not None else {}, headers=headers or {})


def test_http_error_mapping():
    rate = map_http_error(
        "gemini", response(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED"}}, {"Retry-After": "7"})
    )
    assert isinstance(rate, LLMTransientError) and rate.retry_after == 7.0 and rate.code == "llm_rate_limited"
    for status in (500, 502, 503, 504):
        assert isinstance(map_http_error("gemini", response(status)), LLMTransientError)
    bad = map_http_error(
        "gemini", response(400, {"error": {"message": "Invalid JSON payload", "status": "INVALID_ARGUMENT"}})
    )
    assert isinstance(bad, LLMValidationError) and "INVALID_ARGUMENT" in bad.message
    assert isinstance(map_http_error("gemini", response(401)), LLMPermanentError)
    assert map_http_error("gemini", response(403)).code == "llm_auth_failed"
    missing = map_http_error("gemini", response(404))
    assert isinstance(missing, LLMPermanentError) and missing.code == "llm_model_not_found" and missing.fallback


def test_errors_never_contain_the_api_key():
    leaked = response(400, {"error": {"message": "API key SECRET-KEY-123 not valid"}})
    error = map_http_error("gemini", leaked, secret="SECRET-KEY-123")
    assert "SECRET-KEY-123" not in error.message and "[redacted]" in error.message


def test_retry_info_detail_and_http_date():
    detail = [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "31s"}]
    assert parse_retry_after(response(429), detail) == 31.0
    assert parse_retry_after(response(429, headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"})) == 0.0


def test_transport_errors():
    assert isinstance(map_transport_error("gemini", httpx.ConnectError("x")), LLMUnavailableError)
    assert isinstance(map_transport_error("gemini", httpx.ReadTimeout("x")), LLMTransientError)
    assert isinstance(map_transport_error("gemini", httpx.RemoteProtocolError("x")), LLMTransientError)


def test_sse_parser():
    lines = [
        ": keep-alive",
        "event: step.delta",
        "id: 7",
        'data: {"a":',
        "data: 1}",
        "",
        '{"error": {"code": 503,',
        '"message": "overloaded"}}',
        "",
        "data: [DONE]",
    ]
    events = list(iter_sse(lines))
    assert events[0].event == "step.delta" and events[0].id == "7" and json.loads(events[0].data) == {"a": 1}
    assert events[1].raw and json.loads(events[1].data)["error"]["code"] == 503
    assert events[2].data == "[DONE]"
