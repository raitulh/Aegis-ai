"""LLM gateway end to end against a mocked Gemini API: routing, consent, retries, fallbacks, structured output,
function-call round trips, budget pre-check and the model_usage ledger. No network access."""

from __future__ import annotations

import json
import sys
import types
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

import httpx
import pytest
from pydantic import BaseModel
from sqlalchemy import select

from aegis_api.lab.core.errors import BudgetExceeded, ModelUnavailable
from aegis_api.lab.llm.errors import (
    LLMOutputInvalid,
    LLMPolicyError,
    LLMTransientError,
    LLMValidationError,
)
from aegis_api.lab.llm.gateway import LLMCallContext, LLMGateway, get_gateway
from aegis_api.lab.llm.providers.gemini import GeminiClient
from aegis_api.lab.llm.schemas import LLMMessage, LLMRequest, ToolSpec
from tests.conftest import requires_db
from tests.lab.conftest import make_lab
from tests.lab.fakes import FakeLLMProvider, install_fake_llm, tool_call_response

pytestmark = [requires_db, pytest.mark.db]


@pytest.fixture
def lab(client):
    # A unique organization name avoids slug races with other suites running concurrently.
    return make_lab(client, org=f"LLM Gateway {uuid.uuid4().hex[:12]}")


BASE = "https://gemini.test/v1beta"
MODELS = {"fast": "gemini-test-lite", "default": "gemini-test-flash", "reasoning": "gemini-test-pro"}


class Verdict(BaseModel):
    verdict: str
    confidence: float


class Recorder:
    def __init__(self, handler: Callable[[httpx.Request, int], httpx.Response]) -> None:
        self.handler = handler
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request, len(self.requests))

    def body(self, index: int) -> dict:
        return json.loads(self.requests[index].content)


def candidate(
    text: str = "", *, parts: list[dict] | None = None, finish: str = "STOP", usage: dict | None = None
) -> dict:
    return {
        "candidates": [{"content": {"role": "model", "parts": parts or [{"text": text}]}, "finishReason": finish}],
        "usageMetadata": usage or {"promptTokenCount": 1000, "candidatesTokenCount": 200, "thoughtsTokenCount": 50},
        "modelVersion": "gemini-test-pro-001",
        "responseId": "resp-1",
    }


@dataclass
class Harness:
    gateway: LLMGateway
    recorder: Recorder
    sleeps: list[float]


def make_gateway(
    handler: Callable[[httpx.Request, int], httpx.Response],
    *,
    fallback: FakeLLMProvider | None = None,
    fallback_tier: str = "reasoning",
) -> Harness:
    recorder = Recorder(handler)
    sleeps: list[float] = []
    gateway = LLMGateway(include_settings_providers=False, sleep=sleeps.append, rng=lambda: 0.5)
    client = GeminiClient("test-key", base_url=BASE, timeout=5.0, transport=httpx.MockTransport(recorder))
    gateway.register_provider_factory("gemini", lambda: client, models=MODELS, priority=100)
    if fallback is not None:
        gateway.register_provider_factory(
            fallback.kind,
            lambda: fallback,
            capabilities=fallback.capabilities,
            models={fallback_tier: "fake-backup"},
            priority=500,
        )
    return Harness(gateway, recorder, sleeps)


def allow_external(lab, allowed: bool = True) -> None:
    from aegis_api.lab.core.org_settings import get_org_settings

    with lab.db() as db:
        row = get_org_settings(db, lab.org_id)
        row.data_processing = {**(row.data_processing or {}), "allow_external_llm": allowed}


def usage_rows(lab) -> list:
    from aegis_api.lab.models import ModelUsage

    with lab.db() as db:
        rows = list(db.scalars(select(ModelUsage).where(ModelUsage.organization_id == lab.org_id)))
    return sorted(rows, key=lambda r: (r.success, r.retry_count, r.created_at))


def ctx(lab, **kwargs) -> LLMCallContext:
    return LLMCallContext(organization_id=lab.org_id, project_id=lab.project_id, trace_id="trace-abc", **kwargs)


def verify_request(**kwargs) -> LLMRequest:
    base = {
        "task_type": "verification",
        "system": "You are a verifier.",
        "messages": [LLMMessage(role="user", content="Assess the claim.")],
        "response_model": Verdict,
    }
    base.update(kwargs)
    return LLMRequest(**base)


