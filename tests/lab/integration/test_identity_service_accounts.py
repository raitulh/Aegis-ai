"""Identity: service accounts (key auth, project restriction, no human-only permissions) and API key rotation."""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest

from tests.conftest import requires_db, signup

pytestmark = [requires_db, pytest.mark.db]


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _create_sa(lab, **overrides):
    payload = {"name": f"sa-{uuid.uuid4().hex[:8]}", "role": "researcher", "scopes": ["read", "write", "run"]}
    payload.update(overrides)
    r = lab.post("/api/v1/service-accounts", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _issue_key(lab, account_id: str) -> dict:
    r = lab.post(f"/api/v1/service-accounts/{account_id}/keys", json={"name": "ci"})
    assert r.status_code == 201, r.text
    return r.json()


def _add_member(client, lab, role: str):
    from aegis_api.db.session import session_factory
    from aegis_api.models import Membership

    ws = signup(client, org=f"{role} home")
    session = session_factory(admin=True)()
    try:
        session.add(Membership(organization_id=lab.org_id, user_id=uuid.UUID(ws.user_id), role=role, status="active"))
        session.commit()
    finally:
        session.close()
    return ws, {"X-Aegis-Org": str(lab.org_id)}


def test_service_account_crud_and_validation(client, lab, other_lab):
    sa = _create_sa(lab, project_ids=[str(lab.project_id)], description="nightly benchmarks")
    assert sa["role"] == "researcher" and sa["scopes"] == ["read", "write", "run"]
    assert sa["project_ids"] == [str(lab.project_id)] and sa["disabled_at"] is None

    assert lab.post("/api/v1/service-accounts", json={"name": sa["name"]}).status_code == 409
    assert lab.post("/api/v1/service-accounts", json={"name": "x1", "role": "owner"}).status_code == 422
    assert lab.post("/api/v1/service-accounts", json={"name": "x2", "scopes": ["admin"]}).status_code == 422
    assert lab.post("/api/v1/service-accounts", json={"name": "x3", "scopes": []}).status_code == 422
    foreign_project = lab.post(
        "/api/v1/service-accounts", json={"name": "x4", "project_ids": [str(other_lab.project_id)]}
    )
    assert foreign_project.status_code == 404

    listed = lab.get("/api/v1/service-accounts").json()
    assert sa["id"] in {a["id"] for a in listed["items"]}
    patched = lab.patch(f"/api/v1/service-accounts/{sa['id']}", json={"description": "updated", "scopes": ["read"]})
    assert patched.status_code == 200 and patched.json()["scopes"] == ["read"]

    # Other tenants cannot see or use it.
    assert other_lab.get(f"/api/v1/service-accounts/{sa['id']}").status_code == 404
    assert other_lab.post(f"/api/v1/service-accounts/{sa['id']}/keys", json={}).status_code == 404
    assert other_lab.post(f"/api/v1/service-accounts/{sa['id']}/disable").status_code == 404

    # Only admins manage service accounts.
    researcher, headers = _add_member(client, lab, "researcher")
    assert researcher.get("/api/v1/service-accounts", headers=headers).status_code == 403


def test_service_account_key_authentication_and_restrictions(client, lab):
    other_project = lab.post("/api/v1/projects", json={"workspace_id": str(lab.workspace_id), "name": "Off Limits"})
    other_project_id = other_project.json()["id"]
    sa = _create_sa(lab, role="research_lead", project_ids=[str(lab.project_id)])
    issued = _issue_key(lab, sa["id"])
    key = issued["plaintext"]
    assert issued["api_key"]["service_account_id"] == sa["id"]
    assert issued["api_key"]["role"] == "research_lead"
    listed = lab.get(f"/api/v1/service-accounts/{sa['id']}/keys").json()
    assert [k["id"] for k in listed] == [issued["api_key"]["id"]]
    assert all("plaintext" not in k and "key_hash" not in k for k in listed)

    client.cookies.clear()
    me = client.get("/api/v1/users/me", headers=_bearer(key))
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["kind"] == "service_account" and body["auth_method"] == "service_account"
    assert body["service_account_id"] == sa["id"] and body["user"] is None
    assert body["project_ids"] == [str(lab.project_id)]
    from aegis_api.security.rbac import HUMAN_ONLY_PERMISSIONS

    assert not HUMAN_ONLY_PERMISSIONS & set(body["permissions"])  # e.g. approval:decide, strategy:promote
    assert "approval:decide" not in body["lab_permissions"] and "mission:create" in body["lab_permissions"]

    # Limited to its project list.
    visible = {p["id"] for p in client.get("/api/v1/projects", headers=_bearer(key)).json()["items"]}
    assert visible == {str(lab.project_id)}
    assert client.get(f"/api/v1/projects/{lab.project_id}", headers=_bearer(key)).status_code == 200
    assert client.get(f"/api/v1/projects/{other_project_id}", headers=_bearer(key)).status_code == 404

    # Human-only operations are refused even though the account's role would allow them.
    assert client.post("/api/v1/teams", json={"name": "bots"}, headers=_bearer(key)).status_code == 403
    assert (
        client.post(
            f"/api/v1/projects/{lab.project_id}/members",
            json={"user_id": str(lab.user_id), "role": "viewer"},
            headers=_bearer(key),
        ).status_code
        == 403
    )
    assert client.post("/api/v1/service-accounts", json={"name": "child"}, headers=_bearer(key)).status_code == 403
    assert client.post("/api/v1/organizations", json={"name": "Bot Org"}, headers=_bearer(key)).status_code == 403

    # The acting identity is a non-human service account (approvals etc. call require_human).
    from aegis_api.errors import Forbidden
    from aegis_api.lab.core.actor import Actor
    from aegis_api.security.context import Principal

    principal = Principal(
        user_id=uuid.UUID(int=0),
        organization_id=lab.org_id,
        role="research_lead",
        permissions=frozenset(body["permissions"]),
        auth_method="service_account",
        service_account_id=uuid.UUID(sa["id"]),
        project_ids=body["project_ids"],
    )
    actor = Actor.from_principal(principal)
    assert actor.kind == "service_account" and not actor.is_human and actor.user_id is None
    with pytest.raises(Forbidden):
        actor.require_human("approval decision")

    account = lab.get(f"/api/v1/service-accounts/{sa['id']}").json()
    assert account["last_used_at"] is not None


def test_service_account_changes_apply_immediately_and_disable(client, lab):
    sa = _create_sa(lab, role="analyst", scopes=["read", "write"])
    key = _issue_key(lab, sa["id"])["plaintext"]
    client.cookies.clear()
    before = set(client.get("/api/v1/users/me", headers=_bearer(key)).json()["permissions"])
    assert "systems:write" in before
    lab.patch(f"/api/v1/service-accounts/{sa['id']}", json={"scopes": ["read"]})
    after = set(client.get("/api/v1/users/me", headers=_bearer(key)).json()["permissions"])
    assert "systems:write" not in after and "systems:read" in after
    # Widening the account never silently upgrades an existing key beyond what it was issued with.
    lab.patch(f"/api/v1/service-accounts/{sa['id']}", json={"scopes": ["read", "write", "run"]})
    widened = set(client.get("/api/v1/users/me", headers=_bearer(key)).json()["permissions"])
    assert "audits:run" not in widened and "systems:write" in widened

    disabled = lab.post(f"/api/v1/service-accounts/{sa['id']}/disable")
    assert disabled.status_code == 200 and disabled.json()["disabled_at"] is not None
    denied = client.get("/api/v1/users/me", headers=_bearer(key))
    assert denied.status_code == 401 and denied.json()["error"]["code"] == "service_account_disabled"
    assert lab.post(f"/api/v1/service-accounts/{sa['id']}/keys", json={}).status_code == 409
    assert lab.post(f"/api/v1/service-accounts/{sa['id']}/disable").status_code == 409


def test_api_key_rotation_with_grace(client, lab):
    from aegis_api.models import AuditLog

    created = lab.post("/api/v1/api-keys", json={"name": "deploy", "role": "analyst", "scopes": ["read"]}).json()
    old_key, old_id = created["plaintext"], created["api_key"]["id"]
    client.cookies.clear()

    rotated = lab.post(f"/api/v1/api-keys/{old_id}/rotate", json={"grace_seconds": 3600})
    assert rotated.status_code == 201, rotated.text
    body = rotated.json()
    new_key = body["plaintext"]
    assert new_key != old_key and body["previous_key_id"] == old_id and body["previous_key_revoked"] is False
    assert body["api_key"]["rotated_from_id"] == old_id and body["api_key"]["name"] == "deploy"
    assert body["api_key"]["role"] == "analyst" and body["api_key"]["scopes"] == ["read"]
    grace_end = datetime.fromisoformat(body["previous_key_expires_at"])
    assert 3500 < (grace_end - datetime.now(grace_end.tzinfo)).total_seconds() <= 3600
    # Both keys work during the grace period.
    assert client.get("/api/v1/overview", headers=_bearer(old_key)).status_code == 200
    assert client.get("/api/v1/overview", headers=_bearer(new_key)).status_code == 200

    immediate = lab.post(f"/api/v1/api-keys/{body['api_key']['id']}/rotate", json={"grace_seconds": 0}).json()
    assert immediate["previous_key_revoked"] is True
    assert client.get("/api/v1/overview", headers=_bearer(new_key)).status_code == 401
    assert client.get("/api/v1/overview", headers=_bearer(immediate["plaintext"])).status_code == 200
    assert lab.post(f"/api/v1/api-keys/{body['api_key']['id']}/rotate", json={}).status_code == 409

    assert lab.post(f"/api/v1/api-keys/{uuid.uuid4()}/rotate", json={}).status_code == 404
    assert lab.post(f"/api/v1/api-keys/{old_id}/rotate", json={"grace_seconds": 86401}).status_code == 422
    with lab.db() as db:
        assert db.query(AuditLog).filter(AuditLog.action == "API_KEY_ROTATED").count() == 2


def test_api_key_rotation_cross_tenant_and_permissions(client, lab, other_lab):
    created = lab.post("/api/v1/api-keys", json={"name": "k", "role": "owner", "scopes": ["read"]}).json()
    key_id = created["api_key"]["id"]
    assert other_lab.post(f"/api/v1/api-keys/{key_id}/rotate", json={}).status_code == 404

    admin, headers = _add_member(client, lab, "admin")
    # An admin cannot mint a replacement for an owner-level key.
    assert admin.post(f"/api/v1/api-keys/{key_id}/rotate", json={}, headers=headers).status_code == 403
    # Machine credentials cannot rotate keys (api_keys:manage is human-only).
    analyst_key = lab.post("/api/v1/api-keys", json={"name": "a", "role": "admin", "scopes": ["read", "write"]}).json()
    client.cookies.clear()
    r = client.post(f"/api/v1/api-keys/{key_id}/rotate", json={}, headers=_bearer(analyst_key["plaintext"]))
    assert r.status_code == 403


def test_service_account_key_rotation_keeps_binding(client, lab):
    sa = _create_sa(lab)
    issued = _issue_key(lab, sa["id"])
    rotated = lab.post(f"/api/v1/api-keys/{issued['api_key']['id']}/rotate", json={"grace_seconds": 0}).json()
    assert rotated["api_key"]["service_account_id"] == sa["id"]
    client.cookies.clear()
    me = client.get("/api/v1/users/me", headers=_bearer(rotated["plaintext"])).json()
    assert me["service_account_id"] == sa["id"]
    assert client.get("/api/v1/users/me", headers=_bearer(issued["plaintext"])).status_code == 401
    keys = lab.get(f"/api/v1/service-accounts/{sa['id']}/keys").json()
    assert len(keys) == 2 and sum(k["revoked_at"] is None for k in keys) == 1


def test_service_account_writes_are_not_attributed_to_a_person(client, lab):
    from aegis_api.lab.models import ProjectMember, Workspace

    sa = _create_sa(lab, role="admin", scopes=["read", "write"])
    key = _issue_key(lab, sa["id"])["plaintext"]
    client.cookies.clear()
    ws = client.post("/api/v1/workspaces", json={"name": "Automation"}, headers=_bearer(key))
    assert ws.status_code == 201, ws.text
    assert ws.json()["created_by_id"] is None
    project = client.post(
        "/api/v1/projects", json={"workspace_id": ws.json()["id"], "name": "Nightly"}, headers=_bearer(key)
    )
    assert project.status_code == 201, project.text
    with lab.db() as db:
        assert db.query(ProjectMember).filter_by(project_id=uuid.UUID(project.json()["id"])).count() == 0
        assert db.get(Workspace, uuid.UUID(ws.json()["id"])).created_by_id is None
