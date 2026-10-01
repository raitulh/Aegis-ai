"""Platform capabilities: evidence packages, runtime guard, policy studio, finding lifecycle, continuous
assurance, entitlements/usage, billing webhooks, outbound webhooks, graph and tenant isolation."""

from __future__ import annotations

import hashlib
import hmac
import http.server
import io
import json
import threading
import time
import uuid
import zipfile
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from tests.conftest import requires_db, signup

pytestmark = [requires_db, pytest.mark.db]
TERMINAL = {"completed", "partially_completed", "failed", "cancelled"}


def _wait(ws, audit_id: str, timeout: float = 60) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        audit = ws.get(f"/api/v1/audits/{audit_id}").json()
        if audit["status"] in TERMINAL:
            return audit
        time.sleep(0.3)
    raise AssertionError("audit did not finish")


def _set_plan(org_id: str, plan: str) -> None:
    from aegis_api.db.session import admin_session_scope

    with admin_session_scope() as s:
        s.execute(text("update organizations set plan = :p where id = :id"), {"p": plan, "id": org_id})


def _audit(ws, sid: str, categories=("privacy",)) -> dict:
    r = ws.post(
        "/api/v1/audits",
        json={"system_id": sid, "categories": list(categories), "intensity": "quick", "config": {"seed": 7}},
    )
    assert r.status_code == 201, r.text
    return _wait(ws, r.json()["id"])


# --- evidence ---------------------------------------------------------------------------------------
def test_evidence_verify_export_and_tamper_detection(demo_provider_and_system):
    ws, _pid, sid = demo_provider_and_system
    audit = _audit(ws, sid, ("privacy", "fairness"))
    verdict = ws.get(f"/api/v1/audits/{audit['id']}/evidence/verify").json()
    assert verdict["status"] == "VERIFIED", verdict
    assert verdict["records"] == audit["evidence_count"] > 0
    assert verdict["head"] == verdict["recorded_head"]

    r = ws.post(f"/api/v1/audits/{audit['id']}/evidence/export")
    assert r.status_code == 200, r.text
    package = r.content
    key = ws.get("/api/v1/evidence/signing-key").json()
    from engines.evidence.package import verify_package

    report = verify_package(package, key["public_key"])
    assert report["status"] == "VERIFIED", report
    assert report["signature"]["valid"] is True
    assert report["root_hash"] == r.headers["x-aegis-root-hash"]

    # Tamper: edit one artifact's content inside the package.
    src = zipfile.ZipFile(io.BytesIO(package))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as dst:
        for name in src.namelist():
            data = src.read(name)
            if "/evidence/" in name and name.endswith("00001.json"):
                art = json.loads(data)
                art["content"] = {"tampered": True}
                data = json.dumps(art).encode()
            dst.writestr(name, data)
    assert verify_package(out.getvalue(), key["public_key"])["status"] == "TAMPERED"

    # Missing file → INCOMPLETE; foreign key → TAMPERED.
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as dst:
        for name in src.namelist():
            if not name.endswith("findings.json"):
                dst.writestr(name, src.read(name))
    assert verify_package(out.getvalue())["status"] in ("INCOMPLETE", "TAMPERED")
    import base64
    import os

    other_key = base64.urlsafe_b64encode(os.urandom(32)).decode()
    assert verify_package(package, other_key)["status"] == "TAMPERED"

    # Server-side verification of an uploaded package, export history, integrity summary, audit log.
    uploaded = ws.post("/api/v1/evidence/verify-package", files={"file": ("p.zip", package, "application/zip")})
    assert uploaded.status_code == 200 and uploaded.json()["status"] == "VERIFIED"
    exports = ws.get("/api/v1/evidence/exports").json()
    assert exports["meta"]["total"] >= 1
    summary = ws.get("/api/v1/evidence/integrity").json()
    assert summary["status"] == "VERIFIED" and summary["audits_verified"] >= 1
    log = ws.get("/api/v1/audit-log").json()["items"]
    assert any(e["action"] == "evidence.exported" for e in log)