@pytest.fixture
def pricing(monkeypatch):
    from aegis_api.config import get_settings

    monkeypatch.setattr(
        get_settings(),
        "model_pricing_json",
        json.dumps({"gemini:gemini-test-pro": {"input_per_mtok": 2, "output_per_mtok": 12}}),
    )


def test_structured_output_success_records_usage_and_cost(lab, pricing):
    allow_external(lab)
    h = make_gateway(lambda r, n: httpx.Response(200, json=candidate('{"verdict": "supported", "confidence": 0.8}')))
    response = h.gateway.generate(verify_request(), ctx(lab))
    assert response.parsed == {"verdict": "supported", "confidence": 0.8}
    assert response.provider == "gemini" and response.model == "gemini-test-pro"
    assert response.model_version == "gemini-test-pro-001" and response.request_id == "resp-1"
    assert response.retry_count == 0 and "verification→reasoning" in (response.route_reason or "")
    # (1000 × 2 + (200 + 50) × 12) / 1e6
    assert response.cost_usd == Decimal("0.005000") and response.cost_basis == "configured price × reported tokens"
    body = h.recorder.body(0)
    assert h.recorder.requests[0].url.path.endswith("/models/gemini-test-pro:generateContent")
    assert h.recorder.requests[0].headers["x-goog-api-key"] == "test-key"
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["generationConfig"]["responseJsonSchema"]["required"] == ["verdict", "confidence"]
    assert body["generationConfig"]["maxOutputTokens"] > 0
    assert "Assess the claim." not in json.dumps(body["systemInstruction"])
    [row] = usage_rows(lab)
    assert row.success and row.provider == "gemini" and row.model == "gemini-test-pro"
    assert row.task_type == "verification" and row.project_id == lab.project_id and row.trace_id == "trace-abc"
    assert (row.input_tokens, row.output_tokens, row.thinking_tokens) == (1000, 200, 50)
    assert row.cost_usd == Decimal("0.005000") and row.model_version == "gemini-test-pro-001"
    assert row.route_reason and "selected gemini:gemini-test-pro" in row.route_reason


def test_invalid_json_then_repair_succeeds(lab):
    allow_external(lab)
    outputs = ["not json at all", '```json\n{"verdict": "refuted", "confidence": 0.3}\n```']
    h = make_gateway(lambda r, n: httpx.Response(200, json=candidate(outputs[n - 1])))
    response = h.gateway.generate(verify_request(), ctx(lab))
    assert response.parsed == {"verdict": "refuted", "confidence": 0.3}
    assert response.metadata.get("repair_attempted") is True
    assert response.usage.input_tokens == 2000  # both calls
    repair_contents = h.recorder.body(1)["contents"]
    assert repair_contents[-2] == {"role": "model", "parts": [{"text": "not json at all"}]}
    assert "invalid json" in repair_contents[-1]["parts"][0]["text"].lower()
    rows = usage_rows(lab)
    assert len(rows) == 2 and all(r.success for r in rows)
    assert any((r.route_reason or "").startswith("repair;") for r in rows)
    assert response.cost_usd is None and rows[0].cost_basis == "no price configured"


def test_repair_failure_raises_output_invalid(lab):
    allow_external(lab)
    h = make_gateway(lambda r, n: httpx.Response(200, json=candidate('{"verdict": 1}')))
    with pytest.raises(LLMOutputInvalid) as info:
        h.gateway.generate(verify_request(), ctx(lab))
    assert info.value.error_class == "validation" and len(h.recorder.requests) == 2
    assert any("confidence" in line for line in info.value.details["errors"])


def test_json_schema_dict_structured_output(lab):
    allow_external(lab)
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}
    outputs = ['{"n": "x"}', '{"n": 3}']
    h = make_gateway(lambda r, n: httpx.Response(200, json=candidate(outputs[n - 1])))
    response = h.gateway.generate(verify_request(response_model=None, response_schema=schema), ctx(lab))
    assert response.parsed == {"n": 3}


def test_truncated_structured_output_is_invalid_without_repair(lab):
    allow_external(lab)
    h = make_gateway(lambda r, n: httpx.Response(200, json=candidate('{"verdict": "sup', finish="MAX_TOKENS")))
    with pytest.raises(LLMOutputInvalid) as info:
        h.gateway.generate(verify_request(), ctx(lab))
    assert info.value.code == "llm_output_truncated" and len(h.recorder.requests) == 1


