"""Mission budgets (check, enforce + pause, thresholds, project ceilings, view) and organization quotas."""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select

from tests.conftest import requires_db

pytestmark = [requires_db, pytest.mark.db]


def make_mission(lab, **fields: Any) -> uuid.UUID:  # type: ignore[no-untyped-def]
    from aegis_api.db.base import utcnow
    from aegis_api.lab.models import Mission

    values: dict[str, Any] = {"status": "RUNNING", "started_at": utcnow(), "autonomy_level": "L3_AUTOMATED_EXECUTION"}
    values.update(fields)
    with lab.db() as db:
        mission = Mission(
            organization_id=lab.org_id,
            workspace_id=lab.workspace_id,
            project_id=lab.project_id,
            title="Budgeted mission",
            objective="Stay within budget",
            **values,
        )
        db.add(mission)
        db.flush()
        return mission.id


def spend(lab, mission_id, **amounts: Any) -> None:  # type: ignore[no-untyped-def]
    from aegis_api.lab.usage.recorder import increment_mission_spend

    with lab.db() as db:
        increment_mission_spend(db, mission_id, **amounts)


def events(lab, type_: str, mission_id=None):  # type: ignore[no-untyped-def]
    from aegis_api.lab.models import LabEvent

    with lab.db() as db:
        stmt = select(LabEvent).where(LabEvent.type == type_)
        if mission_id is not None:
            stmt = stmt.where(LabEvent.mission_id == mission_id)
        return db.scalars(stmt.order_by(LabEvent.id)).all()


def make_job(db, lab, status: str = "QUEUED"):  # type: ignore[no-untyped-def]
    from aegis_api.lab.models import ComputeJob

    job = ComputeJob(
        organization_id=lab.org_id,
        workspace_id=lab.workspace_id,
        project_id=lab.project_id,
        backend="local_docker",
        image="python:3.12-slim",
        timeout_seconds=60,
        idempotency_key=uuid.uuid4().hex,
        status=status,
    )
    db.add(job)
    db.flush()
    return job


# ---------------------------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------------------------
def test_check_budget(lab):  # type: ignore[no-untyped-def]
    from aegis_api.lab.governance.budgets import check_budget
    from aegis_api.lab.models import Mission

    mission_id = make_mission(lab, budget={"max_total_cost_usd": 10, "max_llm_cost_usd": 6, "max_experiment_count": 2})
    spend(lab, mission_id, llm_usd=Decimal("4"), compute_usd=Decimal("3"), experiments=1)
    with lab.db() as db:
        mission = db.get(Mission, mission_id)  # counters are read fresh regardless of the instance state
        ok = check_budget(db, mission, "llm", estimated_usd=1)
        assert ok.ok and ok.remaining_usd == Decimal("2") and ok.reason is None
        over = check_budget(db, mission, "llm", estimated_usd=Decimal("2.5"))
        assert not over.ok and over.dimension == "llm" and "would be exceeded" in (over.reason or "")
        total = check_budget(db, mission, "compute", estimated_usd=4)
        assert not total.ok and total.dimension == "total"
        assert check_budget(db, mission, "experiment", count=1).ok
        assert not check_budget(db, mission, "experiment", count=2).ok
        assert check_budget(db, mission, "research", count=5).ok  # unlimited
    with lab.db() as db:
        from aegis_api.errors import ValidationFailed

        with pytest.raises(ValidationFailed):
            check_budget(db, db.get(Mission, mission_id), "gpu")


def test_invalid_budget_fails_closed(lab):  # type: ignore[no-untyped-def]
    from aegis_api.lab.governance.budgets import check_budget
    from aegis_api.lab.models import Mission

    mission_id = make_mission(lab, budget={"max_total_cost_usd": "lots"})
    with lab.db() as db:
        check = check_budget(db, db.get(Mission, mission_id), "llm")
    assert not check.ok and (check.reason or "").startswith("invalid_budget_configuration")


def test_time_budget(lab):  # type: ignore[no-untyped-def]
    from aegis_api.db.base import utcnow
    from aegis_api.lab.governance.budgets import check_budget
    from aegis_api.lab.models import Mission

    mission_id = make_mission(lab, time_budget_seconds=60, started_at=utcnow() - timedelta(minutes=5))
    with lab.db() as db:
        check = check_budget(db, db.get(Mission, mission_id), "llm")
    assert not check.ok and check.dimension == "time"


