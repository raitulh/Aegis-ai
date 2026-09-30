"""Approvals: idempotent requests, human-only decisions, separation of duties, expiry, tenancy, signals."""

from __future__ import annotations

import sys
import types
import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select, update

from tests.conftest import requires_db, signup

pytestmark = [requires_db, pytest.mark.db]


class Member:
    """A second human in the lab organization (own login; selects the org with X-Aegis-Org)."""

    def __init__(self, client, lab, role: str) -> None:  # type: ignore[no-untyped-def]
        from aegis_api.db.session import session_factory
        from aegis_api.models import Membership

        ws = signup(client, org=f"Home {uuid.uuid4().hex[:6]}")
        session = session_factory(admin=True)()
        try:
            session.add(Membership(organization_id=lab.org_id, user_id=uuid.UUID(ws.user_id), role=role))
            session.commit()
        finally:
            session.close()
        self.ws = ws
        self.user_id = uuid.UUID(ws.user_id)
        self.headers = {"X-Aegis-Org": str(lab.org_id)}

    def post(self, path: str, **kw: Any):  # type: ignore[no-untyped-def]
        return self.ws.post(path, headers=self.headers, **kw)

    def get(self, path: str, **kw: Any):  # type: ignore[no-untyped-def]
        return self.ws.get(path, headers=self.headers, **kw)


def request(lab, actor=None, **overrides: Any):  # type: ignore[no-untyped-def]
    from aegis_api.lab.governance.approvals import request_approval

    params: dict[str, Any] = {
        "action": "execution.submit",
        "subject_type": "compute_job",
        "subject_id": str(uuid.uuid4()),
        "title": "Run GPU job",
        "payload": {"gpu_count": 1},
        "risk_level": "HIGH",
        "estimated_cost_usd": 12.5,
        "project_id": lab.project_id,
    }
    params.update(overrides)
    with lab.db() as db:
        approval = request_approval(db, actor or lab.actor(), **params)
        return approval.id, approval.subject_id


def decide(member, approval_id, approve=True, reason="Looks safe to run"):  # type: ignore[no-untyped-def]
    return member.post(f"/api/v1/approvals/{approval_id}/decision", json={"approve": approve, "reason": reason})


def test_request_is_idempotent_and_recorded(lab):  # type: ignore[no-untyped-def]
    from aegis_api.lab.models import Approval, LabEvent
    from aegis_api.models import AuditLog

    subject = str(uuid.uuid4())
    first, _ = request(lab, subject_id=subject)
    again, _ = request(lab, subject_id=subject, title="Different title")
    other, _ = request(lab)
    assert first == again and other != first
    with lab.db() as db:
        approval = db.get(Approval, first)
        assert approval.status == "PENDING" and approval.requested_by_type == "user"
        assert approval.workspace_id == lab.workspace_id and approval.expires_at is not None
        events = db.scalars(select(LabEvent).where(LabEvent.type == "APPROVAL_REQUESTED")).all()
        audits = db.scalars(select(AuditLog).where(AuditLog.action == "APPROVAL_REQUESTED")).all()
    assert len(events) == 2 and len(audits) == 2
    body = lab.get(f"/api/v1/approvals/{first}").json()
    assert body["status"] == "PENDING" and body["estimated_cost_usd"] == 12.5 and body["risk_level"] == "HIGH"


def test_request_validation(lab):  # type: ignore[no-untyped-def]
    from aegis_api.errors import ValidationFailed

    for bad in (
        {"action": "Not valid"},
        {"risk_level": "EXTREME"},
        {"required_permission": "root:everything"},
        {"expires_in_hours": 0},
        {"estimated_cost_usd": -1},
        {"title": "   "},
        {"payload": {"blob": "x" * 70_000}},
    ):
        with pytest.raises(ValidationFailed):
            request(lab, **bad)


def test_request_survives_caller_rollback(lab):  # type: ignore[no-untyped-def]
    from aegis_api.lab.core.errors import ApprovalRequired
    from aegis_api.lab.governance.approvals import request_approval
    from aegis_api.lab.models import Approval, LabEvent

    agent = lab.actor().for_agent(
        agent_run_id=uuid.uuid4(), agent_role="CodingAgent", agent_permissions=frozenset(), autonomy_level="L2"
    )
    with pytest.raises(ApprovalRequired) as exc, lab.db() as db:
        approval = request_approval(
            db, agent, action="tool.invoke", subject_type="tool_call", subject_id="call-1", title="Use shell tool"
        )
        raise ApprovalRequired("needs a human", approval_id=str(approval.id))
    approval_id = uuid.UUID(exc.value.approval_id or "")
    with lab.db() as db:
        restored = db.get(Approval, approval_id)
        assert restored is not None and restored.status == "PENDING"
        assert restored.requested_by_type == "agent" and restored.requested_by_agent_run_id == agent.agent_run_id
        assert db.scalar(select(LabEvent.id).where(LabEvent.subject_id == str(approval_id))) is not None
    # retrying returns the same durable request
    with lab.db() as db:
        again = request_approval(
            db, agent, action="tool.invoke", subject_type="tool_call", subject_id="call-1", title="Use shell tool"
        )
        assert again.id == approval_id


