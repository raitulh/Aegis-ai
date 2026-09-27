"""End-to-end audit flow through the API (create system → audit → findings → evidence → report → retest)."""

from __future__ import annotations

import time

import pytest

from tests.conftest import requires_db

pytestmark = [requires_db, pytest.mark.db]


def _wait(ws, audit_id, timeout=60):
    terminal = {"completed", "partially_completed", "failed", "cancelled"}
    for _ in range(timeout * 2):
        a = ws.get(f"/api/v1/audits/{audit_id}").json()
        if a["status"] in terminal:
            return a
        time.sleep(0.5)
    raise AssertionError("audit did not finish")


def test_full_audit_lifecycle(demo_provider_and_system):
    ws, _pid, sid = demo_provider_and_system
    r = ws.post(
        "/api/v1/audits",
        json={
            "system_id": sid,
            "categories": ["fairness", "privacy", "agent_action"],
            "intensity": "standard",
            "config": {"seed": 7},
        },
    )
    assert r.status_code == 201, r.text
    audit = _wait(ws, r.json()["id"])
    assert audit["status"] in ("completed", "partially_completed")
    assert audit["test_count"] > 0
    assert audit["evidence_count"] > 0
    assert audit["findings_count"] >= 1
    assert "fairness" in audit["summary"]["dimensions"]

    findings = ws.get("/api/v1/findings").json()
    assert findings["meta"]["total"] >= 1
    fairness = next((f for f in findings["items"] if f["category"] == "fairness"), None)
    assert fairness is not None
    assert fairness["risk_level"] in ("high", "critical")

    detail = ws.get(f"/api/v1/findings/{fairness['id']}").json()
    assert detail["risk_reasons"] and detail["risk_factors"]
    evidence = ws.get(f"/api/v1/findings/{fairness['id']}/evidence").json()
    assert len(evidence) >= 1
    assert all("chain_hash" in e for e in evidence)

    report = ws.get(f"/api/v1/audits/{audit['id']}/report").json()
    assert report["content"]["detailed_findings"]
    assert "limitations" in report["content"]


def test_remediation_and_retest_measures_improvement(demo_provider_and_system):
    ws, _pid, sid = demo_provider_and_system
    audit_id = ws.post(
        "/api/v1/audits",
        json={"system_id": sid, "categories": ["fairness"], "intensity": "standard", "config": {"seed": 7}},
    ).json()["id"]
    _wait(ws, audit_id)
    finding = ws.get("/api/v1/findings", params={"category": "fairness"}).json()["items"][0]

    rec = ws.get(f"/api/v1/findings/{finding['id']}/recommendation").json()
    assert rec["change"], "a recommended config change is expected"
    remediation = ws.post(
        f"/api/v1/findings/{finding['id']}/remediations",
        json={
            "category": rec["category"],
            "title": rec["title"],
            "description": rec["description"],
            "change": rec["change"],
        },
    ).json()
    ws.post(f"/api/v1/remediations/{remediation['id']}/apply")
    run = ws.post(f"/api/v1/findings/{finding['id']}/retest").json()
    # Poll the regression run
    for _ in range(60):
        rr = ws.get(f"/api/v1/regression-runs/{run['id']}").json()
        if rr["status"] in ("completed", "failed"):
            break
        time.sleep(0.5)
    assert rr["verdict"] == "pass", rr
    assert any(r["improved"] for r in rr["results"])


def test_sse_stream_emits_events(demo_provider_and_system):
    ws, _pid, sid = demo_provider_and_system
    audit_id = ws.post(
        "/api/v1/audits", json={"system_id": sid, "categories": ["privacy"], "intensity": "quick"}
    ).json()["id"]
    _wait(ws, audit_id)
    events = ws.get(f"/api/v1/audits/{audit_id}/events").json()
    types = {e["type"] for e in events}
    assert "audit.started" in types
    assert any(t in types for t in ("audit.completed",))
    assert all(e["seq"] > 0 for e in events)