def test_enforce_pauses_running_mission(lab):  # type: ignore[no-untyped-def]
    from aegis_api.lab.core.errors import BudgetExceeded
    from aegis_api.lab.governance.budgets import enforce_budget
    from aegis_api.lab.models import Mission
    from aegis_api.models import AuditLog

    mission_id = make_mission(lab, budget={"max_compute_cost_usd": 5})
    spend(lab, mission_id, compute_usd=Decimal("4.5"))
    actor = lab.actor()
    with lab.db() as db:
        mission = db.get(Mission, mission_id)
        with pytest.raises(BudgetExceeded) as exc:
            enforce_budget(db, actor, mission, "compute", estimated_usd=1)
        assert mission.status == "PAUSED"  # the caller's instance reflects the pause
    assert exc.value.details["dimension"] == "compute" and exc.value.status_code == 409
    with lab.db() as db:
        mission = db.get(Mission, mission_id)
        assert mission.status == "PAUSED" and mission.status_reason == "budget_exceeded:compute"
        assert mission.paused_at is not None
        audits = db.scalars(select(AuditLog).where(AuditLog.action == "MISSION_PAUSED")).all()
    exceeded = events(lab, "BUDGET_EXCEEDED", mission_id)
    assert len(exceeded) == 1 and exceeded[0].payload["mission_paused"] is True
    assert exceeded[0].payload["limit"] == "5"
    assert len(events(lab, "MISSION_PAUSED", mission_id)) == 1 and len(audits) == 1

    # enforcing again on the paused mission raises but does not duplicate the event
    with lab.db() as db, pytest.raises(BudgetExceeded):
        enforce_budget(db, actor, db.get(Mission, mission_id), "compute", estimated_usd=1)
    assert len(events(lab, "BUDGET_EXCEEDED", mission_id)) == 1
    assert len(events(lab, "MISSION_PAUSED", mission_id)) == 1


def test_pause_survives_caller_rollback(lab):  # type: ignore[no-untyped-def]
    from aegis_api.lab.core.errors import BudgetExceeded
    from aegis_api.lab.governance.budgets import enforce_budget
    from aegis_api.lab.models import Mission

    mission_id = make_mission(lab, budget={"max_llm_cost_usd": 1})
    spend(lab, mission_id, llm_usd=Decimal("1"))
    with pytest.raises(BudgetExceeded), lab.db() as db:  # the exception propagates → rollback
        enforce_budget(db, lab.actor(), db.get(Mission, mission_id), "llm")
    with lab.db() as db:
        mission = db.get(Mission, mission_id)
        assert mission.status == "PAUSED" and mission.status_reason == "budget_exceeded:llm"
    assert len(events(lab, "BUDGET_EXCEEDED", mission_id)) == 1
    assert len(events(lab, "MISSION_PAUSED", mission_id)) == 1


def test_enforce_ok_and_threshold_warning_once(lab):  # type: ignore[no-untyped-def]
    from aegis_api.lab.governance.budgets import enforce_budget
    from aegis_api.lab.models import Mission

    mission_id = make_mission(lab, budget={"max_llm_cost_usd": 10})
    spend(lab, mission_id, llm_usd=Decimal("8.5"))
    for _ in range(2):
        with lab.db() as db:
            check = enforce_budget(db, lab.actor(), db.get(Mission, mission_id), "llm", estimated_usd=1)
            assert check.ok and check.warnings == ("llm",)
    warnings = events(lab, "BUDGET_THRESHOLD", mission_id)
    assert len(warnings) == 1 and warnings[0].payload["dimension"] == "llm"
    with lab.db() as db:
        assert db.get(Mission, mission_id).status == "RUNNING"


def test_enforce_on_non_running_mission_does_not_transition(lab):  # type: ignore[no-untyped-def]
    from aegis_api.lab.core.errors import BudgetExceeded
    from aegis_api.lab.governance.budgets import enforce_budget
    from aegis_api.lab.models import Mission

    mission_id = make_mission(lab, status="APPROVED", budget={"max_research_tasks": 0})
    with lab.db() as db, pytest.raises(BudgetExceeded):
        enforce_budget(db, lab.actor(), db.get(Mission, mission_id), "research", count=1)
    with lab.db() as db:
        assert db.get(Mission, mission_id).status == "APPROVED"
    assert len(events(lab, "BUDGET_EXCEEDED", mission_id)) == 1
    assert events(lab, "MISSION_PAUSED", mission_id) == []


