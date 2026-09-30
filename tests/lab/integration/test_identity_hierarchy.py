"""Identity: organizations, settings, feature flags, workspaces, projects, project roles, teams, users, RBAC."""

from __future__ import annotations

import uuid

import pytest

from tests.conftest import requires_db, signup

pytestmark = [requires_db, pytest.mark.db]


class Member:
    """A second user who belongs to the lab organization with a given org role."""

    def __init__(self, ws, org_id: uuid.UUID) -> None:
        self.ws = ws
        self.user_id = ws.user_id
        self.headers = {"X-Aegis-Org": str(org_id)}

    def request(self, method: str, path: str, **kw):
        headers = {**self.headers, **kw.pop("headers", {})}
        return self.ws.request(method, path, headers=headers, **kw)

    def get(self, path: str, **kw):
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw):
        return self.request("POST", path, **kw)

    def patch(self, path: str, **kw):
        return self.request("PATCH", path, **kw)

    def delete(self, path: str, **kw):
        return self.request("DELETE", path, **kw)


def add_member(client, lab, role: str) -> Member:
    from aegis_api.db.session import session_factory
    from aegis_api.models import Membership

    ws = signup(client, org=f"{role} home")
    session = session_factory(admin=True)()
    try:
        session.add(Membership(organization_id=lab.org_id, user_id=uuid.UUID(ws.user_id), role=role, status="active"))
        session.commit()
    finally:
        session.close()
    return Member(ws, lab.org_id)


# --- organizations ---------------------------------------------------------------------------------
def test_organizations_list_create_and_current(lab):
    orgs = lab.get("/api/v1/organizations")
    assert orgs.status_code == 200, orgs.text
    current = [o for o in orgs.json() if o["is_current"]]
    assert len(current) == 1 and current[0]["id"] == str(lab.org_id) and current[0]["role"] == "owner"

    created = lab.post("/api/v1/organizations", json={"name": "Second Lab"})
    assert created.status_code == 201, created.text
    new_org = created.json()
    assert new_org["role"] == "owner" and new_org["slug"].startswith("second-lab")

    ids = {o["id"] for o in lab.get("/api/v1/organizations").json()}
    assert {str(lab.org_id), new_org["id"]} <= ids
    # Creating an org does not switch the caller's default organization.
    assert lab.get("/api/v1/organizations/current").json()["id"] == str(lab.org_id)
    # The new org is reachable explicitly and the caller is its owner.
    other = lab.get("/api/v1/organizations/current", headers={"X-Aegis-Org": new_org["id"]})
    assert other.status_code == 200 and other.json()["role"] == "owner"

    renamed = lab.patch("/api/v1/organizations/current", json={"name": "Renamed Lab"})
    assert renamed.status_code == 200 and renamed.json()["name"] == "Renamed Lab"
    assert renamed.json()["member_count"] >= 1


def test_org_rename_requires_org_manage(client, lab):
    researcher = add_member(client, lab, "researcher")
    assert researcher.patch("/api/v1/organizations/current", json={"name": "Nope"}).status_code == 403