def test_transient_503_then_success(lab):
    allow_external(lab)

    def handler(request: httpx.Request, n: int) -> httpx.Response:
        if n == 1:
            return httpx.Response(503, json={"error": {"code": 503, "message": "overloaded", "status": "UNAVAILABLE"}})
        return httpx.Response(200, json=candidate('{"verdict": "supported", "confidence": 1}'))

    h = make_gateway(handler)
    response = h.gateway.generate(verify_request(), ctx(lab))
    assert response.retry_count == 1 and response.parsed is not None
    assert len(h.sleeps) == 1 and 0 <= h.sleeps[0] <= 20
    failure, success = usage_rows(lab)
    assert not failure.success and failure.error_code == "llm_provider_unavailable" and failure.retry_count == 0
    assert failure.cost_usd == Decimal("0") and failure.input_tokens == 0
    assert success.success and success.retry_count == 1


def test_retry_exhaustion_records_every_failed_call(lab, monkeypatch):
    from aegis_api.config import get_settings

    allow_external(lab)
    monkeypatch.setattr(get_settings(), "gemini_max_retries", 2)
    h = make_gateway(lambda r, n: httpx.Response(500, json={"error": {"code": 500, "message": "internal"}}))
    with pytest.raises(LLMTransientError):
        h.gateway.generate(verify_request(), ctx(lab))
    # 3 attempts on each candidate (reasoning → default → fast tier), backoff between retries only
    paths = [r.url.path.rsplit("/", 1)[-1] for r in h.recorder.requests]
    assert (
        paths
        == ["gemini-test-pro:generateContent"] * 3
        + ["gemini-test-flash:generateContent"] * 3
        + ["gemini-test-lite:generateContent"] * 3
    )
    assert len(h.sleeps) == 6
    rows = usage_rows(lab)
    assert sorted(r.retry_count for r in rows) == [0, 0, 0, 1, 1, 1, 2, 2, 2]
    assert all(not r.success and r.error_code == "llm_provider_unavailable" for r in rows)
    assert {r.model for r in rows} == set(MODELS.values())


def test_rate_limit_honours_retry_after(lab):
    allow_external(lab)

    def handler(request: httpx.Request, n: int) -> httpx.Response:
        if n == 1:
            return httpx.Response(429, json={"error": {"code": 429}}, headers={"Retry-After": "12"})
        return httpx.Response(200, json=candidate('{"verdict": "supported", "confidence": 1}'))

    h = make_gateway(handler)
    h.gateway.generate(verify_request(), ctx(lab))
    assert h.sleeps == [12.0]


def test_400_is_not_retried_or_fallen_back(lab):
    allow_external(lab)
    backup = FakeLLMProvider(['{"verdict": "x", "confidence": 0}'])
    h = make_gateway(
        lambda r, n: httpx.Response(
            400, json={"error": {"code": 400, "message": "bad schema", "status": "INVALID_ARGUMENT"}}
        ),
        fallback=backup,
    )
    with pytest.raises(LLMValidationError):
        h.gateway.generate(verify_request(), ctx(lab))
    assert len(h.recorder.requests) == 1 and h.sleeps == [] and backup.calls == 0
    [row] = usage_rows(lab)
    assert row.error_code == "llm_invalid_request"


def test_policy_block_is_not_retried(lab):
    allow_external(lab)
    h = make_gateway(lambda r, n: httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}))
    with pytest.raises(LLMPolicyError):
        h.gateway.generate(verify_request(), ctx(lab))
    assert len(h.recorder.requests) == 1
    assert usage_rows(lab)[0].error_code == "llm_prompt_blocked"


def test_falls_back_to_next_candidate_on_auth_failure(lab):
    allow_external(lab)
    backup = FakeLLMProvider(['{"verdict": "supported", "confidence": 0.5}'])
    h = make_gateway(
        lambda r, n: httpx.Response(401, json={"error": {"code": 401}}), fallback=backup, fallback_tier="fast"
    )
    response = h.gateway.generate(verify_request(), ctx(lab))
    assert response.provider == "fake" and response.model == "fake-backup"
    assert response.route_reason and response.route_reason.startswith("fallback #3")
    assert "llm_auth_failed" in response.route_reason
    # bad credentials fail every Gemini model alike: its other candidates are skipped, not retried
    assert len(h.recorder.requests) == 1 and h.sleeps == []
    rows = usage_rows(lab)
    assert [r.success for r in rows] == [False, True]
    assert response.cost_usd == Decimal("0")  # local provider


