"""Python SDK behaviour against a mocked transport (no network)."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from aegis_ai import Aegis, AsyncAegis, PermissionDeniedError, PlanLimitError, ValidationError


def _client(handler) -> Aegis:
    client = Aegis(api_key="aeg_test_x", base_url="http://aegis.test", transport=httpx.MockTransport(handler))
    client._t.backoff = 0
    return client


def test_post_retries_reuse_one_idempotency_key():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["idempotency-key"])
        if len(seen) < 3:
            return httpx.Response(503, json={"error": {"code": "unavailable", "message": "busy"}})
        return httpx.Response(201, json={"id": "a1", "status": "queued"})

    audit = _client(handler).audits.create(system_id="s1", categories=["privacy"])
    assert audit["id"] == "a1"
    assert len(seen) == 3 and len(set(seen)) == 1


def test_error_mapping_plan_limit_and_permission():
    def plan(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403, json={"error": {"code": "plan_limit_exceeded", "message": "limit", "details": {"metric": "systems"}}}
        )

    with pytest.raises(PlanLimitError) as excinfo:
        _client(plan).systems.create(name="x")
    assert excinfo.value.details == {"metric": "systems"}

    def forbidden(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"code": "forbidden", "message": "no"}})

    with pytest.raises(PermissionDeniedError):
        _client(forbidden).systems.get("x")

    def invalid(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"error": {"code": "validation_error", "message": "bad"}})

    with pytest.raises(ValidationError):
        _client(invalid).systems.get("x")


def test_runtime_trace_checks_synchronously_and_batches_events():
    calls: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        calls.append((request.url.path, body))
        if request.url.path.endswith("/runtime/check"):
            return httpx.Response(
                200,
                json={
                    "decision": "block",
                    "effective_decision": "block",
                    "allowed": False,
                    "mode": "enforce",
                    "reason": "no",
                },
            )
        return httpx.Response(202, json={"accepted": len(body["events"]), "duplicates": 0, "decisions": []})

    client = _client(handler)
    with client.runtime.trace("sys-1", agent="bot") as trace:
        decision = trace.check("tool.call", tool="send_email", payload={"to": "a@b.example"})
        trace.event("model.response", payload={"output": "hi"})
    assert decision.decision == "block" and not decision.allowed
    paths = [p for p, _ in calls]
    assert paths == ["/api/v1/runtime/check", "/api/v1/runtime/events"]
    batch = calls[1][1]["events"]
    assert [e["event_type"] for e in batch] == ["agent.start", "model.response", "agent.stop"]
    assert len({e["trace_id"] for e in batch}) == 1 and calls[0][1]["trace_id"] == batch[0]["trace_id"]


def test_async_client_runtime_check():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["idempotency-key"]
        return httpx.Response(200, json={"decision": "allow", "effective_decision": "allow", "allowed": True})

    async def run() -> bool:
        async with AsyncAegis(
            api_key="aeg_test_x", base_url="http://aegis.test", transport=httpx.MockTransport(handler)
        ) as c:
            decision = await c.runtime_check(system_id="s", event_type="tool.call", tool="search")
            return decision.allowed

    assert asyncio.run(run()) is True