def test_org_settings_validation_and_update(lab):
    settings = lab.get("/api/v1/organizations/current/settings")
    assert settings.status_code == 200, settings.text
    body = settings.json()
    assert "quotas" in body and body["quotas"]["max_missions"] > 0

    ok = lab.patch(
        "/api/v1/organizations/current/settings",
        json={
            "max_autonomy_level": "L2_AUTOMATED_EXPERIMENT_DESIGN",
            "default_autonomy_level": "L1_RESEARCH_AUTOMATION",
            "retention": {"agent_logs_days": 30, "artifacts_days": None},
            "egress_allowlist": ["api.openalex.org", ".arxiv.org", "API.OpenAlex.org"],
            "execution_policy": {"allowed_images": ["python:3.12-slim"], "max_timeout_seconds": 600},
            "data_processing": {"allow_external_llm": True},
            "lock_version": body["lock_version"],
        },
    )
    assert ok.status_code == 200, ok.text
    updated = ok.json()
    assert updated["max_autonomy_level"] == "L2_AUTOMATED_EXPERIMENT_DESIGN"
    assert updated["egress_allowlist"] == ["api.openalex.org", ".arxiv.org"]
    assert updated["retention"]["agent_logs_days"] == 30 and updated["retention"]["artifacts_days"] is None
    assert updated["execution_policy"] == {"allowed_images": ["python:3.12-slim"], "max_timeout_seconds": 600}
    assert updated["data_processing"]["allow_external_llm"] is True
    assert updated["lock_version"] == body["lock_version"] + 1

    bad_requests = [
        {"default_autonomy_level": "L4_CLOSED_LOOP_EVOLUTION"},  # default above max
        {"max_autonomy_level": "L9_SKYNET"},
        {"retention": {"agent_logs_days": -1}},
        {"retention": {"unknown_class": 5}},
        {"egress_allowlist": ["10.0.0.1"]},
        {"egress_allowlist": ["*.example.com"]},
        {"egress_allowlist": ["https://example.com"]},
        {"egress_allowlist": ["localhost"]},
        {"execution_policy": {"allowed_images": ["evil/image:latest"]}},  # not in platform allowlist
        {"execution_policy": {"allowed_images": ["Bad Image!!"]}},
        {"execution_policy": {"max_timeout_seconds": 10**9}},
        {"execution_policy": {"gpu_enabled": True}},  # platform GPU disabled
        {"quotas": {"max_missions": 1}},  # quotas are governance-owned
        {"sso_enforced": True},  # no enabled IdP / feature disabled
    ]
    for payload in bad_requests:
        r = lab.patch("/api/v1/organizations/current/settings", json=payload)
        assert r.status_code == 422, (payload, r.text)

    stale = lab.patch("/api/v1/organizations/current/settings", json={"lock_version": 1, "sso_enforced": False})
    assert stale.status_code == 409


def test_org_settings_update_requires_admin(client, lab):
    researcher = add_member(client, lab, "researcher")
    assert researcher.get("/api/v1/organizations/current/settings").status_code == 200
    r = researcher.patch("/api/v1/organizations/current/settings", json={"sso_enforced": False})
    assert r.status_code == 403


