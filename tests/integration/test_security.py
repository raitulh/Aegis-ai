"""Security tests: authn, cross-tenant isolation (RLS), IDOR, RBAC, uploads, API keys, validation."""

from __future__ import annotations

import io

import pytest

from tests.conftest import requires_db, signup

pytestmark = [requires_db, pytest.mark.db]


def test_unauthenticated_access_rejected(client):
    for path in ("/api/v1/systems", "/api/v1/findings", "/api/v1/overview"):
        assert client.get(path).status_code == 401


def test_cross_tenant_isolation(client, demo_provider_and_system):
    _ws_a, _pid, sid = demo_provider_and_system
    ws_b = signup(client, org="Other Org")
    # B cannot read A's system (IDOR / cross-tenant)
    assert ws_b.get(f"/api/v1/systems/{sid}").status_code == 404
    # B's system list does not include A's system
    b_systems = ws_b.get("/api/v1/systems").json()["items"]
    assert all(s["id"] != sid for s in b_systems)
    # B cannot start an audit on A's system
    r = ws_b.post("/api/v1/audits", json={"system_id": sid, "categories": ["privacy"]})
    assert r.status_code == 404


def test_rls_blocks_direct_cross_tenant_rows(admin_session, client):
    """RLS must prevent the app role from reading another org's rows even with a crafted query."""
    from sqlalchemy import text

    from aegis_api.db.session import session_factory, set_tenant

    a = signup(client, org="RLS A")
    b = signup(client, org="RLS B")
    app = session_factory()()
    try:
        app.begin()
        set_tenant(app, b.org_id, b.user_id)
        rows = app.execute(text("select id from organizations")).all()
        ids = {str(r[0]) for r in rows}
        assert a.org_id not in ids
        assert b.org_id in ids
    finally:
        app.rollback()
        app.close()


def test_rbac_viewer_cannot_write(client):
    owner = signup(client, org="RBAC Org")
    # Create an API key with the viewer role and only read scope
    key = owner.post("/api/v1/api-keys", json={"name": "viewer", "role": "viewer", "scopes": ["read"]}).json()[
        "plaintext"
    ]
    headers = {"Authorization": f"Bearer {key}"}
    assert client.get("/api/v1/systems", headers=headers).status_code == 200
    r = client.post("/api/v1/systems", headers=headers, json={"name": "X", "system_type": "llm"})
    assert r.status_code == 403


def test_invalid_uuid_returns_422_not_500(workspace):
    assert workspace.get("/api/v1/systems/not-a-uuid").status_code == 422


def test_api_key_revocation(client):
    ws = signup(client, org="Key Org")
    created = ws.post("/api/v1/api-keys", json={"name": "k", "role": "analyst", "scopes": ["read"]}).json()
    key = created["plaintext"]
    headers = {"Authorization": f"Bearer {key}"}
    assert client.get("/api/v1/overview", headers=headers).status_code == 200
    ws.delete(f"/api/v1/api-keys/{created['api_key']['id']}")
    assert client.get("/api/v1/overview", headers=headers).status_code == 401


def test_upload_rejects_wrong_type(workspace):
    policy = workspace.post("/api/v1/policies", json={"name": "P", "key": "PX"}).json()
    files = {"file": ("evil.exe", io.BytesIO(b"MZ\x90\x00binary"), "application/octet-stream")}
    r = workspace.post(f"/api/v1/policies/{policy['id']}/upload", files=files)
    assert r.status_code in (415, 422)


def test_password_strength_enforced(client):
    r = client.post("/api/v1/auth/signup", json={"email": "weak@example.com", "password": "weak", "full_name": "W"})
    assert r.status_code == 422


def test_ssrf_blocked_on_provider_base_url(workspace):
    r = workspace.post(
        "/api/v1/providers", json={"kind": "openai", "name": "x", "base_url": "http://169.254.169.254/latest/meta-data"}
    )
    assert r.status_code in (400, 422)


def test_finding_status_transition_requires_permission(client, demo_provider_and_system):
    ws, _pid, sid = demo_provider_and_system
    import time

    aid = ws.post("/api/v1/audits", json={"system_id": sid, "categories": ["fairness"], "intensity": "quick"}).json()[
        "id"
    ]
    for _ in range(60):
        if ws.get(f"/api/v1/audits/{aid}").json()["status"] in ("completed", "partially_completed", "failed"):
            break
        time.sleep(0.5)
    findings = ws.get("/api/v1/findings").json()["items"]
    if findings:
        # Owner can accept risk; an analyst-scoped key cannot.
        key = ws.post("/api/v1/api-keys", json={"name": "an", "role": "analyst", "scopes": ["read", "write"]}).json()[
            "plaintext"
        ]
        r = client.patch(
            f"/api/v1/findings/{findings[0]['id']}",
            headers={"Authorization": f"Bearer {key}"},
            json={"status": "accepted_risk"},
        )
        assert r.status_code == 403