def test_human_reviewer_decides_and_events_are_written(lab, client):  # type: ignore[no-untyped-def]
    from aegis_api.lab.governance.approvals import is_approved
    from aegis_api.lab.models import LabEvent
    from aegis_api.models import AuditLog

    approval_id, subject = request(lab)
    reviewer = Member(client, lab, "reviewer")
    r = decide(reviewer, approval_id)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "APPROVED" and body["decided_by_id"] == str(reviewer.user_id)
    assert body["decision_reason"] == "Looks safe to run"
    with lab.db() as db:
        assert is_approved(db, lab.org_id, "execution.submit", "compute_job", subject)
        assert not is_approved(db, lab.org_id, "execution.submit", "compute_job", "other")
        decided = db.scalars(select(LabEvent).where(LabEvent.type == "APPROVAL_DECIDED")).all()
        granted = db.scalars(select(AuditLog).where(AuditLog.action == "APPROVAL_GRANTED")).all()
    assert len(decided) == 1 and decided[0].payload["approved"] is True
    assert len(granted) == 1 and granted[0].user_id == reviewer.user_id

    r = decide(reviewer, approval_id, approve=False)
    assert r.status_code == 409 and r.json()["error"]["code"] == "invalid_state_transition"


def test_rejection(lab, client):  # type: ignore[no-untyped-def]
    approval_id, _ = request(lab)
    reviewer = Member(client, lab, "reviewer")
    r = decide(reviewer, approval_id, approve=False, reason="Too expensive right now")
    assert r.status_code == 200 and r.json()["status"] == "REJECTED"


def test_api_key_and_agent_cannot_decide(lab, client):  # type: ignore[no-untyped-def]
    from aegis_api.errors import Forbidden
    from aegis_api.lab.governance.approvals import decide_approval

    approval_id, _ = request(lab)
    key = lab.post(
        "/api/v1/api-keys", json={"name": "reviewer-key", "role": "reviewer", "scopes": ["read", "write", "run"]}
    )
    headers = {"Authorization": f"Bearer {key.json()['plaintext']}"}
    r = client.post(
        f"/api/v1/approvals/{approval_id}/decision", headers=headers, json={"approve": True, "reason": "automated ok"}
    )
    assert r.status_code == 403
    agent = lab.actor().for_agent(
        agent_run_id=uuid.uuid4(),
        agent_role="VerifierAgent",
        agent_permissions=frozenset({"approval:decide"}),
        autonomy_level="L5",
    )
    with lab.db() as db, pytest.raises(Forbidden):
        decide_approval(db, agent, approval_id, approve=True, reason="I approve myself")
    assert lab.get(f"/api/v1/approvals/{approval_id}").json()["status"] == "PENDING"


def test_missing_permission_is_403(lab, client):  # type: ignore[no-untyped-def]
    approval_id, _ = request(lab)
    viewer = Member(client, lab, "viewer")
    r = decide(viewer, approval_id)
    assert r.status_code == 403
    approval_id, _ = request(lab, required_permission="discovery:publish")
    researcher = Member(client, lab, "reviewer")
    assert decide(researcher, approval_id).status_code == 200  # reviewers hold discovery:publish