def test_project_budget_ceiling_and_aggregate(lab):  # type: ignore[no-untyped-def]
    from aegis_api.lab.governance.budgets import check_budget
    from aegis_api.lab.models import Mission, Project
    from aegis_api.lab.usage.recorder import record_model_usage

    with lab.db() as db:
        db.get(Project, lab.project_id).budget = {"max_total_cost_usd": 20, "max_llm_cost_usd": 3}
    mission_id = make_mission(lab, budget={"max_llm_cost_usd": 50})
    with lab.db() as db:
        check = check_budget(db, db.get(Mission, mission_id), "llm", estimated_usd=4)
        assert not check.ok and check.dimension == "llm"  # mission limit tightened to the project's 3 USD
        # project spend from another mission / no mission counts against the project ceiling
        record_model_usage(
            db,
            organization_id=lab.org_id,
            provider="gemini",
            model="m",
            task_type="t",
            cost_usd=19,
            project_id=lab.project_id,
        )
    with lab.db() as db:
        check = check_budget(db, db.get(Mission, mission_id), "compute", estimated_usd=2)
        assert not check.ok and check.scope == "project" and check.dimension == "project_total"
        assert check_budget(db, db.get(Mission, mission_id), "compute", estimated_usd=1).ok


def test_budget_view_endpoint(lab, other_lab):  # type: ignore[no-untyped-def]
    mission_id = make_mission(lab, budget={"max_total_cost_usd": 10, "max_llm_cost_usd": 5}, time_budget_seconds=3600)
    spend(lab, mission_id, llm_usd=Decimal("4.5"), compute_usd=Decimal("1"))
    r = lab.get(f"/api/v1/budgets/missions/{mission_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    dims = {d["dimension"]: d for d in body["dimensions"]}
    assert dims["llm"]["status"] == "warning" and dims["llm"]["remaining"] == 0.5
    assert dims["total"]["spent"] == 5.5 and dims["compute"]["status"] == "unlimited"
    assert body["ok"] is True and body["warnings"] == ["llm"] and body["mission_status"] == "RUNNING"
    assert body["time_budget_seconds"] == 3600 and body["time_exceeded"] is False
    assert other_lab.get(f"/api/v1/budgets/missions/{mission_id}").status_code == 404
    assert lab.get(f"/api/v1/budgets/missions/{uuid.uuid4()}").status_code == 404


# ---------------------------------------------------------------------------------------------
# Quotas
# ---------------------------------------------------------------------------------------------
def test_max_concurrent_experiments_quota(lab):  # type: ignore[no-untyped-def]
    from aegis_api.lab.core.errors import QuotaExceeded
    from aegis_api.lab.core.org_settings import get_org_settings
    from aegis_api.lab.governance.quotas import check_quota

    with lab.db() as db:
        get_org_settings(db, lab.org_id).quotas = {"max_concurrent_experiments": 2}
        for status in ("QUEUED", "RUNNING", "SUCCEEDED"):
            make_job(db, lab, status)
    with lab.db() as db:
        status = check_quota(db, lab.org_id, "max_concurrent_experiments", increment=0)  # exactly at the limit
        assert status.current == 2 and status.limit == 2 and status.remaining == 0
    with lab.db() as db, pytest.raises(QuotaExceeded) as exc:
        check_quota(db, lab.org_id, "max_concurrent_experiments", increment=1)
    assert exc.value.status_code == 403 and exc.value.code == "quota_exceeded"
    assert exc.value.details["key"] == "max_concurrent_experiments"
    assert exc.value.details["limit"] == 2 and exc.value.details["current"] == 2


def test_spend_storage_and_count_quotas(lab):  # type: ignore[no-untyped-def]
    from aegis_api.lab.core.errors import QuotaExceeded
    from aegis_api.lab.core.org_settings import get_org_settings
    from aegis_api.lab.governance.quotas import check_quota, current_usage
    from aegis_api.lab.usage.recorder import record_model_usage, record_storage_usage

    with lab.db() as db:
        get_org_settings(db, lab.org_id).quotas = {
            "max_llm_spend_usd": 10,
            "max_storage_bytes": 1000,
            "max_missions": 1,
        }
        record_model_usage(db, organization_id=lab.org_id, provider="gemini", model="m", task_type="t", cost_usd=9.5)
        record_storage_usage(db, organization_id=lab.org_id, bytes_delta=900, reason="artifact_upload")
        record_storage_usage(db, organization_id=lab.org_id, bytes_delta=-100, reason="purge")
    make_mission(lab)
    make_mission(lab, status="ARCHIVED")
    with lab.db() as db:
        assert current_usage(db, lab.org_id, "max_llm_spend_usd") == Decimal("9.5")
        assert current_usage(db, lab.org_id, "max_storage_bytes") == 800
        assert current_usage(db, lab.org_id, "max_missions") == 1
        check_quota(db, lab.org_id, "max_llm_spend_usd", increment=Decimal("0.5"))
        with pytest.raises(QuotaExceeded):
            check_quota(db, lab.org_id, "max_llm_spend_usd", increment=1)
        with pytest.raises(QuotaExceeded):
            check_quota(db, lab.org_id, "max_storage_bytes", increment=201)
        with pytest.raises(QuotaExceeded):
            check_quota(db, lab.org_id, "max_missions")
        from aegis_api.errors import ValidationFailed

        with pytest.raises(ValidationFailed):
            check_quota(db, lab.org_id, "max_unicorns")
        with pytest.raises(ValidationFailed):
            check_quota(db, lab.org_id, "max_missions", increment=-1)


def test_plan_quota_is_more_restrictive(lab):  # type: ignore[no-untyped-def]
    from aegis_api.db.session import session_factory
    from aegis_api.lab.core.errors import QuotaExceeded
    from aegis_api.lab.governance.quotas import check_quota, month_start
    from aegis_api.lab.models import Subscription
    from aegis_api.lab.usage.billing import seed_billing_plans

    session = session_factory(admin=True)()
    try:
        seed_billing_plans(session)
        session.commit()
    finally:
        session.close()
    with lab.db() as db:
        start = month_start()
        db.add(
            Subscription(
                organization_id=lab.org_id,
                plan_key="free",
                current_period_start=start,
                current_period_end=start + timedelta(days=31),
                quotas_override={"max_research_jobs": 2},
            )
        )
        make_job(db, lab)
    with lab.db() as db, pytest.raises(QuotaExceeded) as exc:  # free plan: 1 concurrent experiment
        check_quota(db, lab.org_id, "max_concurrent_experiments")
    assert exc.value.details["limit"] == 1
    body = lab.get("/api/v1/quotas").json()
    items = {i["key"]: i for i in body["items"]}
    assert body["plan_key"] == "free"
    assert items["max_concurrent_experiments"]["limit"] == 1
    assert items["max_concurrent_experiments"]["sources"] == {"organization": 4.0, "plan": 1.0, "override": None}
    assert items["max_research_jobs"]["limit"] == 2


def test_quota_api(lab, client):  # type: ignore[no-untyped-def]
    from aegis_api.models import AuditLog

    body = lab.get("/api/v1/quotas").json()
    keys = {i["key"] for i in body["items"]}
    assert keys == {
        "max_agents",
        "max_concurrent_experiments",
        "max_llm_spend_usd",
        "max_compute_spend_usd",
        "max_storage_bytes",
        "max_research_jobs",
        "max_missions",
    }
    r = lab.ws.request("PUT", "/api/v1/quotas", json={"quotas": {"max_missions": 3, "max_llm_spend_usd": 12.5}})
    assert r.status_code == 200, r.text
    items = {i["key"]: i for i in r.json()["items"]}
    assert items["max_missions"]["limit"] == 3 and items["max_llm_spend_usd"]["limit"] == 12.5
    for bad in ({"max_missions": -1}, {"max_missions": 1.5}, {"max_unicorns": 1}, {}):
        r = lab.ws.request("PUT", "/api/v1/quotas", json={"quotas": bad})
        assert r.status_code == 422, bad
    r = lab.ws.request("PUT", "/api/v1/quotas", json={"quotas": {"max_missions": None}})
    assert {i["key"]: i for i in r.json()["items"]}["max_missions"]["limit"] == 100  # default restored
    with lab.db() as db:
        changes = db.scalars(select(AuditLog).where(AuditLog.action == "QUOTA_CHANGED")).all()
    assert len(changes) == 2
    key = lab.post("/api/v1/api-keys", json={"name": "v", "role": "viewer", "scopes": ["read"]}).json()["plaintext"]
    headers = {"Authorization": f"Bearer {key}"}
    assert client.get("/api/v1/quotas", headers=headers).status_code == 200
    assert client.put("/api/v1/quotas", headers=headers, json={"quotas": {"max_missions": 1000}}).status_code == 403
