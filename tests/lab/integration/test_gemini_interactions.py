"""Gemini Interactions API / Deep Research client against a mocked HTTP transport (no network, no DB)."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator

import httpx
import pytest

from aegis_api.lab.llm.errors import LLMPermanentError, LLMTransientError, LLMValidationError
from aegis_api.lab.llm.providers.gemini.interactions import GeminiInteractionsClient, parse_interaction

BASE = "https://gemini.test/v1beta"
KEY = "test-api-key"


class Recorder:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.handler = handler
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)

    def bodies(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests if r.content]


def client_for(
    handler: Callable[[httpx.Request], httpx.Response], **kwargs
) -> tuple[GeminiInteractionsClient, Recorder]:
    recorder = Recorder(handler)
    sleeps: list[float] = []
    client = GeminiInteractionsClient(
        KEY, BASE, 5.0, transport=httpx.MockTransport(recorder), sleep=sleeps.append, **kwargs
    )
    client.sleeps = sleeps  # type: ignore[attr-defined]
    return client, recorder


COMPLETED = {
    "id": "int-1",
    "status": "completed",
    "agent": "deep-research-preview-04-2026",
    "steps": [
        {"type": "user_input", "content": [{"type": "text", "text": "What limits perovskite stability?"}]},
        {"type": "thought", "signature": "abc", "summary": [{"type": "text", "text": "SECRET CHAIN OF THOUGHT"}]},
        {"type": "google_search_call", "id": "s1", "arguments": {"queries": ["perovskite degradation"]}},
        {"type": "google_search_result", "call_id": "s1", "result": [{"url": "https://search.example", "title": "S"}]},
        {"type": "url_context_call", "id": "u1", "arguments": {"urls": ["https://paper.example/1"]}},
        {
            "type": "url_context_result",
            "call_id": "u1",
            "result": [{"url": "https://paper.example/1", "status": "success"}],
        },
        {"type": "model_output", "content": [{"type": "text", "text": "Interim note."}]},
        {"type": "code_execution_call", "id": "c1", "arguments": {"code": "print(2)", "language": "python"}},
        {"type": "code_execution_result", "call_id": "c1", "result": "2"},
        {"type": "thought", "summary": [{"type": "text", "text": "MORE PRIVATE THOUGHTS"}]},
        {
            "type": "model_output",
            "content": [
                {
                    "type": "text",
                    "text": "Moisture drives degradation.",
                    "annotations": [
                        {
                            "type": "url_citation",
                            "url": "https://paper.example/1",
                            "title": "Paper 1",
                            "start_index": 0,
                            "end_index": 8,
                        },
                    ],
                },
                {"type": "image", "mime_type": "image/png", "data": "iVBOR"},
                {
                    "type": "text",
                    "text": "Encapsulation helps.",
                    "annotations": [
                        {
                            "type": "url_citation",
                            "url": "https://paper.example/2",
                            "title": "Paper 2",
                            "start_index": 0,
                            "end_index": 13,
                        },
                        {
                            "type": "url_citation",
                            "url": "https://paper.example/1",
                            "title": "dup",
                            "start_index": 1,
                            "end_index": 2,
                        },
                        {"type": "file_citation", "file": "x"},
                    ],
                },
            ],
        },
    ],
    "usage": {
        "total_input_tokens": 1200,
        "total_output_tokens": 800,
        "total_thought_tokens": 400,
        "total_cached_tokens": 100,
        "total_tool_use_tokens": 50,
        "total_tokens": 2450,
    },
}


def test_deep_research_create_body_shape():
    client, rec = client_for(lambda r: httpx.Response(200, json={"id": "int-1", "status": "in_progress"}))
    snap = client.create_deep_research("What limits perovskite stability?")
    assert snap.id == "int-1" and snap.status == "in_progress" and not snap.is_terminal
    request = rec.requests[0]
    assert request.method == "POST" and str(request.url) == f"{BASE}/interactions"
    assert request.headers["x-goog-api-key"] == KEY and KEY not in str(request.url)
    assert rec.bodies()[0] == {
        "agent": "deep-research-preview-04-2026",
        "input": "What limits perovskite stability?",
        "background": True,
        "agent_config": {
            "type": "deep-research",
            "thinking_summaries": "auto",
            "collaborative_planning": False,
            "visualization": "auto",
        },
        "tools": [{"type": "google_search"}, {"type": "url_context"}, {"type": "code_execution"}],
    }


def test_deep_research_validation():
    client, rec = client_for(lambda r: httpx.Response(200, json={}))
    with pytest.raises(LLMValidationError):
        client.create_deep_research("   ")
    with pytest.raises(LLMValidationError):
        client.create_deep_research("q", thinking_summaries="verbose")
    with pytest.raises(LLMValidationError):
        client.create(model="m", agent="a", input="x")
    with pytest.raises(LLMValidationError):
        client.create(agent="a", input="x", background=True, store=False)
    assert rec.requests == []


def test_polling_parses_steps_citations_usage_without_thoughts():
    states = iter([{"id": "int-1", "status": "in_progress", "steps": []}, COMPLETED])
    client, rec = client_for(lambda r: httpx.Response(200, json=next(states)))
    polled: list[str] = []
    snap = client.wait("int-1", poll_interval=10, on_poll=lambda s: polled.append(s.status))
    assert polled == ["in_progress", "completed"] and client.sleeps == [10]  # type: ignore[attr-defined]
    assert [r.method for r in rec.requests] == ["GET", "GET"]
    assert str(rec.requests[0].url) == f"{BASE}/interactions/int-1"
    assert snap.is_terminal and snap.status == "completed"
    assert snap.text == "Moisture drives degradation.\n\nEncapsulation helps."
    assert [c.url for c in snap.citations] == ["https://paper.example/1", "https://paper.example/2"]
    second = snap.citations[1]
    offset = len("Moisture drives degradation.\n\n")
    assert (second.start_index, second.end_index) == (offset, offset + 13)
    assert snap.text[second.start_index : second.end_index] == "Encapsulation"
    assert snap.usage.input_tokens == 1250 and snap.usage.output_tokens == 800
    assert snap.usage.thinking_tokens == 400 and snap.usage.cached_tokens == 100
    types = [s["type"] for s in snap.steps]
    assert types[0] == "user_input" and types.count("thought") == 2
    assert {"type": "google_search_call", "summary": "google search: perovskite degradation"} in snap.steps
    assert {"type": "url_context_result", "summary": "url context results (1/1 retrieved)"} in snap.steps
    dumped = json.dumps(snap.model_dump())
    assert "SECRET CHAIN OF THOUGHT" not in dumped and "MORE PRIVATE THOUGHTS" not in dumped
    assert snap.raw["steps"][1] == {"type": "thought", "signature": "abc"}


def test_legacy_outputs_are_tolerated():
    legacy = {
        "id": "int-2",
        "status": "completed",
        "outputs": [
            {"type": "thought", "summary": "private"},
            {
                "type": "text",
                "text": "Part one. ",
                "annotations": [{"type": "url_citation", "url": "https://l.example", "start_index": 0, "end_index": 4}],
            },
            {"type": "text", "text": "Part two."},
        ],
        "usage": {"total_input_tokens": 3, "total_output_tokens": 4},
    }
    snap = parse_interaction(legacy)
    assert snap.text == "Part one. \n\nPart two."
    assert [c.url for c in snap.citations] == ["https://l.example"]
    assert "private" not in json.dumps(snap.raw)
    assert snap.usage.output_tokens == 4


def test_citation_fallbacks():
    only_results = {
        "id": "i",
        "status": "completed",
        "steps": [
            {
                "type": "url_context_result",
                "call_id": "u",
                "result": [
                    {"url": "https://ok.example", "status": "success"},
                    {"url": "https://fail.example", "status": "error"},
                ],
            },
            {"type": "model_output", "content": [{"type": "text", "text": "Done, see [paper](https://md.example/p)."}]},
        ],
    }
    assert [c.url for c in parse_interaction(only_results).citations] == ["https://ok.example"]
    only_markdown = {"id": "i", "status": "completed", "steps": [only_results["steps"][1]]}
    citations = parse_interaction(only_markdown).citations
    assert [(c.url, c.title) for c in citations] == [("https://md.example/p", "paper")]


def test_failed_and_requires_action_snapshots():
    failed = parse_interaction(
        {"id": "i", "status": "failed", "errors": [{"code": "quota", "message": "Quota exceeded"}]}
    )
    assert failed.is_terminal and failed.error == {"code": "quota", "message": "Quota exceeded"}
    no_message = parse_interaction({"id": "i", "status": "failed"})
    assert no_message.error is not None and no_message.error["code"] == "failed"
    pending = parse_interaction(
        {
            "id": "i",
            "status": "requires_action",
            "steps": [
                {"type": "function_call", "id": "f1", "name": "lookup", "arguments": {"q": "x"}},
                {"type": "function_call", "id": "f0", "name": "done", "arguments": {}},
                {"type": "function_result", "call_id": "f0", "result": "ok"},
            ],
        }
    )
    assert pending.requires_action and not pending.is_terminal
    assert [(c.id, c.name, c.arguments) for c in pending.function_calls] == [("f1", "lookup", {"q": "x"})]
    assert parse_interaction({"id": "i", "status": "budget_exceeded"}).is_terminal


def test_collaborative_plan_review_flow():
    responses = iter(
        [
            {
                "id": "plan-1",
                "status": "completed",
                "steps": [
                    {"type": "model_output", "content": [{"type": "text", "text": "Plan: 1. survey 2. compare"}]}
                ],
            },
            {
                "id": "plan-2",
                "status": "completed",
                "previous_interaction_id": "plan-1",
                "steps": [{"type": "model_output", "content": [{"type": "text", "text": "Refined plan"}]}],
            },
            {"id": "run-3", "status": "in_progress", "previous_interaction_id": "plan-2"},
        ]
    )
    client, rec = client_for(lambda r: httpx.Response(200, json=next(responses)))
    plan = client.create_deep_research("Survey solid-state electrolytes", collaborative_planning=True)
    assert plan.status == "completed" and plan.text.startswith("Plan:")
    refined = client.create_deep_research(
        "Focus on sulfides", collaborative_planning=True, previous_interaction_id=plan.id
    )
    started = client.create_deep_research("Approved.", collaborative_planning=False, previous_interaction_id=refined.id)
    assert started.status == "in_progress" and started.previous_interaction_id == "plan-2"
    bodies = rec.bodies()
    assert bodies[0]["agent_config"]["collaborative_planning"] is True and "previous_interaction_id" not in bodies[0]
    assert (
        bodies[1]["agent_config"]["collaborative_planning"] is True and bodies[1]["previous_interaction_id"] == "plan-1"
    )
    assert (
        bodies[2]["agent_config"]["collaborative_planning"] is False
        and bodies[2]["previous_interaction_id"] == "plan-2"
    )
    assert all(b["background"] is True and b["agent_config"]["type"] == "deep-research" for b in bodies)


def sse(*events: dict | str) -> bytes:
    chunks = []
    for event in events:
        payload = event if isinstance(event, str) else json.dumps(event)
        chunks.append(f"data: {payload}\n\n")
    return "".join(chunks).encode()


class BrokenStream(httpx.SyncByteStream):
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __iter__(self) -> Iterator[bytes]:
        yield self.payload
        raise httpx.ReadError("connection reset")


def test_stream_parses_events_resumes_and_hides_thoughts():
    first = sse(
        {"event_type": "interaction.start", "event_id": "e1", "interaction": {"id": "int-1", "status": "in_progress"}},
        {
            "event_type": "step.start",
            "event_id": "e2",
            "index": 0,
            "step": {"type": "thought", "summary": [{"type": "text", "text": "hidden"}]},
        },
        {
            "event_type": "step.delta",
            "event_id": "e3",
            "index": 0,
            "delta": {"type": "thought_summary", "content": {"type": "text", "text": "hidden plan"}},
        },
        {"event_type": "content.delta", "event_id": "e4", "index": 1, "delta": {"type": "text", "text": "Hello "}},
    )
    second = sse(
        {"event_type": "step.delta", "event_id": "e5", "index": 1, "delta": {"type": "text", "text": "world"}},
        {
            "event_type": "step.delta",
            "event_id": "e6",
            "index": 1,
            "delta": {
                "type": "text_annotation_delta",
                "annotations": [{"type": "url_citation", "url": "https://c.example"}],
            },
        },
        {
            "event_type": "interaction.status_update",
            "event_id": "e7",
            "interaction_id": "int-1",
            "status": "in_progress",
        },
        {"event_type": "interaction.complete", "event_id": "e8", "interaction": COMPLETED},
        "[DONE]",
    )
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=BrokenStream(first))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=second)

    client, rec = client_for(handler)
    events = list(client.stream("int-1"))
    assert rec.requests[0].url.params["stream"] == "true" and "last_event_id" not in rec.requests[0].url.params
    assert rec.requests[1].url.params["last_event_id"] == "e4"  # resumed after the disconnect
    assert rec.requests[0].headers["accept"] == "text/event-stream"
    types = [e.event_type for e in events]
    assert types == [
        "interaction.created",
        "step.start",
        "step.delta",
        "step.delta",
        "step.delta",
        "step.delta",
        "interaction.status_update",
        "interaction.completed",
    ]
    assert events[0].raw_event_type == "interaction.start" and events[0].snapshot is not None
    assert "".join(e.text_delta or "" for e in events) == "Hello world"
    assert events[5].annotations[0].url == "https://c.example"
    assert events[-1].snapshot is not None and events[-1].snapshot.text.startswith("Moisture")
    assert "hidden" not in json.dumps([e.model_dump() for e in events])
    assert client.sleeps and len(client.sleeps) == 1  # type: ignore[attr-defined]


def test_stream_resume_parameter_and_error_event():
    body = sse({"event_type": "error", "event_id": "e9", "error": {"code": "internal", "message": "boom"}})
    client, rec = client_for(lambda r: httpx.Response(200, content=body))
    events = list(client.stream("int-9", last_event_id="e8"))
    assert rec.requests[0].url.params["last_event_id"] == "e8"
    assert events[0].event_type == "error" and events[0].error == {"code": "internal", "message": "boom"}
    assert events[0].is_terminal


def test_get_cancel_delete():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            return httpx.Response(204)
        if request.url.path.endswith("/cancel"):
            return httpx.Response(200, json={"id": "int-1", "status": "cancelled"})
        return httpx.Response(200, json={"id": "int-1", "status": "in_progress"})

    client, rec = client_for(handler)
    client.get("int-1", include_input=True)
    assert rec.requests[0].url.params["include_input"] == "true"
    assert client.cancel("int-1").status == "cancelled"
    assert str(rec.requests[1].url) == f"{BASE}/interactions/int-1/cancel" and rec.requests[1].method == "POST"
    client.delete("int-1")
    assert rec.requests[2].method == "DELETE"
    client.get("a/../b")
    assert rec.requests[3].url.raw_path.decode().endswith("/interactions/a%2F..%2Fb")


def test_retry_policy_reads_vs_create():
    statuses = iter([503, 200])
    client, rec = client_for(lambda r: httpx.Response(next(statuses), json={"id": "i", "status": "completed"}))
    assert client.get("i").status == "completed" and len(rec.requests) == 2

    statuses = iter([429, 200])
    client, rec = client_for(
        lambda r: httpx.Response(
            next(statuses), json={"id": "i", "status": "in_progress"}, headers={"Retry-After": "3"}
        )
    )
    client.create_deep_research("q")
    assert len(rec.requests) == 2 and client.sleeps[0] >= 3  # type: ignore[attr-defined]

    client, rec = client_for(lambda r: httpx.Response(500, json={"error": {"message": "oops"}}))
    with pytest.raises(LLMTransientError):
        client.create_deep_research("q")
    assert len(rec.requests) == 1  # a 500 may have started paid work: never blindly re-created

    client, rec = client_for(lambda r: httpx.Response(401, json={}))
    with pytest.raises(LLMPermanentError):
        client.get("i")
    assert len(rec.requests) == 1


def test_model_form_with_mcp_server_and_allowed_tools_fallback():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        allowed = body["tools"][0]["allowed_tools"]
        if isinstance(allowed[0], dict):
            return httpx.Response(400, json={"error": {"code": 400, "message": "Unknown field allowed_tools.mode"}})
        return httpx.Response(200, json={"id": "m-1", "status": "completed", "model": "gemini-x"})

    client, rec = client_for(handler)
    tool = GeminiInteractionsClient.mcp_server_tool(
        "issues", "https://mcp.example/mcp", headers={"Authorization": "Bearer t"}, allowed_tools=["list", "get"]
    )
    assert tool["allowed_tools"] == [{"mode": "auto", "tools": ["list", "get"]}]
    snap = client.create(model="gemini-x", input="Summarize open issues", tools=[tool], store=True)
    assert snap.id == "m-1" and snap.model == "gemini-x"
    bodies = rec.bodies()
    assert bodies[0]["tools"][0]["allowed_tools"] == [{"mode": "auto", "tools": ["list", "get"]}]
    assert bodies[1]["tools"][0]["allowed_tools"] == ["list", "get"]
    with pytest.raises(LLMValidationError):
        GeminiInteractionsClient.mcp_server_tool("x", "http://insecure.example/mcp")
    with pytest.raises(LLMValidationError):
        GeminiInteractionsClient.mcp_server_tool("x", "https://user:pw@host.example/mcp")
    assert GeminiInteractionsClient.function_result_input("f1", "lookup", {"a": 1}) == {
        "type": "function_result",
        "call_id": "f1",
        "name": "lookup",
        "result": {"a": 1},
    }


def test_client_requires_api_key(monkeypatch):
    from aegis_api.config import get_settings
    from aegis_api.lab.llm.errors import LLMUnavailableError

    monkeypatch.setattr(get_settings(), "gemini_api_key", None)
    with pytest.raises(LLMUnavailableError):
        GeminiInteractionsClient()