def test_function_call_round_trip_echoes_thought_signature(lab):
    allow_external(lab)
    first = candidate(
        parts=[
            {"text": "thinking out loud", "thought": True},
            {
                "functionCall": {"id": "fc_1", "name": "lookup_paper", "args": {"doi": "10.1/x"}},
                "thoughtSignature": "U0lHTkFUVVJF",
            },
        ]
    )
    second = candidate("The paper reports 92.3% accuracy.")
    h = make_gateway(lambda r, n: httpx.Response(200, json=first if n == 1 else second))
    tools = [
        ToolSpec(
            name="lookup_paper",
            description="Fetch paper metadata",
            parameters={"type": "object", "properties": {"doi": {"type": "string"}}},
        )
    ]
    request = LLMRequest(
        task_type="research",
        messages=[LLMMessage(role="user", content="What accuracy does 10.1/x report?")],
        tools=tools,
    )
    response = h.gateway.generate(request, ctx(lab))
    assert response.finish_reason == "tool_calls" and response.text == ""
    [call] = response.tool_calls
    assert (call.id, call.name, call.arguments) == ("fc_1", "lookup_paper", {"doi": "10.1/x"})
    assert response.raw_assistant_turn is not None and "thinking out loud" not in json.dumps(
        response.raw_assistant_turn
    )

    follow_up = request.model_copy(
        update={
            "messages": [
                *request.messages,
                LLMMessage(role="assistant", content=response.text),
                LLMMessage(role="tool", name=call.name, tool_call_id=call.id, content=json.dumps({"accuracy": 0.923})),
            ],
            "previous_turns": [response.raw_assistant_turn],
        }
    )
    final = h.gateway.generate(follow_up, ctx(lab))
    assert final.text == "The paper reports 92.3% accuracy."
    body = h.recorder.body(1)
    model_turn = body["contents"][1]
    assert model_turn["role"] == "model"
    assert model_turn["parts"][-1]["thoughtSignature"] == "U0lHTkFUVVJF"
    assert body["contents"][2] == {
        "role": "user",
        "parts": [
            {"functionResponse": {"id": "fc_1", "name": "lookup_paper", "response": {"result": {"accuracy": 0.923}}}}
        ],
    }
    assert body["tools"][0]["functionDeclarations"][0]["name"] == "lookup_paper"


def test_consent_required_for_external_providers(lab, monkeypatch):
    from aegis_api.config import get_settings

    settings = get_settings()
    for name, value in (
        ("gemini_api_key", "k"),
        ("openai_api_key", None),
        ("anthropic_api_key", None),
        ("ollama_base_url", None),
    ):
        monkeypatch.setattr(settings, name, value)
    allow_external(lab, False)
    gateway = LLMGateway()
    assert gateway.available_providers(lab.org_id) == []
    with pytest.raises(ModelUnavailable) as info:
        gateway.generate(verify_request(), ctx(lab))
    assert "consent to external data processing" in info.value.message
    assert info.value.status_code == 503
    assert usage_rows(lab) == []
    allow_external(lab, True)
    assert gateway.available_providers(lab.org_id) == ["gemini"]


def test_local_provider_needs_no_consent(lab, monkeypatch):
    allow_external(lab, False)
    fake = install_fake_llm(monkeypatch, ['{"verdict": "supported", "confidence": 0.9}'])
    response = get_gateway().generate(verify_request(), ctx(lab))
    assert response.provider == "fake" and response.parsed == {"verdict": "supported", "confidence": 0.9}
    assert fake.calls == 1 and fake.models == ["fake-model"]
    external = install_fake_llm(monkeypatch, ["x"], external=True)
    with pytest.raises(ModelUnavailable):
        get_gateway().generate(
            LLMRequest(task_type="summarization", messages=[LLMMessage(role="user", content="hi")]), ctx(lab)
        )
    assert external.calls == 0


