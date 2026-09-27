"""Policy compile, framework mapping, search, and API-key auth integration tests."""

from __future__ import annotations

import pytest

from tests.conftest import requires_db

pytestmark = [requires_db, pytest.mark.db]

POLICY_TEXT = """Hiring AI Policy
Section 3.1 Protected attributes such as gender and age must not materially alter candidate scores.
Section 3.2 Final hiring decisions require human review before they are sent.
Section 3.3 The system must not expose candidate personal information.
"""


def test_policy_compile_creates_controls_with_provenance(workspace):
    policy = workspace.post("/api/v1/policies", json={"name": "Hiring", "key": "HR", "source_text": POLICY_TEXT}).json()
    result = workspace.post(f"/api/v1/policies/{policy['id']}/compile").json()
    assert result["report"]["controls_generated"] >= 3
    controls = result["controls"]
    assert any(c["test_type"] == "counterfactual" for c in controls)
    assert any(c["test_type"] == "human_oversight" for c in controls)
    # provenance
    reqs = result["requirements"]
    assert all(r["source_excerpt"] for r in reqs)
    # framework mappings auto-created
    control_id = controls[0]["id"]
    mappings = workspace.get(f"/api/v1/controls/{control_id}/mappings").json()
    assert isinstance(mappings, list)


def test_frameworks_seeded_and_labeled(workspace):
    frameworks = workspace.get("/api/v1/frameworks").json()
    keys = {f["key"] for f in frameworks}
    assert {"nist-ai-rmf", "owasp-llm", "iso-42001"} <= keys
    for f in frameworks:
        if f["kind"] == "reference":
            assert "not constitute legal advice" in (f["disclaimer"] or "").lower()


def test_audit_with_policy_maps_controls(demo_provider_and_system):
    import time

    ws, _pid, sid = demo_provider_and_system
    policy = ws.post("/api/v1/policies", json={"name": "Hiring", "key": "HR", "source_text": POLICY_TEXT}).json()
    compiled = ws.post(f"/api/v1/policies/{policy['id']}/compile").json()
    pv_id = compiled["policy_version_id"]
    aid = ws.post(
        "/api/v1/audits",
        json={
            "system_id": sid,
            "categories": ["fairness", "agent_action", "policy"],
            "policy_version_ids": [pv_id],
            "intensity": "standard",
        },
    ).json()["id"]
    for _ in range(80):
        a = ws.get(f"/api/v1/audits/{aid}").json()
        if a["status"] in ("completed", "partially_completed", "failed"):
            break
        time.sleep(0.5)
    assert a["findings_count"] >= 1
    findings = ws.get("/api/v1/findings").json()["items"]
    assert any(f["control_ref"] for f in findings)


def test_global_search(demo_provider_and_system):
    ws, _pid, _sid = demo_provider_and_system
    results = ws.get("/api/v1/search", params={"q": "Hiring"}).json()
    assert results["total"] >= 1
    assert "systems" in results["groups"]


def test_sdk_against_live_app(demo_provider_and_system):
    """Exercise the Python SDK against the in-process app via a custom transport."""
    import time

    ws, _pid, sid = demo_provider_and_system
    key = ws.post(
        "/api/v1/api-keys", json={"name": "sdk", "role": "auditor", "scopes": ["read", "write", "run"]}
    ).json()["plaintext"]
    from aegis_ai import Aegis

    client = Aegis(api_key=key)
    client._t._client = ws.client  # route through the TestClient transport
    client._t._client.headers.update({"Authorization": f"Bearer {key}"})
    posture = client.overview()
    assert "posture" in posture
    audit = client.audits.create(system_id=sid, categories=["privacy"], intensity="quick")
    for _ in range(60):
        a = client.audits.get(audit["id"])
        if a["status"] in ("completed", "partially_completed", "failed"):
            break
        time.sleep(0.5)
    assert a["status"] in ("completed", "partially_completed")
