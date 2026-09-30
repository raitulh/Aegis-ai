"""ModelGateway against real PostgreSQL with fake providers: external-data consent, budget enforcement through
the policy engine, structured-output validation with one repair round, failover and usage recording."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from aegis_api.infrastructure.llm.base import LLMError, LLMErrorKind
from aegis_api.infrastructure.llm.router import ProviderRegistry, set_registry_override
from aegis_api.infrastructure.llm.schemas import LLMRequest, LLMResponse
from engines.lab.enums import ModelTier
from engines.lab.routing import Feature, ModelCandidate
from tests.support.lab_fakes import ScriptedProvider

pytestmark = pytest.mark.db

SCHEMA = {"type": "object", "properties": {"answer": {"type": "integer"}}, "required": ["answer"]}
FEATURES = frozenset({Feature.STRUCTURED_OUTPUT, Feature.TOOLS})


class FailingProvider(ScriptedProvider):
    name = "failing"

    def generate(self, request: LLMRequest, *, model: str) -> LLMResponse:
        self.requests.append(request)
        raise LLMError("upstream overloaded", kind=LLMErrorKind.TRANSIENT, provider=self.name)


class ExternalProvider(ScriptedProvider):
    name = "external"
    data_leaves_organization = True


def _candidates(name: str, *, price: float, latency: int, external: bool = False) -> list[ModelCandidate]:
    return [
        ModelCandidate(name, f"{name}-{tier.value}", tier, FEATURES, price, price * 4, latency, external)
        for tier in (ModelTier.FAST, ModelTier.DEFAULT, ModelTier.REASONING)
    ]


def _use(providers: dict[str, Any], candidates: list[ModelCandidate]):
    from aegis_api.services.lab import model_gateway

    set_registry_override(lambda _overrides=(): ProviderRegistry(providers=providers, candidates=candidates))
    model_gateway._GATEWAY = None
    return model_gateway.get_gateway()


def _ctx(workspace, **kw):
    from aegis_api.services.lab.model_gateway import CallContext

    return CallContext(organization_id=uuid.UUID(workspace.org_id), **kw)


def _usage_rows(org: str) -> list[Any]:
    from sqlalchemy import select

    from aegis_api.db.session import session_scope
    from aegis_api.models.lab import ModelUsage

    with session_scope(uuid.UUID(org)) as db:
        rows = db.scalars(select(ModelUsage).order_by(ModelUsage.created_at)).all()
        for r in rows:
            db.expunge(r)
        return list(rows)


def test_external_providers_require_consent(workspace, lab):
    from sqlalchemy import update

    from aegis_api.db.session import admin_session_scope
    from aegis_api.errors import ServiceUnavailable
    from aegis_api.models import OrganizationQuota
    from aegis_api.services import quota_service

    gateway = _use(
        {"external": ExternalProvider({"planning": {"answer": 1}})},
        _candidates("external", price=0.1, latency=10, external=True),
    )
    request = LLMRequest(task_type="planning", input="q", response_schema=SCHEMA, metadata={"role": "planning"})
    with pytest.raises(ServiceUnavailable) as info:
        gateway.generate(_ctx(workspace), request)
    assert "allow_external_models" in info.value.message

    with admin_session_scope() as db:
        quota_service.get_quota(db, uuid.UUID(workspace.org_id))
        db.execute(
            update(OrganizationQuota)
            .where(OrganizationQuota.organization_id == uuid.UUID(workspace.org_id))
            .values(allow_external_models=True)
        )
    result = gateway.generate(_ctx(workspace), request)
    assert result.parsed == {"answer": 1} and result.candidate.provider == "external"


def test_budget_exhaustion_blocks_model_calls(workspace, lab):
    from aegis_api.errors import BudgetExceeded

    project = workspace.post("/api/v1/projects", json={"name": "Budgeted"}).json()
    mission = workspace.post(
        "/api/v1/missions",
        json={
            "project_id": project["id"],
            "title": "Tiny budget",
            "objective": "Needs more tokens than allowed.",
            "budget": {"max_llm_tokens": 5},
        },
    ).json()
    provider = ScriptedProvider({"planning": {"answer": 1}})
    gateway = _use({"scripted": provider}, _candidates("scripted", price=0.1, latency=10))
    request = LLMRequest(task_type="planning", input="q", response_schema=SCHEMA, metadata={"role": "planning"})
    with pytest.raises(BudgetExceeded):
        gateway.generate(
            _ctx(workspace, mission_id=uuid.UUID(mission["id"]), project_id=uuid.UUID(project["id"])), request
        )
    assert provider.requests == []  # blocked before any tokens were spent
    ok = gateway.generate(_ctx(workspace, mission_id=uuid.UUID(mission["id"]), enforce_budget=False), request)
    assert ok.parsed == {"answer": 1}


def test_structured_output_repair_and_rejection(workspace, lab):
    from aegis_api.services.lab.model_gateway import StructuredOutputError

    answers = iter([{"wrong": "shape"}, {"answer": 42}])
    provider = ScriptedProvider({"planning": lambda _r: next(answers)})
    gateway = _use({"scripted": provider}, _candidates("scripted", price=0.1, latency=10))
    request = LLMRequest(task_type="planning", input="q", response_schema=SCHEMA, metadata={"role": "planning"})
    result = gateway.generate(_ctx(workspace), request)
    assert result.parsed == {"answer": 42} and len(provider.requests) == 2
    assert "did not match the required JSON schema" in (provider.requests[1].history[-1].text or "")

    stubborn = ScriptedProvider({"planning": {"still": "wrong"}})
    gateway = _use({"scripted": stubborn}, _candidates("scripted", price=0.1, latency=10))
    with pytest.raises(StructuredOutputError):
        gateway.generate(_ctx(workspace), request)
    assert len(stubborn.requests) == 2  # exactly one repair round, never silently "fixed"
    assert any(u.error_code == "output_invalid" for u in _usage_rows(workspace.org_id))


def test_transient_failures_fail_over_to_the_next_candidate(workspace, lab):
    failing = FailingProvider()
    healthy = ScriptedProvider({"planning": {"answer": 7}})
    gateway = _use(
        {"failing": failing, "scripted": healthy},
        _candidates("failing", price=0.01, latency=5) + _candidates("scripted", price=0.5, latency=50),
    )
    request = LLMRequest(task_type="planning", input="q", response_schema=SCHEMA, metadata={"role": "planning"})
    result = gateway.generate(_ctx(workspace), request)
    assert result.candidate.provider == "scripted" and result.parsed == {"answer": 7}
    assert result.attempts[0]["outcome"] == "transient" and result.attempts[-1]["outcome"] == "ok"
    assert len(failing.requests) == 3  # initial call + 2 bounded retries before failover
    rows = _usage_rows(workspace.org_id)
    assert {r.provider for r in rows} >= {"failing", "scripted"}
    assert all(r.input_tokens >= 0 for r in rows)
