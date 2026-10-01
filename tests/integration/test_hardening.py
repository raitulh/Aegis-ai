"""Regression tests for the production-hardening fixes (see docs/audit-2026-10.md)."""

from __future__ import annotations

import threading
import time
import uuid

import pytest
from sqlalchemy import func, select, text

from tests.conftest import TRUSTED_ORIGIN, requires_db, signup

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


def _run_audit(ws, sid: str, categories: list[str] | None = None, **extra) -> dict:
    r = ws.post(
        "/api/v1/audits",
        json={
            "system_id": sid,
            "categories": categories or ["fairness"],
            "intensity": "quick",
            "config": {"seed": 7},
            **extra,
        },
    )
    assert r.status_code == 201, r.text
    return _wait(ws, r.json()["id"])


def _tenant_session(org_id: str):
    from aegis_api.db.session import session_factory, set_tenant

    session = session_factory()()
    session.begin()
    set_tenant(session, uuid.UUID(org_id), None)
    return session


# --- P0-3: duplicate delivery never runs an audit twice ------------------------------------------
def test_duplicate_delivery_is_a_no_op(demo_provider_and_system):
    ws, _pid, sid = demo_provider_and_system
    audit = _run_audit(ws, sid)
    assert audit["status"] in ("completed", "partially_completed")
    from aegis_api.jobs.jobs import run_audit_job
    from aegis_api.models import TestResult

    session = _tenant_session(ws.org_id)
    try:
        before = session.scalar(select(func.count(TestResult.id)).where(TestResult.audit_id == uuid.UUID(audit["id"])))
        run_audit_job(session, audit["id"])  # a redelivered message
        session.commit()
        after = session.scalar(select(func.count(TestResult.id)).where(TestResult.audit_id == uuid.UUID(audit["id"])))
    finally:
        session.close()
    assert before == after and before > 0


