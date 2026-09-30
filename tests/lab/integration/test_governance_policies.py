"""Governance policies: CRUD, immutable versions, evaluation (enrichment, scoping, denials) and the API."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select, text

from tests.conftest import requires_db

pytestmark = [requires_db, pytest.mark.db]

GPU_DENY_RULE = {
    "id": "org.no-gpu",
    "action": "execution.*",
    "when": {"field": "gpu_count", "op": "gt", "value": 0},
    "effect": "deny",
    "reason": "GPU jobs are disabled in this organization",
}


def api_key(lab, role: str, scopes: list[str]) -> dict[str, str]:  # type: ignore[no-untyped-def]
    r = lab.post("/api/v1/api-keys", json={"name": f"k-{uuid.uuid4().hex[:6]}", "role": role, "scopes": scopes})
    assert r.status_code in (200, 201), r.text
    return {"Authorization": f"Bearer {r.json()['plaintext']}"}


def create_policy(lab, **overrides: Any) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    body = {"key": f"p-{uuid.uuid4().hex[:8]}", "name": "No GPU", "rules": [GPU_DENY_RULE], **overrides}
    r = lab.post("/api/v1/governance/policies", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def make_mission(lab, **fields: Any) -> uuid.UUID:  # type: ignore[no-untyped-def]
    from aegis_api.lab.models import Mission

    with lab.db() as db:
        mission = Mission(
            organization_id=lab.org_id,
            workspace_id=lab.workspace_id,
            project_id=lab.project_id,
            title="Mission",
            objective="Improve the benchmark",
            **fields,
        )
        db.add(mission)
        db.flush()
        return mission.id


# ---------------------------------------------------------------------------------------------
# CRUD & versions
# ---------------------------------------------------------------------------------------------
def test_policy_crud_and_versions(lab):  # type: ignore[no-untyped-def]
    created = create_policy(lab, description="Keep costs down", change_note="initial")
    assert created["current_version"] == 1 and created["status"] == "active"
    assert created["rules"][0]["id"] == "org.no-gpu" and len(created["content_hash"]) == 64
    pid = created["id"]

    listed = lab.get("/api/v1/governance/policies").json()
    assert listed["meta"]["total"] == 1 and listed["items"][0]["current_version"] == 1

    new_rules = [
        GPU_DENY_RULE,
        {**GPU_DENY_RULE, "id": "org.no-network", "when": {"field": "network_mode", "op": "ne", "value": "none"}},
    ]
    r = lab.post(f"/api/v1/governance/policies/{pid}/versions", json={"rules": new_rules, "change_note": "network"})
    assert r.status_code == 201, r.text
    assert r.json()["version"] == 2 and r.json()["content_hash"] != created["content_hash"]

    detail = lab.get(f"/api/v1/governance/policies/{pid}").json()
    assert detail["current_version"] == 2 and len(detail["rules"]) == 2
    versions = lab.get(f"/api/v1/governance/policies/{pid}/versions").json()
    assert [v["version"] for v in versions["items"]] == [2, 1]

    r = lab.patch(f"/api/v1/governance/policies/{pid}", json={"status": "disabled"})
    assert r.status_code == 200 and r.json()["status"] == "disabled"
    assert lab.get("/api/v1/governance/policies?status=active").json()["meta"]["total"] == 0

    with lab.db() as db:
        from aegis_api.models import AuditLog

        actions = db.scalars(
            select(AuditLog.after).where(AuditLog.action == "POLICY_CHANGED", AuditLog.resource_id == pid)
        ).all()
    assert {a["operation"] for a in actions} == {"created", "version_created", "status_changed"}


def test_policy_versions_are_immutable(lab):  # type: ignore[no-untyped-def]
    from sqlalchemy.exc import DBAPIError

    create_policy(lab)
    with pytest.raises(DBAPIError), lab.db() as db:
        db.execute(text("update governance_policy_versions set rules = '[]'::jsonb"))
    with pytest.raises(DBAPIError), lab.db() as db:
        db.execute(text("delete from governance_policy_versions"))


def test_policy_validation_and_conflicts(lab):  # type: ignore[no-untyped-def]
    bad = {
        "key": "bad-policy",
        "name": "Bad",
        "rules": [{**GPU_DENY_RULE, "when": {"field": "gpu_count", "op": "bogus"}}],
    }
    r = lab.post("/api/v1/governance/policies", json=bad)
    assert r.status_code == 422
    errors = r.json()["error"]["details"]["errors"]
    assert any("rule #1 ('org.no-gpu'): when.op" in e for e in errors), errors

    reserved = {
        "key": "reserved",
        "name": "R",
        "rules": [{**GPU_DENY_RULE, "id": "baseline.override", "effect": "allow"}],
    }
    r = lab.post("/api/v1/governance/policies", json=reserved)
    assert r.status_code == 422 and "reserved" in str(r.json())

    created = create_policy(lab, key="dup-key")
    r = lab.post("/api/v1/governance/policies", json={"key": "dup-key", "name": "Again", "rules": []})
    assert r.status_code == 409 and r.json()["error"]["code"] == "policy_key_exists"
    r = lab.post(f"/api/v1/governance/policies/{created['id']}/versions", json={"rules": [{"id": "x"}]})
    assert r.status_code == 422
    r = lab.patch(f"/api/v1/governance/policies/{created['id']}", json={"status": "deleted"})
    assert r.status_code == 422


def test_policy_permissions_and_tenancy(lab, other_lab, client):  # type: ignore[no-untyped-def]
    created = create_policy(lab)
    viewer = api_key(lab, "viewer", ["read"])
    assert client.get("/api/v1/governance/baseline", headers=viewer).status_code == 200
    assert client.get("/api/v1/governance/policies", headers=viewer).status_code == 200
    admin_key = api_key(lab, "admin", ["read", "write"])
    r = client.post("/api/v1/governance/policies", headers=admin_key, json={"key": "k-x", "name": "x", "rules": []})
    assert r.status_code == 403  # admin:policy is human-only
    assert other_lab.get(f"/api/v1/governance/policies/{created['id']}").status_code == 404
    r = other_lab.post(f"/api/v1/governance/policies/{created['id']}/versions", json={"rules": []})
    assert r.status_code == 404


def test_baseline_endpoint(lab):  # type: ignore[no-untyped-def]
    body = lab.get("/api/v1/governance/baseline").json()
    assert body["version"] == "baseline-2026.09"
    assert body["default_effects"]["strategy.auto_promote"] == "deny"
    assert body["default_effects"]["discovery.publish"] == "require_approval"
    assert all(rule["id"].startswith("baseline.") for rule in body["rules"])
    assert "gpu_count" in body["context_keys"]


# ---------------------------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------------------------
def test_evaluate_endpoint_dry_run(lab):  # type: ignore[no-untyped-def]
    r = lab.post("/api/v1/governance/evaluate", json={"action": "execution.submit", "context": {"gpu_count": 1}})
    assert r.status_code == 200
    body = r.json()
    # owner user without a mission: missing autonomy counts as L0 → GPU needs approval
    assert body["effect"] == "require_approval"
    assert "baseline.execution.submit.gpu_requires_approval" in body["matched_rules"]
    assert body["context"]["actor_kind"] == "user" and body["context"]["is_human"] is True
    assert ".wikipedia.org" in body["context"]["org_egress_allowlist"]

    create_policy(lab)
    body = lab.post(
        "/api/v1/governance/evaluate", json={"action": "execution.submit", "context": {"gpu_count": 1}}
    ).json()
    assert body["effect"] == "deny" and "org.no-gpu" in body["matched_rules"]
    assert any(v.startswith("baseline@") for v in body["policy_versions"])

    # dry-run simulation may override derived facts
    body = lab.post(
        "/api/v1/governance/evaluate",
        json={"action": "discovery.approve", "context": {"actor_kind": "agent", "is_human": False}},
    ).json()
    assert body["effect"] == "deny"

    r = lab.post("/api/v1/governance/evaluate", json={"action": "Not An Action", "context": {}})
    assert r.status_code == 422
    r = lab.post("/api/v1/governance/evaluate", json={"action": "tool.invoke", "mission_id": str(uuid.uuid4())})
    assert r.status_code == 404

    # dry runs have no side effects
    with lab.db() as db:
        from aegis_api.lab.models import LabEvent

        assert db.scalar(select(LabEvent.id).where(LabEvent.type == "POLICY_DENIED")) is None


def test_evaluate_policy_denial_is_recorded_and_survives_rollback(lab):  # type: ignore[no-untyped-def]
    from aegis_api.lab.core.errors import PolicyDenied
    from aegis_api.lab.governance.policies import evaluate_policy
    from aegis_api.lab.models import LabEvent
    from aegis_api.models import AuditLog

    agent = lab.actor(role="owner").for_agent(
        agent_run_id=uuid.uuid4(),
        agent_role="CodingAgent",
        agent_permissions=frozenset({"tool:invoke"}),
        autonomy_level="L3_AUTOMATED_EXECUTION",
    )
    with pytest.raises(PolicyDenied), lab.db() as db:
        decision = evaluate_policy(db, agent, "mcp.invoke", {"tool_name": "evil", "mcp_registered": False})
        assert decision.denied
        raise PolicyDenied("; ".join(decision.reasons))  # caller aborts → transaction rolls back
    with lab.db() as db:
        events = db.scalars(select(LabEvent).where(LabEvent.type == "POLICY_DENIED")).all()
        audits = db.scalars(select(AuditLog).where(AuditLog.action == "POLICY_DENIED")).all()
    assert len(events) == 1 and events[0].payload["action"] == "mcp.invoke"
    assert "baseline.mcp.invoke.unregistered" in events[0].payload["matched_rules"]
    assert events[0].payload["context"]["tool_name"] == "evil"
    assert len(audits) == 1 and audits[0].actor_type == "agent"


def test_authoritative_context_cannot_be_spoofed(lab):  # type: ignore[no-untyped-def]
    from aegis_api.lab.governance.policies import evaluate_policy

    agent = lab.actor().for_agent(
        agent_run_id=uuid.uuid4(), agent_role="VerifierAgent", agent_permissions=frozenset(), autonomy_level="L5"
    )
    with lab.db() as db:
        decision = evaluate_policy(db, agent, "discovery.approve", {"is_human": True, "actor_kind": "user"})
        assert decision.denied
        decision = evaluate_policy(db, agent, "mission.autonomy_change", {"requested_autonomy_level": "L1"})
        assert decision.denied and "baseline.mission.autonomy_change.non_human" in decision.matched_rules


def test_autonomy_is_clamped_to_ceilings_and_mission_policy_applies(lab):  # type: ignore[no-untyped-def]
    from aegis_api.lab.governance.policies import evaluate_policy
    from aegis_api.lab.models import Mission, Project

    mission_id = make_mission(
        lab,
        autonomy_level="L4_CLOSED_LOOP_EVOLUTION",
        approval_policy={
            "require_approval_for": ["tool.invoke", "research.*", "bogus.action"],
            "approver_permission": "approval:decide",
        },
    )
    user = lab.actor()
    with lab.db() as db:
        mission = db.get(Mission, mission_id)
        # org default ceiling is L3 → an agent of this L4 mission is evaluated at L3 → promotion denied
        agent = user.for_agent(
            agent_run_id=uuid.uuid4(), agent_role="EvolutionAgent", agent_permissions=frozenset(), autonomy_level=None
        )
        decision = evaluate_policy(db, agent, "strategy.promote", {}, mission=mission)
        assert decision.denied and "baseline.strategy.promote.automation_below_l4" in decision.matched_rules
        decision = evaluate_policy(db, user, "tool.invoke", {"tool_risk": "LOW"}, mission=mission)
        assert decision.requires_approval and decision.approver_permission == "approval:decide"
        assert any(v.startswith(f"mission:{mission_id}@") for v in decision.policy_versions)
        decision = evaluate_policy(db, user, "research.deep_research", {"estimated_cost_usd": 1}, mission=mission)
        assert decision.requires_approval and "mission.require_approval.research.all" in decision.matched_rules
        from aegis_api.errors import ValidationFailed

        with pytest.raises(ValidationFailed):
            evaluate_policy(db, user, "tool.invoke", {}, mission=mission, project_id=uuid.uuid4())
        # project ceiling lowers it further
        db.get(Project, lab.project_id).max_autonomy_level = "L1_RESEARCH_AUTOMATION"
        db.flush()
        decision = evaluate_policy(db, agent, "experiment.execute", {"estimated_cost_usd": 0}, mission=mission)
        assert "baseline.experiment.execute.automation_below_l3" in decision.matched_rules
        decision = evaluate_policy(
            db, user, "mission.autonomy_change", {"requested_autonomy_level": "L2"}, mission=mission
        )
        assert decision.denied and "baseline.mission.autonomy_change.above_ceiling" in decision.matched_rules


def test_project_scoped_and_disabled_policies(lab):  # type: ignore[no-untyped-def]
    from aegis_api.lab.governance.policies import evaluate_policy
    from aegis_api.lab.models import Project

    with lab.db() as db:
        other = Project(
            organization_id=lab.org_id, workspace_id=lab.workspace_id, name="Other", slug=f"o-{uuid.uuid4().hex[:6]}"
        )
        db.add(other)
        db.flush()
        other_id = other.id
    scoped = create_policy(lab, project_id=str(lab.project_id))
    assert scoped["project_id"] == str(lab.project_id)
    user = lab.actor()
    ctx = {"gpu_count": 1, "autonomy_level": "L3"}
    with lab.db() as db:
        assert evaluate_policy(db, user, "execution.submit", ctx, project_id=lab.project_id).denied
        assert not evaluate_policy(db, user, "execution.submit", ctx, project_id=other_id).denied
        assert not evaluate_policy(db, user, "execution.submit", ctx).denied
    lab.patch(f"/api/v1/governance/policies/{scoped['id']}", json={"status": "disabled"})
    with lab.db() as db:
        assert not evaluate_policy(db, user, "execution.submit", ctx, project_id=lab.project_id).denied


def test_consent_and_egress_enrichment(lab):  # type: ignore[no-untyped-def]
    from aegis_api.lab.core.org_settings import get_org_settings
    from aegis_api.lab.governance.policies import evaluate_policy

    user = lab.actor()
    with lab.db() as db:
        assert evaluate_policy(db, user, "llm.external_processing", {"external_provider": "gemini"}).denied
        settings = get_org_settings(db, lab.org_id)
        settings.data_processing = {"allow_external_llm": True}
        settings.egress_allowlist = ["data.example.org"]
        db.flush()
        assert evaluate_policy(db, user, "llm.external_processing", {"external_provider": "gemini"}).allowed
        ctx = {"autonomy_level": "L3", "network_mode": "allowlist", "egress_hosts": ["data.example.org"]}
        assert evaluate_policy(db, user, "execution.submit", ctx).requires_approval
        ctx["egress_hosts"] = ["exfil.example.net"]
        assert evaluate_policy(db, user, "execution.submit", ctx).denied
