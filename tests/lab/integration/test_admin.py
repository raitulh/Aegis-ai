"""Platform admin API (cross-tenant operator) and organization suspension."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tests.conftest import requires_db, signup

pytestmark = [requires_db, pytest.mark.db]


@pytest.fixture
def platform_admin(client):
    """A signed-up user flagged as platform admin out-of-band (the flag is never settable via the API)."""
    from aegis_api.db.session import session_factory
    from aegis_api.models import User

    ws = signup(client, org="Platform Ops")
    session = session_factory(admin=True)()
    try:
        session.get(User, uuid.UUID(ws.user_id)).is_platform_admin = True
        session.commit()
    finally:
        session.close()
    return ws


def _admin_session():
    from aegis_api.db.session import session_factory

    return session_factory(admin=True)()


def test_admin_api_hidden_from_non_admins(client, lab):
    for path in ("/api/v1/admin/organizations", "/api/v1/admin/system", "/api/v1/admin/jobs"):
        r = lab.get(path)
        assert r.status_code == 404, (path, r.text)  # organization owners are not platform admins
    assert lab.post(f"/api/v1/admin/organizations/{lab.org_id}/suspend", json={"reason": "nope"}).status_code == 404
    client.cookies.clear()
    assert client.get("/api/v1/admin/organizations").status_code == 401
    me = lab.get("/api/v1/users/me").json()
    assert me["is_platform_admin"] is False


def test_platform_admin_lists_organizations(lab, platform_admin):
    assert platform_admin.get("/api/v1/users/me").json()["is_platform_admin"] is True
    r = platform_admin.get("/api/v1/admin/organizations", params={"q": lab.ws.data["organization"]["slug"]})
    assert r.status_code == 200, r.text
    orgs = {o["id"]: o for o in r.json()["items"]}
    assert str(lab.org_id) in orgs
    entry = orgs[str(lab.org_id)]
    assert entry["member_count"] == 1 and entry["suspended"] is False and entry["plan"] == "free"
    assert platform_admin.get("/api/v1/admin/organizations", params={"page_size": 1}).json()["meta"]["total"] >= 2


def test_platform_admin_api_keys_are_not_admin(client, platform_admin):
    key = platform_admin.post("/api/v1/api-keys", json={"name": "ops", "role": "owner", "scopes": ["read"]}).json()
    client.cookies.clear()
    r = client.get("/api/v1/admin/organizations", headers={"Authorization": f"Bearer {key['plaintext']}"})
    assert r.status_code == 404


def test_suspension_blocks_every_principal_and_is_audited(client, lab, platform_admin):
    from aegis_api.models import AuditLog

    api_key = lab.post("/api/v1/api-keys", json={"name": "k", "role": "viewer", "scopes": ["read"]}).json()["plaintext"]
    email = lab.ws.data["user"]["email"]
    client.cookies.clear()
    jwt_access = client.post("/api/v1/auth/token", json={"email": email, "password": "Str0ng-Pass!23"}).json()

    r = platform_admin.post(f"/api/v1/admin/organizations/{lab.org_id}/suspend", json={"reason": "abuse report #42"})
    assert r.status_code == 200, r.text
    assert r.json()["suspended"] is True and r.json()["suspended_reason"] == "abuse report #42"
    assert (
        platform_admin.post(f"/api/v1/admin/organizations/{lab.org_id}/suspend", json={"reason": "again"}).status_code
        == 409
    )

    for response in (
        lab.get("/api/v1/projects"),
        lab.get("/api/v1/users/me"),
        lab.get("/api/v1/systems"),
        client.get("/api/v1/overview", headers={"Authorization": f"Bearer {api_key}"}),
        client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {jwt_access['access_token']}"}),
    ):
        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "organization_suspended"
    refreshed = client.post("/api/v1/auth/token/refresh", json={"refresh_token": jwt_access["refresh_token"]})
    assert refreshed.status_code == 403
    assert client.post("/api/v1/auth/token", json={"email": email, "password": "Str0ng-Pass!23"}).status_code == 403

    listed = platform_admin.get("/api/v1/admin/organizations", params={"suspended": True}).json()["items"]
    assert str(lab.org_id) in {o["id"] for o in listed}

    restored = platform_admin.post(f"/api/v1/admin/organizations/{lab.org_id}/unsuspend", json={"reason": "resolved"})
    assert restored.status_code == 200 and restored.json()["suspended"] is False
    assert lab.get("/api/v1/projects").status_code == 200
    assert platform_admin.post(f"/api/v1/admin/organizations/{lab.org_id}/unsuspend").status_code == 409

    with lab.db() as db:
        entries = db.query(AuditLog).filter(AuditLog.action == "ADMIN_ACTION").order_by(AuditLog.created_at).all()
    assert [e.after["operation"] for e in entries] == ["organization.suspend", "organization.unsuspend"]
    assert all(e.user_id == uuid.UUID(platform_admin.user_id) for e in entries)
    assert entries[0].after["reason"] == "abuse report #42"


def test_platform_admin_excepted_from_suspension(platform_admin):
    r = platform_admin.post(
        f"/api/v1/admin/organizations/{platform_admin.org_id}/suspend", json={"reason": "maintenance test"}
    )
    assert r.status_code == 200
    assert platform_admin.get("/api/v1/users/me").status_code == 200
    assert platform_admin.post(f"/api/v1/admin/organizations/{platform_admin.org_id}/unsuspend").status_code == 200


def test_admin_jobs_and_compute_jobs(lab, platform_admin):
    from aegis_api.lab.models import ComputeJob, WorkflowRun

    session = _admin_session()
    try:
        runs = [
            WorkflowRun(
                organization_id=lab.org_id,
                kind="MissionWorkflow",
                subject_type="mission",
                subject_id=str(uuid.uuid4()),
                engine="local",
                external_id=f"admin-test-{uuid.uuid4()}",
                status="RUNNING",
            )
            for _ in range(3)
        ]
        session.add_all(runs)
        job = ComputeJob(
            organization_id=lab.org_id,
            workspace_id=lab.workspace_id,
            project_id=lab.project_id,
            backend="local_docker",
            image="python:3.12-slim",
            timeout_seconds=60,
            idempotency_key=f"admin-test-{uuid.uuid4()}",
            status="QUEUED",
        )
        session.add(job)
        session.commit()
        run_ids = {str(r.id) for r in runs}
        job_id = str(job.id)
    finally:
        session.close()

    seen: set[str] = set()
    cursor = None
    for _ in range(3):
        params = {"status": "RUNNING", "org_id": str(lab.org_id), "limit": 1}
        if cursor:
            params["cursor"] = cursor
        page = platform_admin.get("/api/v1/admin/jobs", params=params)
        assert page.status_code == 200, page.text
        seen |= {item["id"] for item in page.json()["items"]}
        cursor = page.json()["next_cursor"]
    assert seen == run_ids
    assert platform_admin.get("/api/v1/admin/jobs", params={"status": "BOGUS"}).status_code == 422

    jobs = platform_admin.get(
        "/api/v1/admin/compute-jobs", params={"status": "QUEUED", "org_id": str(lab.org_id)}
    ).json()
    assert [j["id"] for j in jobs["items"]] == [job_id]
    assert jobs["items"][0]["image"] == "python:3.12-slim"


def test_admin_usage_totals(lab, platform_admin):
    from aegis_api.lab.models import ComputeJob, ComputeUsage, ModelUsage

    session = _admin_session()
    try:
        job = ComputeJob(
            organization_id=lab.org_id,
            workspace_id=lab.workspace_id,
            project_id=lab.project_id,
            backend="local_docker",
            image="python:3.12-slim",
            timeout_seconds=60,
            idempotency_key=f"usage-{uuid.uuid4()}",
            status="SUCCEEDED",
        )
        session.add(job)
        session.flush()
        session.add_all(
            [
                ModelUsage(
                    organization_id=lab.org_id,
                    provider="gemini",
                    model="test-model",
                    task_type="test",
                    input_tokens=100,
                    output_tokens=50,
                    cost_usd=Decimal("900.250000"),
                ),
                ModelUsage(
                    organization_id=lab.org_id,
                    provider="gemini",
                    model="test-model",
                    task_type="test",
                    success=False,
                    cost_usd=Decimal("0"),
                ),
                ComputeUsage(
                    organization_id=lab.org_id,
                    compute_job_id=job.id,
                    backend="local_docker",
                    cpu_seconds=12.5,
                    wall_seconds=10.0,
                    cost_usd=Decimal("100.000001"),
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    now = datetime.now(UTC)
    params = {"start": (now - timedelta(minutes=5)).isoformat(), "end": (now + timedelta(minutes=5)).isoformat()}
    r = platform_admin.get("/api/v1/admin/usage", params=params)
    assert r.status_code == 200, r.text
    body = r.json()
    mine = next(o for o in body["by_organization"] if o["organization_id"] == str(lab.org_id))
    assert Decimal(mine["llm_cost_usd"]) == Decimal("900.25")
    assert Decimal(mine["compute_cost_usd"]) == Decimal("100.000001")
    assert Decimal(mine["total_cost_usd"]) == Decimal("1000.250001")
    assert mine["llm_calls"] == 2 and mine["compute_jobs"] == 1
    assert body["totals"]["llm_failed_calls"] >= 1 and body["totals"]["input_tokens"] >= 100

    bad = platform_admin.get("/api/v1/admin/usage", params={"start": params["end"], "end": params["start"]})
    assert bad.status_code == 422
    too_long = platform_admin.get(
        "/api/v1/admin/usage", params={"start": (now - timedelta(days=400)).isoformat(), "end": now.isoformat()}
    )
    assert too_long.status_code == 422


def test_admin_global_feature_flags(lab, platform_admin):
    # Use a flag whose override equals its default so concurrently running suites see no behaviour change.
    from aegis_api.config import get_settings

    key = "evolution"
    default = get_settings().feature_defaults()[key]
    try:
        r = platform_admin.request("PUT", f"/api/v1/admin/feature-flags/{key}", json={"enabled": default})
        assert r.status_code == 200, r.text
        assert r.json()["global_override"] is default and r.json()["effective_default"] is default
        org_view = {f["key"]: f for f in lab.get("/api/v1/organizations/current/features").json()}
        assert org_view[key]["source"] == "global" and org_view[key]["enabled"] is default

        bulk = platform_admin.request("PUT", "/api/v1/admin/feature-flags", json={"flags": {key: None}})
        assert bulk.status_code == 200, bulk.text
        assert next(f for f in bulk.json() if f["key"] == key)["global_override"] is None
        assert (
            platform_admin.request("PUT", "/api/v1/admin/feature-flags/not_a_flag", json={"enabled": True}).status_code
            == 404
        )
        listed = platform_admin.get("/api/v1/admin/feature-flags").json()
        assert {f["key"] for f in listed} == set(get_settings().feature_defaults())
    finally:
        platform_admin.request("PUT", f"/api/v1/admin/feature-flags/{key}", json={"enabled": None})


def test_admin_model_providers_and_system(platform_admin):
    providers = platform_admin.get("/api/v1/admin/model-providers")
    assert providers.status_code == 200
    body = providers.json()
    assert set(body["providers"]) == {"gemini", "openai", "anthropic", "ollama"}
    assert all(isinstance(v, bool) for v in body["providers"].values())

    system = platform_admin.get("/api/v1/admin/system")
    assert system.status_code == 200, system.text
    info = system.json()
    assert info["environment"] == "test"
    assert info["workflow_engine"] in {"local", "temporal"}
    assert info["schema_revision"] and info["database_version"]
    assert "fastapi" in info["library_versions"]