def test_install_fake_llm_helper(lab, monkeypatch):
    fake = install_fake_llm(
        monkeypatch,
        [
            "plain text",
            {"verdict": "supported", "confidence": 1.0},
            lambda req: f"echo {req.task_type}",
            tool_call_response("search", {"q": "x"}),
            LLMTransientError("flaky"),
            "after retry",
        ],
    )
    gateway = get_gateway()
    assert fake.gateway is gateway
    simple = LLMRequest(task_type="summarization", messages=[LLMMessage(role="user", content="summarize")])
    assert gateway.generate(simple, ctx(lab)).text == "plain text"
    assert gateway.generate(verify_request(), ctx(lab)).parsed == {"verdict": "supported", "confidence": 1.0}
    assert gateway.generate(simple, ctx(lab)).text == "echo summarization"
    assert gateway.generate(simple, ctx(lab)).tool_calls[0].name == "search"
    retried = gateway.generate(simple, ctx(lab))
    assert retried.text == "after retry" and retried.retry_count == 1
    with pytest.raises(AssertionError, match="no scripted response"):
        gateway.generate(simple, ctx(lab))
    rows = usage_rows(lab)
    assert {r.provider for r in rows} == {"fake"} and sum(not r.success for r in rows) == 2


def test_streaming_records_usage(lab, monkeypatch):
    install_fake_llm(monkeypatch, ["A streamed answer that is longer than sixteen characters."])
    request = LLMRequest(task_type="summarization", messages=[LLMMessage(role="user", content="go")])
    chunks = list(get_gateway().stream(request, ctx(lab)))
    assert "".join(c.text_delta for c in chunks) == "A streamed answer that is longer than sixteen characters."
    assert chunks[-1].done and chunks[-1].usage is not None and chunks[-1].cost_usd == Decimal("0")
    [row] = usage_rows(lab)
    assert row.success and row.output_tokens > 0
    with pytest.raises(LLMValidationError):
        get_gateway().stream(verify_request(), ctx(lab))


def test_budget_precheck_blocks_mission_calls(lab, monkeypatch):
    from aegis_api.lab.models import Mission

    with lab.db() as db:
        mission = Mission(
            organization_id=lab.org_id,
            workspace_id=lab.workspace_id,
            project_id=lab.project_id,
            title="Budgeted",
            objective="o",
        )
        db.add(mission)
        db.flush()
        mission_id = mission.id

    seen: list[tuple] = []

    @dataclass
    class Check:
        ok: bool
        reason: str | None
        remaining_usd: Decimal

    def check_budget(db, mission, kind, *, estimated_usd=0, count=0):
        seen.append((mission.id, kind, estimated_usd))
        return Check(ok=False, reason="LLM budget exhausted", remaining_usd=Decimal("0"))

    module = types.ModuleType("aegis_api.lab.governance.budgets")
    module.check_budget = check_budget  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "aegis_api.lab.governance.budgets", module)
    fake = install_fake_llm(monkeypatch, [])
    with pytest.raises(BudgetExceeded, match="LLM budget exhausted"):
        get_gateway().generate(verify_request(), ctx(lab, mission_id=mission_id))
    assert fake.calls == 0 and seen[0][0] == mission_id and seen[0][1] == "llm"
    assert isinstance(seen[0][2], Decimal)

    module.check_budget = lambda db, mission, kind, **kw: Check(ok=True, reason=None, remaining_usd=Decimal("5"))  # type: ignore[attr-defined]
    fake.add('{"verdict": "supported", "confidence": 0.7}')
    response = get_gateway().generate(verify_request(), ctx(lab, mission_id=mission_id))
    assert response.parsed is not None
    [row] = [r for r in usage_rows(lab) if r.success]
    assert row.mission_id == mission_id


def test_model_unavailable_when_nothing_configured(lab):
    gateway = LLMGateway(include_settings_providers=False)
    with pytest.raises(ModelUnavailable, match="No model provider is configured"):
        gateway.generate(verify_request(), ctx(lab))