def test_evidence_tampering_in_database_is_detected(demo_provider_and_system):
    ws, _pid, sid = demo_provider_and_system
    audit = _audit(ws, sid)
    from aegis_api.db.session import admin_session_scope

    # Simulate an attacker with owner access bypassing the trigger to rewrite content.
    with admin_session_scope() as s:
        s.execute(text("alter table evidence disable trigger evidence_immutable"))
        s.execute(
            text("update evidence set content = '{\"forged\": true}'::jsonb where audit_id = :a and seq = 1"),
            {"a": audit["id"]},
        )
        s.execute(text("alter table evidence enable trigger evidence_immutable"))
    verdict = ws.get(f"/api/v1/audits/{audit['id']}/evidence/verify").json()
    assert verdict["status"] == "TAMPERED"
    assert any(p["check"] == "content_hash" for p in verdict["problems"])


# --- runtime guard + policy studio ----------------------------------------------------------------------
def _publish_template(ws, template: str, scope: dict | None = None) -> dict:
    policy = ws.post("/api/v1/runtime-policies", json={"template_key": template}).json()
    assert "id" in policy, policy
    published = ws.post(f"/api/v1/runtime-policies/{policy['id']}/publish", json={"version": 1})
    assert published.status_code == 200, published.text
    assign = ws.post(
        f"/api/v1/runtime-policies/{policy['id']}/assignments", json=scope or {"scope_type": "organization"}
    )
    assert assign.status_code == 201, assign.text
    return policy


def _exfil_event(sid: str, event_id: str | None = None) -> dict:
    return {
        "event_id": event_id or uuid.uuid4().hex,
        "event_type": "network.request",
        "system_id": sid,
        "agent": "billing-agent",
        "session_id": "sess-1",
        "trace_id": "trace-1",
        "payload": {
            "url": "https://upload.partner.example/files",
            "method": "POST",
            "data_classification": "confidential",
            "body": "customer list for jane.doe@example.com",
        },
    }


def test_runtime_observe_audit_and_enforce_modes(demo_provider_and_system):
    ws, _pid, sid = demo_provider_and_system
    _publish_template(ws, "prevent-sensitive-exfiltration")

    # Observe (default): decision recorded, never enforced, no finding.
    r = ws.post("/api/v1/runtime/events", json={"events": [_exfil_event(sid)]})
    assert r.status_code == 202, r.text
    decision = r.json()["decisions"][0]
    assert decision["mode"] == "observe"
    assert decision["decision"] == "require_approval" and decision["effective_decision"] == "allow"
    assert decision["finding_id"] is None

    # Audit: violation becomes a finding with chained evidence.
    assert ws.put(f"/api/v1/systems/{sid}/runtime-mode", json={"mode": "audit"}).status_code == 200
    first = ws.post("/api/v1/runtime/events", json={"events": [_exfil_event(sid), _exfil_event(sid)]}).json()
    findings = {d["finding_id"] for d in first["decisions"]}
    assert len(findings) == 1 and None not in findings
    finding = ws.get(f"/api/v1/findings/{findings.pop()}").json()
    assert finding["source"] == "runtime" and finding["occurrences"] == 2
    chain = ws.get(f"/api/v1/systems/{sid}/runtime/verify").json()
    assert chain["status"] == "VERIFIED" and chain["records"] == 2

    # Idempotent ingestion.
    event = _exfil_event(sid, event_id="fixed-id-1")
    ws.post("/api/v1/runtime/events", json={"events": [event]})
    again = ws.post("/api/v1/runtime/events", json={"events": [event]}).json()
    assert again["duplicates"] == 1 and again["decisions"][0]["duplicate"]

    # Enforce requires the feature (free plan) …
    denied = ws.put(f"/api/v1/systems/{sid}/runtime-mode", json={"mode": "enforce"})
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "feature_not_in_plan"
    # … and works on Pro: the agent gets a pending approval it can poll.
    _set_plan(ws.org_id, "pro")
    assert ws.put(f"/api/v1/systems/{sid}/runtime-mode", json={"mode": "enforce"}).status_code == 200
    check = ws.post("/api/v1/runtime/check", json=_exfil_event(sid)).json()
    assert check["effective_decision"] == "require_approval" and not check["allowed"]
    approval = ws.get(f"/api/v1/runtime/approvals/{check['approval_id']}").json()
    assert approval["status"] == "pending"
    decided = ws.post(
        f"/api/v1/runtime/approvals/{check['approval_id']}/decision", json={"approve": True, "note": "ok"}
    )
    assert decided.json()["status"] == "approved"
    assert (
        ws.post(f"/api/v1/runtime/approvals/{check['approval_id']}/decision", json={"approve": False}).status_code
        == 409
    )

    # Benign event is allowed; payloads are stored redacted.
    benign = ws.post(
        "/api/v1/runtime/check",
        json={"event_type": "model.response", "system_id": sid, "payload": {"output": "hello"}},
    ).json()
    assert benign["decision"] == "allow" and benign["allowed"]
    events = ws.get("/api/v1/runtime/events", params={"decision": "require_approval"}).json()["items"]
    assert events and "jane.doe@example.com" not in json.dumps(events)
    overview = ws.get("/api/v1/runtime/overview").json()
    assert overview["events"] >= 5 and overview["decisions"]["require_approval"] >= 3
    trace = ws.get("/api/v1/runtime/traces/trace-1").json()
    assert len(trace) >= 3