def test_separation_of_duties(lab, client):  # type: ignore[no-untyped-def]
    from aegis_api.db.session import session_factory
    from aegis_api.models import Organization

    approval_id, _ = request(lab)  # requested by the owner
    r = lab.post(f"/api/v1/approvals/{approval_id}/decision", json={"approve": True, "reason": "self approval"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "separation_of_duties"

    session = session_factory(admin=True)()
    try:
        session.execute(
            update(Organization).where(Organization.id == lab.org_id).values(settings={"allow_self_approval": True})
        )
        session.commit()
    finally:
        session.close()
    r = lab.post(f"/api/v1/approvals/{approval_id}/decision", json={"approve": True, "reason": "self approval"})
    assert r.status_code == 200 and r.json()["status"] == "APPROVED"


def test_expired_request_becomes_expired(lab, client):  # type: ignore[no-untyped-def]
    from aegis_api.db.base import utcnow
    from aegis_api.lab.models import Approval, LabEvent

    approval_id, _ = request(lab)
    with lab.db() as db:
        db.execute(
            update(Approval).where(Approval.id == approval_id).values(expires_at=utcnow() - timedelta(minutes=1))
        )
    reviewer = Member(client, lab, "reviewer")
    r = decide(reviewer, approval_id)
    assert r.status_code == 409 and r.json()["error"]["code"] == "approval_expired"
    with lab.db() as db:
        assert db.get(Approval, approval_id).status == "EXPIRED"  # persisted despite the request rollback
        expired = db.scalars(select(LabEvent).where(LabEvent.type == "APPROVAL_DECIDED")).all()
    assert [e.payload["status"] for e in expired] == ["EXPIRED"]
    assert decide(reviewer, approval_id).json()["error"]["code"] == "invalid_state_transition"


def test_expire_due_approvals_sweep_and_activity(lab):  # type: ignore[no-untyped-def]
    from aegis_api.db.base import utcnow
    from aegis_api.lab.governance.activities import expire_approvals
    from aegis_api.lab.models import Approval
    from aegis_api.lab.workflows.registry import ActivityContext

    overdue, _ = request(lab)
    fresh, _ = request(lab)
    with lab.db() as db:
        db.execute(update(Approval).where(Approval.id == overdue).values(expires_at=utcnow() - timedelta(hours=1)))
    result = expire_approvals(ActivityContext(actor=lab.actor()), {})
    assert result == {"expired": 1}
    assert expire_approvals(ActivityContext(actor=lab.actor()), {}) == {"expired": 0}  # idempotent
    with lab.db() as db:
        assert db.get(Approval, overdue).status == "EXPIRED"
        assert db.get(Approval, fresh).status == "PENDING"


def test_expired_pending_request_is_replaced(lab):  # type: ignore[no-untyped-def]
    from aegis_api.db.base import utcnow
    from aegis_api.lab.models import Approval

    subject = "job-42"
    first, _ = request(lab, subject_id=subject)
    with lab.db() as db:
        db.execute(update(Approval).where(Approval.id == first).values(expires_at=utcnow() - timedelta(minutes=5)))
    second, _ = request(lab, subject_id=subject)
    assert second != first
    with lab.db() as db:
        assert db.get(Approval, first).status == "EXPIRED"


def test_cancel(lab, client):  # type: ignore[no-untyped-def]
    reviewer = Member(client, lab, "reviewer")
    approval_id, _ = request(lab)
    r = reviewer.post(f"/api/v1/approvals/{approval_id}/cancel", json={"reason": "not mine"})
    assert r.status_code == 403  # neither requester nor admin
    r = lab.post(f"/api/v1/approvals/{approval_id}/cancel", json={"reason": "no longer needed"})
    assert r.status_code == 200 and r.json()["status"] == "CANCELLED"
    assert lab.post(f"/api/v1/approvals/{approval_id}/cancel").status_code == 409


def test_list_filters_and_tenancy(lab, other_lab):  # type: ignore[no-untyped-def]
    approval_id, _ = request(lab)
    request(lab, action="tool.invoke", subject_type="tool_call")
    listed = lab.get("/api/v1/approvals?status=pending").json()
    assert listed["meta"]["total"] == 2
    assert lab.get("/api/v1/approvals?action=tool.invoke").json()["meta"]["total"] == 1
    assert lab.get(f"/api/v1/approvals?project_id={lab.project_id}").json()["meta"]["total"] == 2
    assert lab.get("/api/v1/approvals?status=bogus").status_code == 422
    assert other_lab.get(f"/api/v1/approvals/{approval_id}").status_code == 404
    assert other_lab.get("/api/v1/approvals").json()["meta"]["total"] == 0
    r = other_lab.post(f"/api/v1/approvals/{approval_id}/decision", json={"approve": True, "reason": "cross tenant"})
    assert r.status_code == 404
    assert (
        lab.post(f"/api/v1/approvals/{approval_id}/decision", json={"approve": True, "reason": "x"}).status_code == 422
    )


def test_decision_signals_workflow(lab, client, monkeypatch):  # type: ignore[no-untyped-def]
    calls: list[tuple[Any, ...]] = []
    fake = types.ModuleType("aegis_api.lab.workflows.launcher")
    fake.signal_workflow = lambda db, run_id, name, payload: calls.append((run_id, name, payload))  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "aegis_api.lab.workflows.launcher", fake)
    run_id = uuid.uuid4()
    approval_id, _ = request(lab, workflow_run_id=run_id, signal_name="plan_approved")
    reviewer = Member(client, lab, "reviewer")
    assert decide(reviewer, approval_id).status_code == 200
    assert calls == [
        (
            run_id,
            "plan_approved",
            {"approval_id": str(approval_id), "approved": True, "reason": "Looks safe to run", "status": "APPROVED"},
        )
    ]