def test_org_model_config_overrides_routing(lab, monkeypatch):
    from aegis_api.lab.models import ModelConfig

    allow_external(lab)
    with lab.db() as db:
        db.add(
            ModelConfig(
                organization_id=lab.org_id,
                provider_kind="gemini",
                model="gemini-org-special",
                tier="reasoning",
                priority=1,
                max_output_tokens=256,
                input_per_mtok_usd=Decimal("1"),
                output_per_mtok_usd=Decimal("1"),
            )
        )
    h = make_gateway(lambda r, n: httpx.Response(200, json=candidate('{"verdict": "supported", "confidence": 1}')))
    response = h.gateway.generate(verify_request(), ctx(lab))
    assert response.model == "gemini-org-special"
    assert h.recorder.body(0)["generationConfig"]["maxOutputTokens"] == 256
    assert response.cost_usd == Decimal("0.001250")  # (1000 + 200 + 50) × $1 / 1e6


# --- other provider adapters (single attempt, typed errors, JSON mode) ------------------------------
def test_openai_adapter_json_mode_and_single_attempt():
    from aegis_api.lab.llm.providers.openai import OpenAIChatProvider

    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, json={"error": {"message": "slow down", "type": "rate_limit"}})
        return httpx.Response(
            200,
            json={
                "model": "gpt-test-2026",
                "choices": [{"message": {"content": '{"verdict": "ok", "confidence": 1}'}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 7},
            },
        )

    provider = OpenAIChatProvider("sk-openai", timeout=5, default_max_tokens=99, transport=httpx.MockTransport(handler))
    with pytest.raises(LLMTransientError):
        provider.generate(verify_request(), model="gpt-test")
    assert len(calls) == 1  # the engine's internal retry loop is bypassed; the gateway owns retries
    response = provider.generate(verify_request(), model="gpt-test")
    body = json.loads(calls[1].content)
    assert body["response_format"] == {"type": "json_object"} and body["max_completion_tokens"] == 99
    assert body["messages"][0]["role"] == "system" and '"confidence"' in body["messages"][0]["content"]
    assert calls[1].headers["authorization"] == "Bearer sk-openai"
    assert response.text == '{"verdict": "ok", "confidence": 1}' and response.usage.input_tokens == 12
    assert response.model_version == "gpt-test-2026" and response.finish_reason == "stop"


def test_anthropic_adapter_maps_refusal_to_policy_error():
    from aegis_api.lab.llm.providers.anthropic import AnthropicChatProvider

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "sk-ant"
        return httpx.Response(
            200,
            json={
                "model": "claude-test",
                "content": [{"type": "text", "text": ""}],
                "stop_reason": "refusal",
                "usage": {},
            },
        )

    provider = AnthropicChatProvider("sk-ant", timeout=5, default_max_tokens=64, transport=httpx.MockTransport(handler))
    with pytest.raises(LLMPolicyError):
        provider.generate(LLMRequest(task_type="coding", messages=[LLMMessage(role="user", content="x")]), model="c")


def test_local_ollama_provider_through_gateway_without_consent(lab):
    from aegis_api.lab.llm.providers.local import OllamaChatProvider

    allow_external(lab, False)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.path == "/api/chat" and body["format"] == "json" and body["think"] is False
        return httpx.Response(
            200,
            json={
                "model": "qwen-test",
                "message": {"content": '<think>private</think>{"verdict": "ok", "confidence": 0.5}'},
                "done_reason": "stop",
                "prompt_eval_count": 30,
                "eval_count": 9,
            },
        )

    local = OllamaChatProvider(
        "http://ollama.test:11434", timeout=5, default_max_tokens=128, transport=httpx.MockTransport(handler)
    )
    gateway = LLMGateway(include_settings_providers=False, sleep=lambda _s: None)
    gateway.register_provider_factory("ollama", lambda: local, models={"fast": "qwen-test"})
    response = gateway.generate(verify_request(), ctx(lab))
    assert response.provider == "ollama" and response.parsed == {"verdict": "ok", "confidence": 0.5}
    assert "private" not in response.text
    assert response.cost_usd == Decimal("0") and response.cost_estimated is False
    [row] = usage_rows(lab)
    assert row.provider == "ollama" and row.cost_basis == "local model — no provider charge"


def test_builtin_kind_registered_without_models_uses_settings_mapping(monkeypatch):
    from aegis_api.config import get_settings

    monkeypatch.setattr(get_settings(), "gemini_reasoning_model", "configured-pro")
    gateway = LLMGateway(include_settings_providers=False)
    gateway.register_provider_factory("gemini", lambda: None)
    [profile] = gateway.profiles()
    assert profile.models["reasoning"] == "configured-pro"