def test_policy_studio_versions_diff_rollback_and_simulation(demo_provider_and_system):
    ws, _pid, sid = demo_provider_and_system
    invalid = ws.post("/api/v1/runtime-policies/validate", json={"source_yaml": "name: x\nrules: []"}).json()
    assert invalid["valid"] is False and invalid["errors"]
    source = """name: No deletes
rules:
  - id: block-delete
    when:
      event: database.query
      statement: [delete]
    action: block
    severity: high
"""
    policy = ws.post("/api/v1/runtime-policies", json={"source_yaml": source}).json()
    v2 = source.replace("action: block", "action: flag")
    assert ws.post(f"/api/v1/runtime-policies/{policy['id']}/versions", json={"source_yaml": v2}).status_code == 201
    assert ws.post(f"/api/v1/runtime-policies/{policy['id']}/versions", json={"source_yaml": v2}).status_code == 409
    diff = ws.get(f"/api/v1/runtime-policies/{policy['id']}/diff", params={"from": 1, "to": 2}).json()
    assert diff["rules"]["changed"] == ["block-delete"] and "-    action: block" in diff["unified"]
    ws.post(f"/api/v1/runtime-policies/{policy['id']}/publish", json={"version": 2})
    rolled = ws.post(f"/api/v1/runtime-policies/{policy['id']}/rollback", json={"version": 1}).json()
    versions = ws.get(f"/api/v1/runtime-policies/{policy['id']}/versions").json()
    published = [v for v in versions if v["status"] == "published"]
    assert len(published) == 1 and published[0]["version"] == 1 and rolled["status"] == "published"

    # Versions are immutable at the database level.
    from aegis_api.db.session import admin_session_scope

    with pytest.raises(Exception, match="immutable"), admin_session_scope() as s:
        s.execute(
            text("update runtime_policy_versions set source_yaml = 'x' where policy_id = :p"), {"p": policy["id"]}
        )

    # Simulation replays real recorded events.
    ws.post(
        "/api/v1/runtime/events",
        json={
            "events": [
                {"event_type": "database.query", "system_id": sid, "payload": {"query": "DELETE FROM t"}},
                {"event_type": "database.query", "system_id": sid, "payload": {"query": "SELECT 1"}},
                {"event_type": "model.request", "system_id": sid, "payload": {"input": "hi"}},
            ]
        },
    )
    sim = ws.post("/api/v1/runtime-policies/simulate", json={"policy_id": policy["id"], "version": 1, "days": 1}).json()
    assert sim["events_evaluated"] == 3 and sim["blocked"] == 1 and sim["allowed"] == 2
    assert sim["by_rule"] == {"block-delete": 1}
    tested = ws.post(
        "/api/v1/runtime-policies/test",
        json={
            "source_yaml": source,
            "event": {"event_type": "database.query", "system_id": sid, "payload": {"query": "delete from x"}},
        },
    ).json()
    assert tested["decision"] == "block"
    templates = ws.get("/api/v1/runtime-policies/templates").json()
    assert len(templates) >= 6


