"""Models API: catalog (no secrets), model configuration CRUD + audit, permissions, tenancy, route preview,
model usage listing and summaries."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from tests.conftest import requires_db
from tests.lab.conftest import make_lab

pytestmark = [requires_db, pytest.mark.db]


@pytest.fixture
def lab(client):
    return make_lab(client, org=f"Models Lab {uuid.uuid4().hex[:12]}")


@pytest.fixture
def other_lab(client):
    return make_lab(client, org=f"Models Other {uuid.uuid4().hex[:12]}")


@pytest.fixture
def providers(monkeypatch):
    """Gemini + Ollama configured via settings (no network is used by these endpoints)."""
    from aegis_api.config import get_settings

    settings = get_settings()
    for name, value in (
        ("gemini_api_key", "sk-super-secret-gemini-key"),
        ("openai_api_key", None),
        ("anthropic_api_key", None),
        ("ollama_base_url", "http://ollama.internal:11434"),
        ("llm_default_provider", "gemini"),
    ):
        monkeypatch.setattr(settings, name, value)
    return settings


def allow_external(lab, allowed: bool = True) -> None:
    from aegis_api.lab.core.org_settings import get_org_settings

    with lab.db() as db:
        row = get_org_settings(db, lab.org_id)
        row.data_processing = {**(row.data_processing or {}), "allow_external_llm": allowed}


def viewer_headers(lab) -> dict[str, str]:
    created = lab.post("/api/v1/api-keys", json={"name": "reader", "role": "viewer", "scopes": ["read"]})
    assert created.status_code == 201, created.text
    return {"Authorization": f"Bearer {created.json()['plaintext']}"}


CONFIG = {
    "provider_kind": "gemini",
    "model": "gemini-org-reasoner",
    "tier": "reasoning",
    "task_types": ["planning", "verification"],
    "priority": 10,
    "input_per_mtok_usd": "1.25",
    "output_per_mtok_usd": "10",
    "capabilities": {"search": False},
}


def test_catalog_never_exposes_secrets(lab, providers):
    response = lab.get("/api/v1/models")
    assert response.status_code == 200, response.text
    assert "sk-super-secret-gemini-key" not in response.text and "ollama.internal" not in response.text
    body = response.json()
    status = {p["kind"]: p for p in body["providers"]}
    assert status["gemini"]["configured"] and not status["gemini"]["usable"]  # no consent yet
    assert status["ollama"]["configured"] and status["ollama"]["usable"] and status["ollama"]["local"]
    assert not status["openai"]["configured"]
    assert body["external_processing_allowed"] is False
    assert body["default_provider"] == "gemini" and body["task_tiers"]["planning"] == "reasoning"
    assert {m["model"] for m in body["tiers"]["reasoning"]} >= {providers.gemini_reasoning_model}
    assert body["deep_research_agent"] == providers.gemini_deep_research_agent
    allow_external(lab)
    assert {p["kind"]: p for p in lab.get("/api/v1/models").json()["providers"]}["gemini"]["usable"]


def test_model_config_crud_and_audit(lab, other_lab):
    created = lab.post("/api/v1/models/configs", json=CONFIG)
    assert created.status_code == 201, created.text
    config = created.json()
    assert config["scope"] == "organization" and Decimal(config["input_per_mtok_usd"]) == Decimal("1.25")
    assert config["task_types"] == ["planning", "verification"] and config["capabilities"] == {"search": False}

    assert lab.post("/api/v1/models/configs", json=CONFIG).status_code == 409
    listing = lab.get("/api/v1/models/configs", params={"provider_kind": "gemini"})
    assert listing.status_code == 200 and config["id"] in [c["id"] for c in listing.json()["items"]]
    assert lab.get(f"/api/v1/models/configs/{config['id']}").json()["model"] == "gemini-org-reasoner"

    patched = lab.patch(f"/api/v1/models/configs/{config['id']}", json={"priority": 5, "enabled": False})
    assert patched.status_code == 200 and patched.json()["priority"] == 5 and patched.json()["enabled"] is False
    assert lab.patch(f"/api/v1/models/configs/{config['id']}", json={"priority": None}).status_code == 422
    assert lab.patch(f"/api/v1/models/configs/{config['id']}", json={"input_per_mtok_usd": None}).status_code == 422

    # tenancy: invisible to other organizations
    for method in ("get", "patch", "delete"):
        kwargs = {"json": {"priority": 1}} if method == "patch" else {}
        assert getattr(other_lab, method)(f"/api/v1/models/configs/{config['id']}", **kwargs).status_code == 404
    assert config["id"] not in [c["id"] for c in other_lab.get("/api/v1/models/configs").json()["items"]]

    assert lab.delete(f"/api/v1/models/configs/{config['id']}").status_code == 204
    assert lab.get(f"/api/v1/models/configs/{config['id']}").status_code == 404

    from aegis_api.models import AuditLog

    with lab.db() as db:
        audits = db.scalars(
            select(AuditLog).where(AuditLog.organization_id == lab.org_id, AuditLog.action == "MODEL_CONFIG_CHANGED")
        ).all()
    assert len(audits) == 3
    assert any(a.before and a.before.get("priority") == 10 and a.after and a.after["priority"] == 5 for a in audits)


def test_model_config_validation(lab):
    for payload in (
        {**CONFIG, "provider_kind": "unknown"},
        {**CONFIG, "tier": "huge"},
        {**CONFIG, "task_types": ["nonsense"]},
        {**CONFIG, "output_per_mtok_usd": None},
        {**CONFIG, "input_per_mtok_usd": "-1"},
        {**CONFIG, "model": "bad model id"},
        {**CONFIG, "capabilities": {"teleport": True}},
        {**CONFIG, "organization_id": str(uuid.uuid4())},
    ):
        response = lab.post("/api/v1/models/configs", json=payload)
        assert response.status_code == 422, (payload, response.text)


def test_viewer_can_read_but_not_manage(lab):
    headers = viewer_headers(lab)
    client = lab.ws.client
    assert client.get("/api/v1/models", headers=headers).status_code == 200
    assert client.get("/api/v1/models/configs", headers=headers).status_code == 200
    assert client.post("/api/v1/models/configs", headers=headers, json=CONFIG).status_code == 403
    assert client.post("/api/v1/models/route-preview", headers=headers, json={"task_type": "coding"}).status_code == 200


def test_platform_rows_are_visible_but_read_only(lab):
    from aegis_api.db.session import session_factory
    from aegis_api.lab.models import ModelConfig

    session = session_factory(admin=True)()
    row = ModelConfig(
        organization_id=None, provider_kind="anthropic", model=f"platform-{uuid.uuid4().hex[:8]}", tier="default"
    )
    session.add(row)
    session.commit()
    try:
        listing = lab.get("/api/v1/models/configs", params={"provider_kind": "anthropic", "page_size": 200})
        platform = [c for c in listing.json()["items"] if c["id"] == str(row.id)]
        assert platform and platform[0]["scope"] == "platform" and platform[0]["organization_id"] is None
        assert lab.get(f"/api/v1/models/configs/{row.id}").status_code == 200
        assert lab.patch(f"/api/v1/models/configs/{row.id}", json={"priority": 1}).status_code == 404
        assert lab.delete(f"/api/v1/models/configs/{row.id}").status_code == 404
    finally:
        session.delete(session.get(ModelConfig, row.id))
        session.commit()
        session.close()


def test_route_preview(lab, providers):
    preview = lab.post("/api/v1/models/route-preview", json={"task_type": "planning"})
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["provider"] == "ollama" and body["external_allowed"] is False  # no consent → local only
    assert any("external processing not allowed" in r["reason"] for r in body["rejected"])

    allow_external(lab)
    body = lab.post("/api/v1/models/route-preview", json={"task_type": "planning"}).json()
    assert body["provider"] == "gemini" and body["model"] == providers.gemini_reasoning_model
    assert body["tier"] == "reasoning" and body["fallbacks"] and "selected gemini:" in body["reason"]

    fast = lab.post("/api/v1/models/route-preview", json={"task_type": "planning", "latency_budget_ms": 500}).json()
    assert fast["tier"] == "fast"

    tools = lab.post(
        "/api/v1/models/route-preview", json={"task_type": "coding", "required_capabilities": ["tools", "search"]}
    ).json()
    assert tools["provider"] == "gemini" and all(f["provider"] == "gemini" for f in tools["fallbacks"])

    created = lab.post("/api/v1/models/configs", json=CONFIG)
    assert created.status_code == 201
    org_route = lab.post("/api/v1/models/route-preview", json={"task_type": "verification"}).json()
    assert org_route["model"] == "gemini-org-reasoner" and org_route["primary"]["source"] == "org"
    assert org_route["primary"]["estimated_cost_usd"] is not None
    budget = lab.post(
        "/api/v1/models/route-preview",
        json={"task_type": "verification", "cost_budget_usd": "0.000001", "estimated_input_tokens": 100000},
    ).json()
    assert budget["model"] != "gemini-org-reasoner"
    assert lab.post("/api/v1/models/route-preview", json={"task_type": "astrology"}).status_code == 422


def _record_usage(lab, count: int, *, provider: str = "gemini", success: bool = True) -> None:
    from aegis_api.lab.usage.recorder import record_model_usage

    with lab.db() as db:
        for i in range(count):
            record_model_usage(
                db,
                organization_id=lab.org_id,
                provider=provider,
                model=f"{provider}-model",
                task_type="planning" if i % 2 else "coding",
                input_tokens=100,
                output_tokens=10,
                cached_tokens=5,
                thinking_tokens=3,
                latency_ms=100 * (i + 1),
                cost_usd=Decimal("0.01"),
                success=success,
                error_code=None if success else "llm_timeout",
                project_id=lab.project_id,
            )


def test_usage_listing_and_summary(lab, other_lab):
    _record_usage(lab, 3)
    _record_usage(lab, 2, provider="ollama", success=False)
    _record_usage(other_lab, 4)

    first = lab.get("/api/v1/models/usage", params={"limit": 3})
    assert first.status_code == 200, first.text
    page = first.json()
    assert len(page["items"]) == 3 and page["next_cursor"]
    second = lab.get("/api/v1/models/usage", params={"limit": 3, "cursor": page["next_cursor"]}).json()
    assert len(second["items"]) == 2 and second["next_cursor"] is None
    ids = [i["id"] for i in page["items"] + second["items"]]
    assert len(set(ids)) == 5
    failures = lab.get("/api/v1/models/usage", params={"success": "false"}).json()["items"]
    assert len(failures) == 2 and all(f["error_code"] == "llm_timeout" for f in failures)
    assert len(lab.get("/api/v1/models/usage", params={"provider": "gemini"}).json()["items"]) == 3
    assert lab.get("/api/v1/models/usage", params={"cursor": "garbage!!"}).status_code == 422

    summary = lab.get("/api/v1/models/usage/summary")
    assert summary.status_code == 200, summary.text
    body = summary.json()
    assert body["group_by"] == ["provider", "model", "task_type"]
    assert body["totals"]["calls"] == 5 and body["totals"]["failures"] == 2
    assert body["totals"]["input_tokens"] == 500 and body["totals"]["thinking_tokens"] == 15
    assert Decimal(body["totals"]["cost_usd"]) == Decimal("0.05")
    by_provider = lab.get("/api/v1/models/usage/summary", params={"group_by": "provider"}).json()
    rows = {g["provider"]: g for g in by_provider["groups"]}
    assert rows["gemini"]["calls"] == 3 and rows["ollama"]["failures"] == 2 and rows["gemini"]["model"] is None

    now = datetime.now(UTC)
    empty = lab.get(
        "/api/v1/models/usage/summary",
        params={"since": (now - timedelta(days=30)).isoformat(), "until": (now - timedelta(days=20)).isoformat()},
    ).json()
    assert empty["totals"]["calls"] == 0 and empty["groups"] == []
    assert lab.get("/api/v1/models/usage/summary", params={"group_by": "user"}).status_code == 422
    bad_range = lab.get(
        "/api/v1/models/usage/summary",
        params={"since": now.isoformat(), "until": (now - timedelta(days=1)).isoformat()},
    )
    assert bad_range.status_code == 422

    other = other_lab.get("/api/v1/models/usage/summary").json()
    assert other["totals"]["calls"] == 4


def test_usage_requires_usage_read(lab):
    from aegis_api.errors import Forbidden
    from aegis_api.lab.core.pagination import CursorParams
    from aegis_api.lab.llm import service

    actor = lab.actor(role="scientist_operator")
    actor_without = type(actor)(**{**actor.__dict__, "permissions": actor.permissions - {"usage:read"}})
    with lab.db() as db, pytest.raises(Forbidden):
        service.list_usage(db, actor_without, CursorParams())
