"""Infrastructure adapters with transport-level fakes (no network): Gemini Interactions API, MCP client, S3
object storage (moto), webhook signing, activity error classification and harness/platform parity."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from aegis_api.infrastructure.llm.base import LLMError, LLMErrorKind
from aegis_api.infrastructure.llm.providers.gemini.provider import GeminiProvider
from aegis_api.infrastructure.llm.schemas import LLMRequest, ToolDefinition
from aegis_api.infrastructure.mcp.client import MCPClient, MCPError

ROOT = Path(__file__).resolve().parents[3]


def _gemini(handler) -> GeminiProvider:
    return GeminiProvider(
        api_key="test-key",
        base_url="https://generativelanguage.example/v1beta",
        api_revision="2026-05-20",
        transport=httpx.MockTransport(handler),
    )


def test_gemini_generate_builds_request_and_parses_steps() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "int-1",
                "status": "completed",
                "model": "gemini-x",
                "steps": [
                    {"type": "thought", "content": [{"type": "text", "text": "private reasoning"}]},
                    {"type": "function_call", "id": "c1", "name": "paper_search", "arguments": {"query": "annealing"}},
                    {"type": "function_result", "call_id": "c1", "result": "..."},
                    {
                        "type": "model_output",
                        "content": [
                            {
                                "type": "text",
                                "text": '{"answer": 1}',
                                "annotations": [{"url": "https://arxiv.org/abs/1", "title": "Paper", "start_index": 0}],
                            }
                        ],
                    },
                ],
                "usage": {"total_input_tokens": 120, "total_output_tokens": 30, "total_thought_tokens": 7},
            },
        )

    provider = _gemini(handler)
    request = LLMRequest(
        task_type="research",
        system="SYSTEM POLICY",
        input="question",
        response_schema={"type": "object", "properties": {"answer": {"type": "integer"}}, "required": ["answer"]},
        tools=[ToolDefinition(name="paper_search", parameters={"type": "object", "properties": {}})],
        seed=7,
        thinking_level="low",
    )
    response = provider.generate(request, model="gemini-x")
    body = seen["body"]
    assert seen["url"].endswith("/v1beta/interactions")
    assert seen["headers"]["x-goog-api-key"] == "test-key" and seen["headers"]["api-revision"] == "2026-05-20"
    assert body["store"] is False and body["system_instruction"] == "SYSTEM POLICY"
    assert body["response_format"]["mime_type"] == "application/json"
    assert body["generation_config"]["seed"] == 7 and body["tools"][0]["type"] == "function"
    assert response.parsed == {"answer": 1} and "private reasoning" not in response.text
    assert [c.name for c in response.tool_calls] == ["paper_search"]
    assert response.citations[0].url == "https://arxiv.org/abs/1"
    assert response.usage.input_tokens == 120 and response.usage.thought_tokens == 7
    assert response.interaction_id == "int-1"


@pytest.mark.parametrize(
    ("status", "headers", "kind", "retryable"),
    [
        (429, {"retry-after": "3"}, LLMErrorKind.RATE_LIMITED, True),
        (503, {}, LLMErrorKind.TRANSIENT, True),
        (401, {}, LLMErrorKind.AUTH, False),
        (400, {}, LLMErrorKind.INVALID_REQUEST, False),
    ],
)
def test_gemini_error_classification(status, headers, kind, retryable) -> None:
    provider = _gemini(lambda r: httpx.Response(status, headers=headers, json={"error": {"message": "nope"}}))
    with pytest.raises(LLMError) as info:
        provider.generate(LLMRequest(task_type="t", input="x"), model="m")
    assert info.value.kind == kind and info.value.retryable is retryable
    if status == 429:
        assert info.value.retry_after == 3.0


def test_gemini_safety_block_is_a_policy_error_and_ids_are_validated() -> None:
    provider = _gemini(
        lambda r: httpx.Response(
            200,
            json={
                "status": "failed",
                "errors": [{"message": "Response blocked by safety filters"}],
            },
        )
    )
    with pytest.raises(LLMError) as info:
        provider.generate(LLMRequest(task_type="t", input="x"), model="m")
    assert info.value.kind == LLMErrorKind.POLICY
    with pytest.raises(LLMError):
        provider.get_interaction("../../admin")


def test_gemini_deep_research_background_and_resumable_stream() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == "POST" and request.url.path.endswith("/interactions"):
            return httpx.Response(200, json={"id": "dr-1", "status": "in_progress", "agent": "deep-research"})
        if request.url.params.get("stream") == "true":
            sse = (
                'id: e2\ndata: {"event_id": "e2", "event_type": "step.delta", "delta": {"text": "a"}}\n\n'
                "data: not-json\n\n"
                'id: e3\ndata: {"event_type": "interaction.complete"}\n\n'
            )
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse.encode())
        return httpx.Response(
            200,
            json={
                "id": "dr-1",
                "status": "completed",
                "steps": [{"type": "model_output", "content": [{"type": "text", "text": "report"}]}],
            },
        )

    provider = _gemini(handler)
    snap = provider.start_agent(
        agent="deep-research", input_text="question", agent_config={"thinking_summaries": "auto"}
    )
    body = json.loads(calls[0].content)
    assert snap.id == "dr-1" and snap.status == "in_progress"
    assert body["background"] is True and body["store"] is True and body["agent"] == "deep-research"
    events = list(provider.stream_interaction("dr-1", last_event_id="e1"))
    assert calls[-1].url.params["last_event_id"] == "e1"
    assert [e.event_id for e in events] == ["e2", "e3"] and events[-1].event_type == "interaction.complete"
    done = provider.get_interaction("dr-1")
    assert done.status == "completed" and done.text == "report"


def _mcp_handler(log: list[dict[str, Any]], *, sse: bool = False):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":  # session termination on close
            log.append({"payload": {"method": "DELETE"}, "headers": dict(request.headers)})
            return httpx.Response(200)
        payload = json.loads(request.read())
        log.append({"payload": payload, "headers": dict(request.headers)})
        method = payload.get("method")
        if "id" not in payload:
            return httpx.Response(202)
        if method == "initialize":
            result = {"protocolVersion": "2025-06-18", "serverInfo": {"name": "lit", "version": "1"}}
            return httpx.Response(
                200,
                headers={"mcp-session-id": "sess-1"},
                json={"jsonrpc": "2.0", "id": payload["id"], "result": result},
            )
        if method == "tools/list":
            result = {
                "tools": [
                    {"name": "search", "description": "Search papers", "inputSchema": {"type": "object"}},
                    {"description": "nameless tool is ignored"},
                ]
            }
        elif method == "tools/call":
            if payload["params"]["name"] == "explode":
                return httpx.Response(
                    200, json={"jsonrpc": "2.0", "id": payload["id"], "error": {"code": -32000, "message": "boom"}}
                )
            result = {"content": [{"type": "text", "text": "3 results"}, {"type": "image", "data": "..."}]}
        else:
            result = {}
        message = {"jsonrpc": "2.0", "id": payload["id"], "result": result}
        if sse:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=f"event: message\ndata: {json.dumps(message)}\n\n".encode(),
            )
        return httpx.Response(200, json=message)

    return handler


@pytest.mark.parametrize("sse", [False, True])
def test_mcp_client_session_tools_and_errors(sse: bool) -> None:
    log: list[dict[str, Any]] = []
    client = MCPClient(
        "https://mcp.example.com/mcp",
        headers={"Authorization": "Bearer t"},
        transport=httpx.MockTransport(_mcp_handler(log, sse=sse)),
        url_validator=lambda u: u,
    )
    with client:  # initializes the session
        assert client.server_info["name"] == "lit" and client.session_id == "sess-1"
        tools = client.list_tools()
        assert [t.name for t in tools] == ["search"]
        result = client.call_tool("search", {"q": "annealing"})
        assert result.text.startswith("3 results") and "[image content omitted]" in result.text
        with pytest.raises(MCPError, match="boom"):
            client.call_tool("explode", {})
    assert log[-1]["payload"]["method"] == "DELETE"  # the session is closed explicitly
    later = [entry["headers"] for entry in log[1:]]
    assert all(h.get("mcp-session-id") == "sess-1" and h.get("mcp-protocol-version") == "2025-06-18" for h in later)


def test_mcp_client_revalidates_url_and_rejects_redirects_and_auth_failures() -> None:
    checks: list[str] = []

    def validator(url: str) -> str:
        checks.append(url)
        return url

    client = MCPClient(
        "https://mcp.example.com/mcp",
        transport=httpx.MockTransport(lambda r: httpx.Response(302, headers={"location": "http://169.254.169.254/"})),
        url_validator=validator,
    )
    with pytest.raises(MCPError):
        client.initialize()
    assert len(checks) >= 2  # validated at construction and again at call time (DNS rebinding defence)
    denied = MCPClient(
        "https://mcp.example.com/mcp",
        transport=httpx.MockTransport(lambda r: httpx.Response(401)),
        url_validator=lambda u: u,
    )
    with pytest.raises(MCPError, match="credentials"):
        denied.initialize()
    from aegis_api.errors import ValidationFailed

    with pytest.raises(ValidationFailed):
        MCPClient("http://127.0.0.1:8080/mcp")


def test_s3_storage_roundtrip_limits_and_presign() -> None:
    boto3 = pytest.importorskip("boto3")
    moto = pytest.importorskip("moto")
    from aegis_api.infrastructure.storage import ObjectTooLarge, S3ObjectStorage

    with moto.mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        storage = S3ObjectStorage(
            bucket="lab-test",
            endpoint=None,
            public_endpoint=None,
            region="us-east-1",
            access_key=None,
            secret_key=None,
            client=client,
        )
        storage.ensure_bucket()
        stored = storage.put_stream(
            "org/a/b.bin", io.BytesIO(b"x" * 1000), content_type="application/octet-stream", max_bytes=4096
        )
        assert stored.size == 1000 and len(stored.sha256) == 64
        assert storage.get_bytes("org/a/b.bin") == b"x" * 1000
        stat = storage.stat("org/a/b.bin")
        assert stat is not None and stat.size == 1000
        with pytest.raises(ObjectTooLarge):
            storage.put_stream("org/a/big.bin", io.BytesIO(b"y" * 5000), content_type="x", max_bytes=4096)
        assert storage.stat("org/a/big.bin") is None  # nothing is written when the limit is exceeded
        url = storage.presign_get("org/a/b.bin", ttl_seconds=60, filename="b.bin")
        assert url and url.startswith("https://") and "Signature" in url and "b.bin" in url
        storage.delete("org/a/b.bin")
        assert storage.stat("org/a/b.bin") is None


def test_webhook_signature_roundtrip_tamper_and_replay_window() -> None:
    from aegis_api.security.webhook_signing import sign_payload, verify_signature

    body = b'{"event":"discovery.approved"}'
    header = sign_payload("whsec_test", body, timestamp=1_000_000)
    assert verify_signature("whsec_test", body, header, now=1_000_100)
    assert not verify_signature("whsec_test", body + b" ", header, now=1_000_100)
    assert not verify_signature("whsec_other", body, header, now=1_000_100)
    assert not verify_signature("whsec_test", body, header, now=1_000_000 + 301)  # replay outside tolerance
    assert not verify_signature("whsec_test", body, "garbage")


def test_activity_error_classification() -> None:
    from sqlalchemy.exc import IntegrityError, OperationalError

    from aegis_api.errors import Forbidden, ServiceUnavailable
    from aegis_api.infrastructure.storage import StorageError
    from aegis_api.workflows.runtime import classify

    assert classify(LLMError("x", kind=LLMErrorKind.RATE_LIMITED, provider="p")).retryable
    assert not classify(LLMError("x", kind=LLMErrorKind.POLICY, provider="p")).retryable
    assert not classify(Forbidden("no")).retryable
    assert classify(ServiceUnavailable("later")).retryable
    assert classify(StorageError("s3", transient=True)).retryable
    assert classify(OperationalError("select", {}, Exception("conn reset"))).retryable
    assert not classify(IntegrityError("insert", {}, Exception("duplicate"))).retryable
    assert not classify(KeyError("missing")).retryable


def test_redaction_of_secrets_in_persisted_structures() -> None:
    from aegis_api.logging import redact_data

    data = {
        "api_key": "sk-live-123",
        "nested": {"Authorization": "Bearer abc", "query": "annealing"},
        "list": [{"password": "p"}],
    }
    out = json.dumps(redact_data(data))
    assert "sk-live-123" not in out and "Bearer abc" not in out and '"p"' not in out
    assert "annealing" in out


def test_objective_harness_matches_platform_benchmark_functions(monkeypatch) -> None:
    from engines.lab.benchmarks import functions

    harness_dir = ROOT / "engines" / "lab" / "harnesses"
    monkeypatch.syspath_prepend(str(harness_dir))
    spec = importlib.util.spec_from_file_location("objective_harness_copy", harness_dir / "objective.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.modules.pop("_common", None)
    points = [[0.0] * 5, [1.0, -2.0, 0.5, 3.3, -4.1], [5.12] * 5, [0.01 * i for i in range(10)]]
    assert set(module.FUNCTIONS) == set(functions.FUNCTIONS)
    for name, fn in functions.FUNCTIONS.items():
        for x in points:
            assert module.FUNCTIONS[name](x) == pytest.approx(fn(x), rel=1e-12, abs=1e-12), name


def test_mission_tool_names_match_the_broker_and_roles() -> None:
    from aegis_api.services.lab.missions import KNOWN_TOOLS
    from aegis_api.services.lab.tools import BUILTIN_TOOLS
    from engines.lab.agents.roles import ROLE_SPECS

    assert set(BUILTIN_TOOLS) == KNOWN_TOOLS  # no advertised tool without an implementation
    for spec in ROLE_SPECS.values():
        assert set(spec.default_tools) <= KNOWN_TOOLS, spec.role