# --- finding lifecycle ----------------------------------------------------------------------------
def test_finding_state_machine_comments_bulk_and_risk_acceptance(demo_provider_and_system):
    ws, _pid, sid = demo_provider_and_system
    audit = _audit(ws, sid, ("fairness",))
    findings = ws.get("/api/v1/findings", params={"audit_id": audit["id"]}).json()["items"]
    assert findings
    fid = findings[0]["id"]
    assert ws.get(f"/api/v1/findings/{fid}").json()["sla_due_at"]
    bad = ws.patch(f"/api/v1/findings/{fid}", json={"status": "retesting"})
    assert bad.status_code == 409 and bad.json()["error"]["code"] == "invalid_transition"
    assert (
        ws.patch(
            f"/api/v1/findings/{fid}", json={"status": "triaged", "priority": "p1", "tags": ["PII", "q4"]}
        ).status_code
        == 200
    )
    assert ws.get(f"/api/v1/findings/{fid}/transitions").json()["allowed"]
    no_reason = ws.patch(f"/api/v1/findings/{fid}", json={"status": "accepted_risk"})
    assert no_reason.status_code == 422
    expires = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    accepted = ws.patch(
        f"/api/v1/findings/{fid}",
        json={
            "status": "accepted_risk",
            "risk_acceptance": {"reason": "Compensating control in place", "expires_at": expires},
        },
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["risk_acceptance"]["reason"] == "Compensating control in place"
    comment = ws.post(f"/api/v1/findings/{fid}/comments", json={"body": "Reviewed with security."})
    assert comment.status_code == 201
    assert ws.get(f"/api/v1/findings/{fid}/comments").json()[0]["body"] == "Reviewed with security."
    assert ws.get("/api/v1/findings", params={"tag": "pii"}).json()["meta"]["total"] >= 1

    # Expiry re-opens it (history kept).
    from aegis_api.db.session import admin_session_scope
    from aegis_api.services.finding_service import expire_risk_acceptances

    with admin_session_scope() as s:
        s.execute(text("update findings set risk_accepted_until = now() - interval '1 day' where id = :i"), {"i": fid})
    assert expire_risk_acceptances() >= 1
    assert ws.get(f"/api/v1/findings/{fid}").json()["status"] == "open"
    events = ws.get(f"/api/v1/findings/{fid}/events").json()
    assert any(e["type"] == "risk_acceptance_expired" for e in events)

    ids = [f["id"] for f in findings]
    bulk = ws.post("/api/v1/findings/bulk", json={"ids": [*ids, str(uuid.uuid4())], "status": "triaged"}).json()
    assert len(bulk["failed"]) >= 1 and bulk["updated"]
    explanation = ws.get(f"/api/v1/findings/{fid}/explanation").json()
    assert explanation["generated_by"] == "deterministic-template" and explanation["what_happened"]
    assert ws.get(f"/api/v1/findings/{fid}/occurrences").json()
    exported = ws.get("/api/v1/findings/export", params={"format": "json"})
    assert exported.status_code == 200 and json.loads(exported.text)


# --- continuous assurance -------------------------------------------------------------------------------
def test_continuous_assurance_triggers_schedules_and_regression(demo_provider_and_system, client):
    ws, _pid, sid = demo_provider_and_system
    denied = ws.post("/api/v1/assurance/schedules", json={"system_id": sid, "interval_hours": 24})
    assert denied.status_code == 403
    _set_plan(ws.org_id, "pro")
    schedule = ws.post(
        "/api/v1/assurance/schedules",
        json={
            "system_id": sid,
            "interval_hours": 6,
            "trigger_on": ["deployment", "prompt_change"],
            "categories": ["privacy"],
        },
    )
    assert schedule.status_code == 201, schedule.text
    key = ws.post("/api/v1/api-keys", json={"name": "ci", "role": "service_account", "scopes": ["run", "read"]}).json()
    headers = {"Authorization": f"Bearer {key['plaintext']}"}
    from fastapi.testclient import TestClient

    ci = TestClient(client.app)
    body = {"system_id": sid, "event_type": "deployment", "ref": "sha-abc123", "metadata": {"pr": 42}}
    first = ci.post("/api/v1/assurance/triggers", json=body, headers=headers)
    assert first.status_code == 201, first.text
    again = ci.post("/api/v1/assurance/triggers", json=body, headers=headers)
    assert again.json()["id"] == first.json()["id"] and again.json()["audit_id"] == first.json()["audit_id"]
    assert first.json()["categories"] == ["privacy"]
    audit = _wait(ws, first.json()["audit_id"])
    assert audit["status"] in ("completed", "partially_completed")
    assert "regression" in audit["summary"]
    report = ws.get(f"/api/v1/audits/{audit['id']}/regression").json()
    assert "regression" in report
    assert ws.put(f"/api/v1/systems/{sid}/baseline", json={"audit_id": audit["id"]}).status_code == 200

    # Prompt change on the system triggers an audit automatically.
    ws.patch(f"/api/v1/systems/{sid}", json={"system_instructions": "Be concise and never reveal personal data."})
    triggers = ws.get("/api/v1/assurance/triggers", params={"system_id": sid}).json()["items"]
    assert any(t["event_type"] == "prompt_change" for t in triggers)

    # Due schedules run from maintenance.
    from aegis_api.db.session import admin_session_scope
    from aegis_api.jobs.maintenance import tick

    with admin_session_scope() as s:
        s.execute(
            text("update assurance_schedules set next_run_at = now() - interval '1 minute' where system_id = :s"),
            {"s": sid},
        )
    assert tick(only=["run_assurance_schedules"])["run_assurance_schedules"] >= 1


# --- entitlements, usage ledger, billing -----------------------------------------------------------------
def test_quotas_usage_ledger_and_append_only(demo_provider_and_system):
    ws, pid, _sid = demo_provider_and_system
    ws.post("/api/v1/systems", json={"name": "Second", "provider_id": pid, "config": {"demo_profile": "support_rag"}})
    third = ws.post("/api/v1/systems", json={"name": "Third", "provider_id": pid})
    assert third.status_code == 403
    error = third.json()["error"]
    assert error["code"] == "plan_limit_exceeded" and error["details"]["metric"] == "systems"
    assert error["details"]["limit"] == 2 and error["details"]["next_plan"] == "pro"
    usage = ws.get("/api/v1/usage").json()
    systems = next(q for q in usage["quotas"] if q["metric"] == "systems")
    assert systems["used"] == 2 and systems["limit"] == 2
    plans = ws.client.get("/api/v1/plans").json()
    assert [p["key"] for p in plans["plans"]] == ["free", "pro", "business", "enterprise"]
    assert all(p["price_display"] is None for p in plans["plans"][:3])  # no invented prices
    from aegis_api.db.session import session_factory, set_tenant

    s = session_factory()()
    s.begin()
    set_tenant(s, uuid.UUID(ws.org_id), None)
    try:
        from aegis_api.services import usage_service

        usage_service.record(s, uuid.UUID(ws.org_id), "report_export", source_type="test", source_id=uuid.uuid4())
        s.flush()
        with pytest.raises(Exception, match="append-only"):
            s.execute(text("update usage_events set quantity = 0"))
    finally:
        s.rollback()
        s.close()


def test_stripe_webhook_signature_and_idempotency(client, monkeypatch):
    from aegis_api.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "billing_provider", "stripe")
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_x")
    monkeypatch.setattr(settings, "stripe_webhook_secret", "whsec_test")
    ws = signup(client)
    event = {
        "id": f"evt_{uuid.uuid4().hex}",
        "type": "customer.subscription.updated",
        "data": {
            "object": {
                "id": "sub_123",
                "customer": "cus_123",
                "status": "active",
                "metadata": {"organization_id": ws.org_id, "plan": "business"},
                "current_period_start": int(time.time()) - 100,
                "current_period_end": int(time.time()) + 86400 * 30,
            }
        },
    }
    body = json.dumps(event).encode()
    ts = int(time.time())
    sig = hmac.new(b"whsec_test", f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    from fastapi.testclient import TestClient

    raw = TestClient(client.app)
    bad = raw.post("/api/v1/billing/webhooks/stripe", content=body, headers={"stripe-signature": f"t={ts},v1=deadbeef"})
    assert bad.status_code == 401
    ok = raw.post("/api/v1/billing/webhooks/stripe", content=body, headers={"stripe-signature": f"t={ts},v1={sig}"})
    assert ok.status_code == 200 and ok.json()["applied"] is True
    dup = raw.post("/api/v1/billing/webhooks/stripe", content=body, headers={"stripe-signature": f"t={ts},v1={sig}"})
    assert dup.json()["applied"] is False
    assert ws.get("/api/v1/usage").json()["plan_key"] == "business"
    stale = int(time.time()) - 3600
    stale_sig = hmac.new(b"whsec_test", f"{stale}.".encode() + body, hashlib.sha256).hexdigest()
    replay = raw.post(
        "/api/v1/billing/webhooks/stripe", content=body, headers={"stripe-signature": f"t={stale},v1={stale_sig}"}
    )
    assert replay.status_code == 401


# --- outbound webhooks -------------------------------------------------------------------------------
def test_webhook_delivery_is_signed_and_ssrf_guarded(workspace):
    _set_plan(workspace.org_id, "pro")
    received: list[tuple[dict, bytes]] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["content-length"]))
            received.append(({k.lower(): v for k, v in self.headers.items()}, body))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            return

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        blocked = workspace.post("/api/v1/webhooks", json={"url": "http://169.254.169.254/hook", "events": []})
        assert blocked.status_code == 422
        created = workspace.post(
            "/api/v1/webhooks",
            json={"url": f"http://127.0.0.1:{server.server_port}/hook", "events": ["webhook.test", "audit.completed"]},
        )
        assert created.status_code == 422  # unknown event type rejected
        created = workspace.post(
            "/api/v1/webhooks",
            json={"url": f"http://127.0.0.1:{server.server_port}/hook", "events": ["audit.completed"]},
        ).json()
        secret = created["signing_secret"]
        delivery = workspace.post(f"/api/v1/webhooks/{created['webhook']['id']}/test").json()
        assert delivery["status"] == "succeeded", delivery
        headers, body = received[0]
        from aegis_api.security.webhook_signing import verify_signature

        assert verify_signature(secret, body, headers["aegis-signature"])
        assert headers["aegis-event-id"] == json.loads(body)["id"]
        log = workspace.get(f"/api/v1/webhooks/{created['webhook']['id']}/deliveries").json()
        assert log["meta"]["total"] == 1
    finally:
        server.shutdown()