def test_feature_flags(lab):
    from aegis_api.models import AuditLog

    flags = {f["key"]: f for f in lab.get("/api/v1/organizations/current/features").json()}
    assert flags["enterprise_sso"]["enabled"] is False and flags["enterprise_sso"]["source"] == "default"
    r = lab.ws.request("PUT", "/api/v1/organizations/current/features/enterprise_sso", json={"enabled": True})
    assert r.status_code == 200, r.text
    assert r.json() == {"key": "enterprise_sso", "enabled": True, "default": False, "source": "organization"}
    r = lab.ws.request("PUT", "/api/v1/organizations/current/features/enterprise_sso", json={"enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False
    unknown = lab.ws.request("PUT", "/api/v1/organizations/current/features/not_a_flag", json={"enabled": True})
    assert unknown.status_code == 404
    with lab.db() as db:
        actions = db.query(AuditLog).filter(AuditLog.action == "FEATURE_FLAG_CHANGED").count()
    assert actions == 2


# --- workspaces ------------------------------------------------------------------------------------
def test_workspace_crud_members_and_archive(client, lab):
    created = lab.post("/api/v1/workspaces", json={"name": "Protein Folding", "description": "d"})
    assert created.status_code == 201, created.text
    ws = created.json()
    assert ws["slug"] == "protein-folding"
    again = lab.post("/api/v1/workspaces", json={"name": "Protein Folding"})
    assert again.status_code == 201 and again.json()["slug"] == "protein-folding-2"
    dup = lab.post("/api/v1/workspaces", json={"name": "X", "slug": "protein-folding"})
    assert dup.status_code == 409
    assert lab.post("/api/v1/workspaces", json={"name": "X", "slug": "Bad Slug!"}).status_code == 422

    listed = lab.get("/api/v1/workspaces", params={"q": "protein"}).json()
    assert listed["meta"]["total"] == 2

    patched = lab.patch(f"/api/v1/workspaces/{ws['id']}", json={"name": "Folding", "settings": {"color": "blue"}})
    assert patched.status_code == 200 and patched.json()["name"] == "Folding"

    members = lab.get(f"/api/v1/workspaces/{ws['id']}/members").json()
    assert [m["user_id"] for m in members] == [str(lab.user_id)]

    researcher = add_member(client, lab, "researcher")
    added = lab.post(
        f"/api/v1/workspaces/{ws['id']}/members", json={"user_id": researcher.user_id, "role": "researcher"}
    )
    assert added.status_code == 201, added.text
    assert (
        lab.post(
            f"/api/v1/workspaces/{ws['id']}/members", json={"user_id": researcher.user_id, "role": "researcher"}
        ).status_code
        == 409
    )
    outsider = signup(client, org="Outsider")
    not_member = lab.post(f"/api/v1/workspaces/{ws['id']}/members", json={"user_id": outsider.user_id})
    assert not_member.status_code == 404
    owner_role = lab.post(f"/api/v1/workspaces/{ws['id']}/members", json={"user_id": outsider.user_id, "role": "owner"})
    assert owner_role.status_code == 422
    # A researcher cannot manage workspaces.
    assert researcher.post(f"/api/v1/workspaces/{ws['id']}/archive").status_code == 403
    assert lab.delete(f"/api/v1/workspaces/{ws['id']}/members/{researcher.user_id}").status_code == 200

    project = lab.post("/api/v1/projects", json={"workspace_id": ws["id"], "name": "P1"}).json()
    archived = lab.post(f"/api/v1/workspaces/{ws['id']}/archive")
    assert archived.status_code == 200 and archived.json()["archived_at"] is not None
    assert lab.post(f"/api/v1/workspaces/{ws['id']}/archive").status_code == 409
    assert lab.patch(f"/api/v1/workspaces/{ws['id']}", json={"name": "Y"}).status_code == 409
    assert lab.get(f"/api/v1/projects/{project['id']}").json()["archived_at"] is not None
    assert lab.post("/api/v1/projects", json={"workspace_id": ws["id"], "name": "P2"}).status_code == 409
    assert all(w["id"] != ws["id"] for w in lab.get("/api/v1/workspaces").json()["items"])
    assert any(
        w["id"] == ws["id"] for w in lab.get("/api/v1/workspaces", params={"include_archived": True}).json()["items"]
    )


# --- projects --------------------------------------------------------------------------------------
def test_project_create_slug_budget_autonomy(lab):
    wid = str(lab.workspace_id)
    created = lab.post(
        "/api/v1/projects",
        json={
            "workspace_id": wid,
            "name": "Sparse Attention",
            "visibility": "restricted",
            "max_autonomy_level": "L2_AUTOMATED_EXPERIMENT_DESIGN",
            "budget": {"max_total_cost_usd": "125.50", "max_llm_cost_usd": 20},
        },
    )
    assert created.status_code == 201, created.text
    project = created.json()
    assert project["slug"] == "sparse-attention"
    assert project["budget"] == {"max_total_cost_usd": 125.5, "max_llm_cost_usd": 20.0}
    assert lab.post("/api/v1/projects", json={"workspace_id": wid, "name": "Sparse Attention"}).json()["slug"] == (
        "sparse-attention-2"
    )
    members = lab.get(f"/api/v1/projects/{project['id']}/members").json()
    assert [(m["user_id"], m["role"]) for m in members] == [(str(lab.user_id), "research_lead")]

    detail = lab.get(f"/api/v1/projects/{project['id']}").json()
    assert "experiment:execute" in detail["effective_permissions"]

    invalid = [
        {"workspace_id": wid, "name": "X", "budget": {"max_total_cost_usd": -1}},
        {"workspace_id": wid, "name": "X", "budget": {"unknown": 1}},
        {"workspace_id": wid, "name": "X", "visibility": "public"},
        {"workspace_id": wid, "name": "X", "max_autonomy_level": "L5_LONG_HORIZON_AUTONOMOUS_RND"},  # > org max
        {"workspace_id": wid, "name": ""},
    ]
    for payload in invalid:
        assert lab.post("/api/v1/projects", json=payload).status_code == 422, payload
    assert lab.post("/api/v1/projects", json={"workspace_id": str(uuid.uuid4()), "name": "X"}).status_code == 404

    patched = lab.patch(f"/api/v1/projects/{project['id']}", json={"description": "new", "visibility": "organization"})
    assert patched.status_code == 200 and patched.json()["visibility"] == "organization"
    archived = lab.post(f"/api/v1/projects/{project['id']}/archive")
    assert archived.status_code == 200
    assert lab.patch(f"/api/v1/projects/{project['id']}", json={"description": "x"}).status_code == 409
    listed = lab.get("/api/v1/projects", params={"workspace_id": wid}).json()["items"]
    assert project["id"] not in {p["id"] for p in listed}


def test_restricted_project_hidden_until_membership(client, lab):
    restricted = lab.post(
        "/api/v1/projects", json={"workspace_id": str(lab.workspace_id), "name": "Secret", "visibility": "restricted"}
    ).json()
    researcher = add_member(client, lab, "researcher")

    assert researcher.get(f"/api/v1/projects/{restricted['id']}").status_code == 404
    ids = {p["id"] for p in researcher.get("/api/v1/projects").json()["items"]}
    assert restricted["id"] not in ids and str(lab.project_id) in ids

    added = lab.post(
        f"/api/v1/projects/{restricted['id']}/members", json={"user_id": researcher.user_id, "role": "researcher"}
    )
    assert added.status_code == 201, added.text
    assert researcher.get(f"/api/v1/projects/{restricted['id']}").status_code == 200
    assert restricted["id"] in {p["id"] for p in researcher.get("/api/v1/projects").json()["items"]}


def test_project_role_grants_permission_in_that_project_only(client, lab):
    researcher = add_member(client, lab, "researcher")
    other = lab.post("/api/v1/projects", json={"workspace_id": str(lab.workspace_id), "name": "Other"}).json()

    # Org researchers lack project:manage anywhere…
    assert researcher.patch(f"/api/v1/projects/{lab.project_id}", json={"description": "x"}).status_code == 403
    member = lab.post(
        f"/api/v1/projects/{lab.project_id}/members", json={"user_id": researcher.user_id, "role": "research_lead"}
    )
    assert member.status_code == 201, member.text
    # …until a project role grants it — in that project only.
    assert researcher.patch(f"/api/v1/projects/{lab.project_id}", json={"description": "ok"}).status_code == 200
    assert researcher.patch(f"/api/v1/projects/{other['id']}", json={"description": "no"}).status_code == 403
    detail = researcher.get(f"/api/v1/projects/{lab.project_id}").json()
    assert "project:manage" in detail["effective_permissions"]
    assert "project:manage" not in researcher.get(f"/api/v1/projects/{other['id']}").json()["effective_permissions"]

    # A project-level lead cannot grant roles above its own permissions (e.g. admin).
    viewer = add_member(client, lab, "viewer")
    escalate = researcher.post(
        f"/api/v1/projects/{lab.project_id}/members", json={"user_id": viewer.user_id, "role": "admin"}
    )
    assert escalate.status_code == 403
    ok = researcher.post(
        f"/api/v1/projects/{lab.project_id}/members", json={"user_id": viewer.user_id, "role": "researcher"}
    )
    assert ok.status_code == 201, ok.text


def test_project_members_validation_update_and_remove(client, lab):
    from aegis_api.models import AuditLog

    path = f"/api/v1/projects/{lab.project_id}/members"
    researcher = add_member(client, lab, "researcher")
    team = lab.post("/api/v1/teams", json={"name": "Vision"}).json()

    assert lab.post(path, json={"role": "researcher"}).status_code == 422  # neither subject
    both = {"user_id": researcher.user_id, "team_id": team["id"], "role": "researcher"}
    assert lab.post(path, json=both).status_code == 422
    assert lab.post(path, json={"user_id": researcher.user_id, "role": "owner"}).status_code == 422
    assert lab.post(path, json={"user_id": researcher.user_id, "role": "wizard"}).status_code == 422
    assert lab.post(path, json={"team_id": str(uuid.uuid4()), "role": "viewer"}).status_code == 404

    by_user = lab.post(path, json={"user_id": researcher.user_id, "role": "researcher"})
    assert by_user.status_code == 201
    assert lab.post(path, json={"user_id": researcher.user_id, "role": "viewer"}).status_code == 409
    by_team = lab.post(path, json={"team_id": team["id"], "role": "reviewer"})
    assert by_team.status_code == 201 and by_team.json()["team_name"] == "Vision"

    member_id = by_user.json()["id"]
    changed = lab.patch(f"{path}/{member_id}", json={"role": "scientist_operator"})
    assert changed.status_code == 200 and changed.json()["role"] == "scientist_operator"
    assert lab.delete(f"{path}/{member_id}").status_code == 200
    assert lab.delete(f"{path}/{member_id}").status_code == 404
    with lab.db() as db:
        changes = db.query(AuditLog).filter(AuditLog.action == "PROJECT_MEMBER_CHANGED").count()
    assert changes == 4


def test_default_project(lab):
    from aegis_api.lab.identity.service import get_default_project

    with lab.db() as db:
        project = get_default_project(db, lab.actor())
    assert project is not None and project.id == lab.project_id


# --- teams -----------------------------------------------------------------------------------------
def test_teams_crud_and_members(client, lab):
    created = lab.post("/api/v1/teams", json={"name": "Genomics", "description": "g"})
    assert created.status_code == 201, created.text
    team = created.json()
    assert lab.post("/api/v1/teams", json={"name": "Genomics"}).status_code == 409
    researcher = add_member(client, lab, "researcher")

    added = lab.post(f"/api/v1/teams/{team['id']}/members", json={"user_id": researcher.user_id})
    assert added.status_code == 201
    assert lab.post(f"/api/v1/teams/{team['id']}/members", json={"user_id": researcher.user_id}).status_code == 409
    assert lab.get(f"/api/v1/teams/{team['id']}").json()["member_count"] == 1
    assert [m["user_id"] for m in lab.get(f"/api/v1/teams/{team['id']}/members").json()] == [researcher.user_id]

    # Team membership grants project roles (and visibility of restricted projects).
    restricted = lab.post(
        "/api/v1/projects",
        json={"workspace_id": str(lab.workspace_id), "name": "Team Only", "visibility": "restricted"},
    ).json()
    assert researcher.get(f"/api/v1/projects/{restricted['id']}").status_code == 404
    lab.post(f"/api/v1/projects/{restricted['id']}/members", json={"team_id": team["id"], "role": "researcher"})
    assert researcher.get(f"/api/v1/projects/{restricted['id']}").status_code == 200

    # Researchers cannot manage teams.
    assert researcher.post("/api/v1/teams", json={"name": "Mine"}).status_code == 403
    renamed = lab.patch(f"/api/v1/teams/{team['id']}", json={"name": "Genomics Core"})
    assert renamed.status_code == 200 and renamed.json()["name"] == "Genomics Core"
    assert lab.delete(f"/api/v1/teams/{team['id']}/members/{researcher.user_id}").status_code == 200
    assert researcher.get(f"/api/v1/projects/{restricted['id']}").status_code == 404
    assert lab.delete(f"/api/v1/teams/{team['id']}").status_code == 200
    assert lab.get(f"/api/v1/teams/{team['id']}").status_code == 404


def test_team_manager_cannot_escalate_through_team(client, lab):
    lead = add_member(client, lab, "research_lead")
    team = lab.post("/api/v1/teams", json={"name": "Admins"}).json()
    lab.post(f"/api/v1/projects/{lab.project_id}/members", json={"team_id": team["id"], "role": "admin"})
    # The research lead holds team:manage but could not grant "admin" in the project, so it may not join.
    r = lead.post(f"/api/v1/teams/{team['id']}/members", json={"user_id": lead.user_id})
    assert r.status_code == 403


# --- users & RBAC ------------------------------------------------------------------------------------
def test_users_me_and_list(client, lab):
    me = lab.get("/api/v1/users/me")
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["kind"] == "user" and body["user"]["id"] == str(lab.user_id)
    assert body["role"] == "owner" and body["is_platform_admin"] is False
    assert body["lab_permissions"] == sorted(body["lab_permissions"])
    assert "mission:create" in body["lab_permissions"] and "systems:read" not in body["lab_permissions"]
    assert any(m["organization_id"] == str(lab.org_id) for m in body["memberships"])

    researcher = add_member(client, lab, "researcher")
    users = lab.get("/api/v1/users", params={"page_size": 50}).json()
    assert users["meta"]["total"] == 2
    assert {u["user_id"] for u in users["items"]} == {str(lab.user_id), researcher.user_id}
    only_researchers = lab.get("/api/v1/users", params={"role": "researcher"}).json()
    assert [u["user_id"] for u in only_researchers["items"]] == [researcher.user_id]

    billing = add_member(client, lab, "billing_admin")
    assert billing.get("/api/v1/users").status_code == 200  # holds project:read
    researcher_me = researcher.get("/api/v1/users/me").json()
    assert researcher_me["role"] == "researcher" and "approval:decide" not in researcher_me["lab_permissions"]


def test_roles_and_permissions_catalog(lab):
    roles = {r["key"]: r for r in lab.get("/api/v1/roles").json()}
    assert {"owner", "admin", "research_lead", "researcher", "reviewer", "billing_admin"} <= set(roles)
    assert roles["owner"]["assignable"] is False and roles["researcher"]["assignable"] is True
    assert "approval:decide" in roles["research_lead"]["permissions"]
    perms = {p["key"]: p for p in lab.get("/api/v1/permissions").json()}
    assert perms["mission:create"]["family"] == "lab" and perms["systems:read"]["family"] == "core"
    assert perms["approval:decide"]["human_only"] is True


def test_seed_rbac_catalog_is_idempotent(admin_session):
    from sqlalchemy import func, select

    from aegis_api.lab.identity.service import seed_rbac_catalog
    from aegis_api.lab.models import PermissionRecord, RolePermission, RoleRecord
    from aegis_api.security.rbac import ROLE_PERMISSIONS

    seed_rbac_catalog(admin_session)
    admin_session.commit()
    second = seed_rbac_catalog(admin_session)
    admin_session.commit()
    assert second["added"] == 0 and second["removed"] == 0
    system_roles = admin_session.scalar(select(func.count(RoleRecord.id)).where(RoleRecord.organization_id.is_(None)))
    assert system_roles == len(ROLE_PERMISSIONS)
    lead = admin_session.scalar(select(RoleRecord).where(RoleRecord.key == "research_lead"))
    granted = set(
        admin_session.scalars(
            select(PermissionRecord.key)
            .join(RolePermission, RolePermission.permission_id == PermissionRecord.id)
            .where(RolePermission.role_id == lead.id)
        ).all()
    )
    assert granted == set(ROLE_PERMISSIONS["research_lead"])


# --- tenancy -----------------------------------------------------------------------------------------
def test_cross_tenant_resources_are_404(lab, other_lab):
    team = lab.post("/api/v1/teams", json={"name": "Private"}).json()
    other = other_lab
    assert other.get(f"/api/v1/workspaces/{lab.workspace_id}").status_code == 404
    assert other.patch(f"/api/v1/workspaces/{lab.workspace_id}", json={"name": "x"}).status_code == 404
    assert other.get(f"/api/v1/projects/{lab.project_id}").status_code == 404
    assert other.patch(f"/api/v1/projects/{lab.project_id}", json={"name": "x"}).status_code == 404
    assert other.get(f"/api/v1/projects/{lab.project_id}/members").status_code == 404
    assert other.get(f"/api/v1/teams/{team['id']}").status_code == 404
    assert other.delete(f"/api/v1/teams/{team['id']}").status_code == 404
    assert (
        other.post("/api/v1/projects", json={"workspace_id": str(lab.workspace_id), "name": "Hijack"}).status_code
        == 404
    )
    # Cannot attach another tenant's user or team to one's own project.
    assert (
        other.post(
            f"/api/v1/projects/{other.project_id}/members", json={"user_id": str(lab.user_id), "role": "viewer"}
        ).status_code
        == 404
    )
    assert (
        other.post(
            f"/api/v1/projects/{other.project_id}/members", json={"team_id": team["id"], "role": "viewer"}
        ).status_code
        == 404
    )
    assert str(lab.project_id) not in {p["id"] for p in other.get("/api/v1/projects").json()["items"]}
    assert str(lab.user_id) not in {u["user_id"] for u in other.get("/api/v1/users").json()["items"]}


def test_unauthenticated_requests_rejected(client):
    for path in ("/api/v1/organizations", "/api/v1/projects", "/api/v1/users/me", "/api/v1/teams"):
        assert client.get(path).status_code == 401


def test_project_membership_service_api_for_system_and_agent_actors(client, lab):
    from dataclasses import replace

    from aegis_api.errors import Forbidden
    from aegis_api.lab.core.actor import Actor
    from aegis_api.lab.identity.service import add_project_member

    researcher = add_member(client, lab, "researcher")
    with lab.db() as db:
        member = add_project_member(
            db,
            Actor.system(lab.org_id, label="onboarding"),
            lab.project_id,
            user_id=uuid.UUID(researcher.user_id),
            role="researcher",
        )
        assert member.role == "researcher"
    agent = replace(lab.actor(), kind="agent", label="agent:test")
    with pytest.raises(Forbidden), lab.db() as db:
        add_project_member(db, agent, lab.project_id, user_id=lab.user_id, role="viewer")


def test_create_organization_retries_on_concurrent_slug_collision(lab, monkeypatch):
    from aegis_api.services import auth_service

    taken = lab.ws.data["organization"]["slug"]  # simulate a concurrent signup winning the race for this slug
    real = auth_service.unique_org_slug
    calls: list[str] = []

    def racy(session, base):
        calls.append(base)
        return taken if len(calls) == 1 else real(session, base)

    monkeypatch.setattr(auth_service, "unique_org_slug", racy)
    created = lab.post("/api/v1/organizations", json={"name": "Race Lab"})
    assert created.status_code == 201, created.text
    assert created.json()["slug"] != taken and len(calls) == 2


def test_allow_self_approval_setting(client, lab):
    from aegis_api.models import AuditLog, Organization

    assert lab.get("/api/v1/organizations/current/settings").json()["allow_self_approval"] is False
    r = lab.patch("/api/v1/organizations/current/settings", json={"allow_self_approval": True})
    assert r.status_code == 200, r.text
    assert r.json()["allow_self_approval"] is True
    assert lab.get("/api/v1/organizations/current/settings").json()["allow_self_approval"] is True
    with lab.db() as db:
        assert db.get(Organization, lab.org_id).settings["allow_self_approval"] is True
        entry = (
            db.query(AuditLog)
            .filter(AuditLog.action == "ORG_SETTINGS_CHANGED")
            .order_by(AuditLog.created_at.desc())
            .first()
        )
    assert entry.before == {"allow_self_approval": False} and entry.after == {"allow_self_approval": True}
    researcher = add_member(client, lab, "researcher")
    assert (
        researcher.patch("/api/v1/organizations/current/settings", json={"allow_self_approval": False}).status_code
        == 403
    )
    assert lab.patch("/api/v1/organizations/current/settings", json={"allow_self_approval": "maybe"}).status_code == 422