def test_claim_is_exclusive(demo_provider_and_system):
    ws, _pid, sid = demo_provider_and_system
    created = ws.post(
        "/api/v1/audits", json={"system_id": sid, "categories": ["privacy"], "intensity": "quick", "start": False}
    ).json()
    from aegis_api.models import Audit
    from aegis_api.services.orchestrator import claim_audit

    session = _tenant_session(ws.org_id)
    session.execute(text("update audits set status = 'queued' where id = :id"), {"id": created["id"]})
    session.commit()
    session.close()
    winners: list[object] = []

    def attempt(name: str) -> None:
        winners.append(claim_audit(uuid.UUID(ws.org_id), uuid.UUID(created["id"]), name))

    threads = [threading.Thread(target=attempt, args=(f"w{i}",)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(1 for w in winners if w is not None) == 1
    session = _tenant_session(ws.org_id)
    assert session.get(Audit, uuid.UUID(created["id"])).status == "running"
    session.close()


# --- P0-1: progress is visible while the results transaction is still open ----------------------
def test_progress_channel_commits_independently(demo_provider_and_system):
    ws, _pid, sid = demo_provider_and_system
    created = ws.post(
        "/api/v1/audits", json={"system_id": sid, "categories": ["privacy"], "intensity": "quick", "start": False}
    ).json()
    from aegis_api.models import AuditEvent
    from aegis_api.services.orchestrator import ProgressChannel, claim_audit

    session = _tenant_session(ws.org_id)
    session.execute(text("update audits set status = 'queued' where id = :id"), {"id": created["id"]})
    session.commit()
    session.close()
    claim_audit(uuid.UUID(ws.org_id), uuid.UUID(created["id"]), "worker-x")
    channel = ProgressChannel(uuid.UUID(ws.org_id), uuid.UUID(created["id"]), "worker-x")
    channel.emit("audit.stage", "visible now", stage="inference", progress=40)
    events = ws.get(f"/api/v1/audits/{created['id']}/events").json()
    assert any(e["message"] == "visible now" for e in events)
    assert ws.get(f"/api/v1/audits/{created['id']}").json()["progress"] == 40
    session = _tenant_session(ws.org_id)
    assert session.scalar(select(func.count(AuditEvent.id)).where(AuditEvent.audit_id == uuid.UUID(created["id"]))) >= 1
    session.close()


# --- P0-2: cancellation during a run discards the attempt and is never overwritten ---------------
def test_cancel_during_run_discards_results(demo_provider_and_system, monkeypatch):
    ws, _pid, sid = demo_provider_and_system
    from aegis_api.services import orchestrator

    original = orchestrator.AuditRunner._map_controls

    def cancel_midway(self, rows, policy_ids):
        # Simulates a user pressing Cancel while results are being assembled (separate connection).
        s = _tenant_session(ws.org_id)
        s.execute(
            text("update audits set cancel_requested = true, status = 'cancelled' where id = :id"),
            {"id": str(self.audit_id)},
        )
        s.commit()
        s.close()
        return original(self, rows, policy_ids)

    monkeypatch.setattr(orchestrator.AuditRunner, "_map_controls", cancel_midway)
    audit = _run_audit(ws, sid, ["privacy"])
    assert audit["status"] == "cancelled"
    from aegis_api.models import Finding, TestResult

    session = _tenant_session(ws.org_id)
    try:
        aid = uuid.UUID(audit["id"])
        assert session.scalar(select(func.count(TestResult.id)).where(TestResult.audit_id == aid)) == 0
        assert session.scalar(select(func.count(Finding.id)).where(Finding.audit_id == aid)) == 0
    finally:
        session.close()
    events = ws.get(f"/api/v1/audits/{audit['id']}/events").json()
    assert events[-1]["type"] == "audit.cancelled"


def test_cancel_queued_audit_is_immediate(demo_provider_and_system):
    ws, _pid, sid = demo_provider_and_system
    created = ws.post(
        "/api/v1/audits", json={"system_id": sid, "categories": ["privacy"], "intensity": "quick", "start": False}
    ).json()
    r = ws.post(f"/api/v1/audits/{created['id']}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert ws.post(f"/api/v1/audits/{created['id']}/cancel").status_code == 409


# --- P0-4: concurrent finding-number allocation --------------------------------------------------
def test_finding_numbers_are_unique_under_concurrency(workspace):
    from aegis_api.services.finding_service import allocate_finding_number

    numbers: list[int] = []
    lock = threading.Lock()

    def grab() -> None:
        for _ in range(5):
            n = allocate_finding_number(uuid.UUID(workspace.org_id))
            with lock:
                numbers.append(n)

    threads = [threading.Thread(target=grab) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(numbers) == 30 and len(set(numbers)) == 30


# --- P0-5/6: per-audit occurrences and per-system dedup -----------------------------------------
def test_reobserved_finding_stays_attached_to_both_audits(demo_provider_and_system):
    ws, _pid, sid = demo_provider_and_system
    first = _run_audit(ws, sid)
    second = _run_audit(ws, sid)
    a_findings = ws.get(f"/api/v1/audits/{first['id']}/findings").json()
    b_findings = ws.get(f"/api/v1/audits/{second['id']}/findings").json()
    assert a_findings, "the first audit must keep its findings after re-observation"
    assert {f["id"] for f in a_findings} & {f["id"] for f in b_findings}
    assert all(f["first_detected_here"] for f in a_findings)
    assert not any(f["first_detected_here"] for f in b_findings if f["id"] in {x["id"] for x in a_findings})
    comparison = ws.get(f"/api/v1/audits/{first['id']}/compare/{second['id']}").json()
    assert comparison["unchanged"] >= 1
    assert not comparison["resolved_findings"]
    finding = ws.get(f"/api/v1/findings/{a_findings[0]['id']}").json()
    assert finding["audit_id"] == first["id"] and finding["last_audit_id"] == second["id"]


def test_same_issue_on_two_systems_is_two_findings(demo_provider_and_system):
    ws, pid, sid = demo_provider_and_system
    other = ws.post(
        "/api/v1/systems",
        json={
            "name": "Hiring-Agent-B",
            "system_type": "agent",
            "provider_id": pid,
            "config": {"demo_profile": "hiring_agent", "guardrails": {}, "tools": {}},
        },
    ).json()
    _run_audit(ws, sid)
    _run_audit(ws, other["id"])
    by_system: dict[str, set[str]] = {}
    for f in ws.get("/api/v1/findings", params={"category": "fairness", "page_size": 100}).json()["items"]:
        by_system.setdefault(f["system_id"], set()).add(f["id"])
    assert sid in by_system and other["id"] in by_system
    assert not (by_system[sid] & by_system[other["id"]])


# --- P0-9: RBAC — owner protections ---------------------------------------------------------------
def test_admin_cannot_demote_owner_and_last_owner_is_protected(client):
    owner = signup(client)
    team = owner.get("/api/v1/team").json()
    owner_membership = team[0]["membership_id"]
    # Last owner cannot demote themselves.
    r = owner.patch(f"/api/v1/team/{owner_membership}/role", json={"role": "viewer"})
    assert r.status_code == 409
    # An admin key cannot change the owner's role.
    from aegis_api.db.session import admin_session_scope
    from aegis_api.models import Membership, User
    from aegis_api.security.passwords import hash_password

    email = f"admin-{uuid.uuid4().hex[:8]}@example.com"
    with admin_session_scope() as s:
        user = User(
            email=email,
            full_name="Admin",
            auth_provider="local",
            auth_subject=f"local:{uuid.uuid4()}",
            password_hash=hash_password("Str0ng-Pass!23"),
        )
        s.add(user)
        s.flush()
        s.add(Membership(organization_id=uuid.UUID(owner.org_id), user_id=user.id, role="admin", status="active"))
        user.default_organization_id = uuid.UUID(owner.org_id)
    client.cookies.clear()
    login = client.post("/api/v1/auth/login", json={"email": email, "password": "Str0ng-Pass!23"})
    assert login.status_code == 200, login.text
    admin_cookies = dict(login.cookies)
    r = client.patch(f"/api/v1/team/{owner_membership}/role", json={"role": "viewer"}, cookies=admin_cookies)
    assert r.status_code == 403
    r = client.patch(f"/api/v1/team/{owner_membership}/role", json={"role": "owner"}, cookies=admin_cookies)
    assert r.status_code == 403


def test_role_catalog_and_service_account_not_assignable(workspace):
    from aegis_api.security.rbac import ASSIGNABLE_ROLES, permissions_for_role

    assert "service_account" not in ASSIGNABLE_ROLES
    assert "runtime:decide" in permissions_for_role("service_account")
    assert "team:manage" not in permissions_for_role("security_engineer")
    assert "findings:accept_risk" in permissions_for_role("security_engineer")
    assert "findings:accept_risk" not in permissions_for_role("ai_engineer")
    r = workspace.post("/api/v1/team/invite", json={"email": "sa@example.com", "role": "service_account"})
    assert r.status_code == 422


# --- P0-10: immutability cannot be bypassed by the app role ---------------------------------------
def test_app_role_cannot_bypass_evidence_immutability(demo_provider_and_system):
    ws, _pid, sid = demo_provider_and_system
    audit = _run_audit(ws, sid, ["privacy"])
    evidence = ws.get(f"/api/v1/audits/{audit['id']}/evidence").json()
    assert evidence
    session = _tenant_session(ws.org_id)
    try:
        session.execute(text("select set_config('aegis.allow_evidence_delete', 'on', true)"))
        with pytest.raises(Exception, match="append-only"):
            session.execute(text("delete from evidence where id = :id"), {"id": evidence[0]["id"]})
    finally:
        session.rollback()
        session.close()
    session = _tenant_session(ws.org_id)
    try:
        session.execute(text("select set_config('aegis.allow_evidence_delete', 'on', true)"))
        with pytest.raises(Exception, match="append-only"):
            session.execute(text("delete from audit_logs where organization_id = :o"), {"o": ws.org_id})
    finally:
        session.rollback()
        session.close()


# --- P0-11: identity sessions are not held by streaming responses ---------------------------------
def test_identity_pool_not_pinned_by_principal_resolution(workspace):
    from aegis_api.db.session import get_admin_engine

    for _ in range(25):
        assert workspace.get("/api/v1/auth/session").status_code == 200
    pool = get_admin_engine().pool
    assert pool.checkedout() == 0  # type: ignore[attr-defined]


# --- P0-12/13: rate limiting cannot be spoofed; login throttled per account ----------------------
def test_x_forwarded_for_ignored_without_trusted_proxies():
    from starlette.requests import Request

    from aegis_api.ratelimit import client_ip

    scope = {
        "type": "http",
        "headers": [(b"x-forwarded-for", b"1.2.3.4")],
        "client": ("10.9.8.7", 1234),
        "method": "GET",
        "path": "/",
    }
    assert client_ip(Request(scope)) == "10.9.8.7"


def test_login_throttled_per_account(client, monkeypatch):
    from aegis_api.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "login_max_failures", 3)
    monkeypatch.setattr(settings, "rate_limit_public_per_min", 1000)
    ws = signup(client)
    email = ws.data["user"]["email"]
    client.cookies.clear()
    for _ in range(3):
        assert client.post("/api/v1/auth/login", json={"email": email, "password": "wrong-Pass!1"}).status_code == 401
    blocked = client.post("/api/v1/auth/login", json={"email": email, "password": "Str0ng-Pass!23"})
    assert blocked.status_code == 429
    from aegis_api.ratelimit import login_throttle

    login_throttle().reset(email)


# --- P1-5: CSRF origin guard ----------------------------------------------------------------------
def test_cookie_requests_require_trusted_origin(workspace, client):
    r = workspace.post("/api/v1/systems", json={"name": "x"}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "origin_rejected"
    from fastapi.testclient import TestClient

    raw = TestClient(client.app)
    r = raw.post("/api/v1/systems", json={"name": "x"}, cookies=workspace.cookies)
    assert r.status_code == 403
    key = workspace.post("/api/v1/api-keys", json={"name": "ci", "role": "analyst", "scopes": ["read", "write"]}).json()
    r = raw.post("/api/v1/systems", json={"name": "From CI"}, headers={"Authorization": f"Bearer {key['plaintext']}"})
    assert r.status_code == 201, r.text


# --- P1-3: SSE resume, auth and 404 ---------------------------------------------------------------
def test_sse_requires_auth_and_resumes_from_last_event_id(demo_provider_and_system, client):
    ws, _pid, sid = demo_provider_and_system
    audit = _run_audit(ws, sid, ["privacy"])
    from fastapi.testclient import TestClient

    anon = TestClient(client.app)
    assert anon.get(f"/api/v1/audits/{audit['id']}/stream").status_code == 401
    assert ws.get(f"/api/v1/audits/{uuid.uuid4()}/stream").status_code == 404
    events = ws.get(f"/api/v1/audits/{audit['id']}/events").json()
    resume_from = events[-3]["seq"]
    with ws.client.stream(
        "GET", f"/api/v1/audits/{audit['id']}/stream", cookies=ws.cookies, headers={"Last-Event-ID": str(resume_from)}
    ) as r:
        body = "".join(r.iter_text())
    ids = [int(line.split(": ")[1]) for line in body.splitlines() if line.startswith("id: ")]
    assert ids and min(ids) > resume_from
    assert "event: done" in body


# --- P1-4: idempotency keys -----------------------------------------------------------------------
def test_idempotency_key_replays_and_rejects_mismatch(demo_provider_and_system):
    ws, _pid, sid = demo_provider_and_system
    payload = {"system_id": sid, "categories": ["privacy"], "intensity": "quick", "start": False}
    key = f"idem-{uuid.uuid4().hex}"
    first = ws.post("/api/v1/audits", json=payload, headers={"Idempotency-Key": key})
    second = ws.post("/api/v1/audits", json=payload, headers={"Idempotency-Key": key})
    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert second.headers.get("idempotency-replayed") == "true"
    mismatch = ws.post("/api/v1/audits", json={**payload, "intensity": "deep"}, headers={"Idempotency-Key": key})
    assert mismatch.status_code == 422


# --- P1-6: readiness ------------------------------------------------------------------------------
def test_readiness_and_liveness(client):
    assert client.get("/live").json() == {"status": "ok"}
    ready = client.get("/ready")
    assert ready.status_code == 200 and ready.json()["checks"]["database"] == "ok"


# --- P1-8: invitations can be accepted ------------------------------------------------------------
def test_invitation_accept_creates_member(workspace, client):
    email = f"invitee-{uuid.uuid4().hex[:8]}@example.com"
    invite = workspace.post("/api/v1/team/invite", json={"email": email, "role": "security_engineer"})
    assert invite.status_code == 201, invite.text
    token = invite.json()["invite_url"].split("token=")[1]
    client.cookies.clear()
    accepted = client.post(
        "/api/v1/auth/invitations/accept",
        json={"token": token, "password": "An0ther-Pass!9", "full_name": "Invitee"},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["role"] == "security_engineer"
    assert accepted.json()["organization"]["id"] == workspace.org_id
    replay = client.post("/api/v1/auth/invitations/accept", json={"token": token, "password": "An0ther-Pass!9"})
    assert replay.status_code == 422


# --- P1-1/2: job ledger idempotency ---------------------------------------------------------------
def test_job_ledger_deduplicates_and_claims_once(workspace):
    from aegis_api.jobs import ledger

    org = uuid.UUID(workspace.org_id)
    key = f"test-job:{uuid.uuid4()}"
    job_id = ledger.enqueue("tests:noop", org, ["a"], key=key)
    assert job_id is not None
    assert ledger.enqueue("tests:noop", org, ["a"], key=key) is None
    assert ledger.claim(job_id, "w1") == 1
    assert ledger.claim(job_id, "w2") is None
    ledger.succeed(job_id, 5)
    assert ledger.enqueue("tests:noop", org, ["a"], key=key) is None
    assert ledger.enqueue("tests:noop", org, ["a"], key=key, force=True) == job_id


def test_transient_failures_retry_then_dead_letter(workspace):
    from aegis_api.jobs import ledger

    org = uuid.UUID(workspace.org_id)
    job_id = ledger.enqueue("tests:flaky", org, [], key=f"flaky:{uuid.uuid4()}", max_attempts=2)
    assert job_id is not None
    attempt = ledger.claim(job_id, "w")
    assert ledger.fail(job_id, ConnectionError("boom"), attempt, 1) is not None  # retry scheduled
    from aegis_api.db.session import admin_session_scope
    from aegis_api.models import JobRun

    with admin_session_scope() as s:
        s.execute(text("update job_runs set next_attempt_at = now() where id = :i"), {"i": str(job_id)})
    attempt = ledger.claim(job_id, "w")
    assert ledger.fail(job_id, ConnectionError("boom"), attempt, 1) is None
    with admin_session_scope() as s:
        assert s.get(JobRun, job_id).status == "dead"
    permanent = ledger.enqueue("tests:bad", org, [], key=f"bad:{uuid.uuid4()}")
    attempt = ledger.claim(permanent, "w")
    assert ledger.fail(permanent, ValueError("nope"), attempt, 1) is None
    with admin_session_scope() as s:
        assert s.get(JobRun, permanent).status == "failed"


def test_maintenance_tick_runs_and_reaps_stale_audit(demo_provider_and_system):
    ws, _pid, sid = demo_provider_and_system
    created = ws.post(
        "/api/v1/audits", json={"system_id": sid, "categories": ["privacy"], "intensity": "quick", "start": False}
    ).json()
    from aegis_api.db.session import admin_session_scope
    from aegis_api.jobs.maintenance import tick

    with admin_session_scope() as s:
        s.execute(
            text(
                "update audits set status='running', lease_owner='dead-worker', attempts=1, "
                "heartbeat_at = now() - interval '2 hours' where id = :id"
            ),
            {"id": created["id"]},
        )
    result = tick(only=["reap_stale_runs"])
    assert result["reap_stale_runs"]["requeued"] >= 1
    audit = _wait(ws, created["id"])
    assert audit["status"] in ("completed", "partially_completed")
    events = ws.get(f"/api/v1/audits/{created['id']}/events").json()
    assert any(e["type"] == "audit.recovered" for e in events)


def test_trusted_origin_constant_matches_settings():
    from aegis_api.config import get_settings

    assert TRUSTED_ORIGIN in get_settings().trusted_origins