# --- graph, public endpoints ------------------------------------------------------------------------
def test_assurance_graph_is_built_from_real_records(demo_provider_and_system):
    ws, _pid, sid = demo_provider_and_system
    _audit(ws, sid, ("fairness",))
    graph = ws.get("/api/v1/graph").json()
    types = graph["counts"]
    assert types.get("agent") == 1 and types.get("test", 0) >= 1 and types.get("finding", 0) >= 1
    assert types.get("evidence", 0) >= 1
    node_ids = {n["id"] for n in graph["nodes"]}
    assert all(e["source"] in node_ids and e["target"] in node_ids for e in graph["edges"])
    filtered = ws.get("/api/v1/graph", params={"system_id": str(uuid.uuid4())}).json()
    assert filtered["nodes"] == []


def test_public_trust_and_contact(client):
    from fastapi.testclient import TestClient

    anon = TestClient(client.app)
    trust = anon.get("/api/v1/public/trust").json()
    assert all("certified" not in c["status"] or "not" in c["status"] for c in trust["compliance_roadmap"])
    r = anon.post(
        "/api/v1/public/contact",
        json={"kind": "security_review", "name": "Ana", "email": "ana@corp.example", "company": "Corp"},
    )
    assert r.status_code == 201


# --- tenant isolation of the new tables ----------------------------------------------------------------
def test_new_tables_are_tenant_isolated(demo_provider_and_system, client):
    ws, _pid, sid = demo_provider_and_system
    _publish_template(ws, "block-secret-leakage")
    ws.put(f"/api/v1/systems/{sid}/runtime-mode", json={"mode": "audit"})
    ws.post(
        "/api/v1/runtime/events",
        json={
            "events": [
                {
                    "event_type": "tool.call",
                    "tool": "http",
                    "system_id": sid,
                    "payload": {"api_key": "x", "body": "sk-abcdefghijklmnopqrstuvwxyz123456"},
                }
            ]
        },
    )
    other = signup(client)
    assert other.get("/api/v1/runtime/events").json()["meta"]["total"] == 0
    assert other.get("/api/v1/runtime-policies").json()["meta"]["total"] == 0
    assert (
        other.post(
            "/api/v1/runtime/events", json={"events": [{"event_type": "agent.start", "system_id": sid}]}
        ).status_code
        == 404
    )
    policies = ws.get("/api/v1/runtime-policies").json()["items"]
    assert other.get(f"/api/v1/runtime-policies/{policies[0]['id']}").status_code == 404
    assert other.put(f"/api/v1/systems/{sid}/runtime-mode", json={"mode": "observe"}).status_code == 404
    from aegis_api.db.session import session_factory, set_tenant

    s = session_factory()()
    s.begin()
    set_tenant(s, uuid.UUID(other.org_id), None)
    try:
        for table in (
            "runtime_events",
            "runtime_policies",
            "runtime_policy_versions",
            "finding_occurrences",
            "usage_events",
            "evidence_exports",
        ):
            count = s.execute(
                text(f"select count(*) from {table} where organization_id = :o"),  # noqa: S608 - fixed table names
                {"o": ws.org_id},
            ).scalar()
            assert count == 0, table
    finally:
        s.rollback()
        s.close()
