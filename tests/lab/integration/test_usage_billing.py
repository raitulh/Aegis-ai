"""Usage aggregation over the ledgers (seeded through the usage recorder) and the billing abstraction."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select, update

from tests.conftest import requires_db

pytestmark = [requires_db, pytest.mark.db]


def seed_plans() -> None:
    from aegis_api.db.session import session_factory
    from aegis_api.lab.usage.billing import seed_billing_plans

    session = session_factory(admin=True)()
    try:
        seed_billing_plans(session)
        session.commit()
    finally:
        session.close()


def enable_billing(lab) -> None:  # type: ignore[no-untyped-def]
    from aegis_api.db.session import session_factory
    from aegis_api.models import FeatureFlag

    session = session_factory(admin=True)()
    try:
        session.add(FeatureFlag(organization_id=lab.org_id, key="billing", enabled=True))
        session.commit()
    finally:
        session.close()


def api_headers(lab, role: str) -> dict[str, str]:  # type: ignore[no-untyped-def]
    key = lab.post("/api/v1/api-keys", json={"name": f"k-{uuid.uuid4().hex[:6]}", "role": role, "scopes": ["read"]})
    return {"Authorization": f"Bearer {key.json()['plaintext']}"}


def seed_usage(lab) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    """Two missions, agent runs, compute jobs, tool invocations, storage and a discovery."""
    from aegis_api.lab.models import (
        Agent,
        AgentRun,
        AgentVersion,
        ComputeJob,
        Discovery,
        Experiment,
        Mission,
        ScientificClaim,
        ToolInvocation,
    )
    from aegis_api.lab.usage.recorder import record_compute_usage, record_model_usage, record_storage_usage

    ids: dict[str, Any] = {}
    with lab.db() as db:
        scope = {"organization_id": lab.org_id, "workspace_id": lab.workspace_id, "project_id": lab.project_id}
        m1 = Mission(**scope, title="Alpha", objective="a", status="RUNNING")
        m2 = Mission(**scope, title="Beta", objective="b", status="RUNNING")
        db.add_all([m1, m2])
        db.flush()
        agent = Agent(organization_id=lab.org_id, role="LiteratureAgent", name=f"lit-{uuid.uuid4().hex[:6]}")
        db.add(agent)
        db.flush()
        version = AgentVersion(
            organization_id=lab.org_id, agent_id=agent.id, version=1, prompt_key="lit", config_hash="0" * 64
        )
        db.add(version)
        db.flush()
        run = AgentRun(
            **scope, mission_id=m1.id, agent_id=agent.id, agent_version_id=version.id, role="LiteratureAgent"
        )
        db.add(run)
        db.flush()
        exp = Experiment(**scope, mission_id=m1.id, title="Exp 1")
        db.add(exp)
        db.flush()

        def llm(cost: str, mission, agent_run=None, model="gemini-pro", success=True, tokens=(100, 50)):  # type: ignore[no-untyped-def]
            record_model_usage(
                db,
                organization_id=lab.org_id,
                provider="gemini",
                model=model,
                task_type="analysis",
                input_tokens=tokens[0],
                output_tokens=tokens[1],
                cost_usd=Decimal(cost),
                success=success,
                project_id=lab.project_id,
                mission_id=mission.id if mission else None,
                agent_run_id=agent_run.id if agent_run else None,
            )

        llm("1.50", m1, run)
        llm("0.50", m1, run, model="gemini-flash")
        llm("2.00", m2)
        llm("0.25", None, success=False, tokens=(10, 0))

        jobs = []
        for mission, cost, experiment in ((m1, "3.00", exp), (m2, "1.00", None)):
            job = ComputeJob(
                **scope,
                mission_id=mission.id,
                experiment_id=experiment.id if experiment else None,
                backend="local_docker",
                image="python:3.12-slim",
                timeout_seconds=60,
                idempotency_key=uuid.uuid4().hex,
                status="SUCCEEDED",
                purpose="experiment",
            )
            db.add(job)
            db.flush()
            jobs.append(job)
            record_compute_usage(
                db,
                organization_id=lab.org_id,
                compute_job_id=job.id,
                backend="local_docker",
                cpu_seconds=120.0,
                wall_seconds=60.0,
                cost_usd=Decimal(cost),
                project_id=lab.project_id,
                mission_id=mission.id,
                experiment_id=experiment.id if experiment else None,
            )
        for name, status, cost in (
            ("web_search", "succeeded", "0.10"),
            ("web_search", "failed", "0"),
            ("mcp_x", "denied", "0"),
        ):
            db.add(
                ToolInvocation(
                    organization_id=lab.org_id,
                    project_id=lab.project_id,
                    mission_id=m1.id,
                    agent_run_id=run.id,
                    tool_name=name,
                    tool_source="mcp" if name.startswith("mcp") else "builtin",
                    input_hash="0" * 64,
                    status=status,
                    cost_usd=Decimal(cost),
                    latency_ms=100,
                )
            )
        record_storage_usage(db, organization_id=lab.org_id, bytes_delta=2 * 1024**3, reason="artifact_upload")
        claim = ScientificClaim(**scope, mission_id=m1.id, statement="X improves Y", source_type="human")
        db.add(claim)
        db.flush()
        discovery = Discovery(
            **scope, mission_id=m1.id, claim_id=claim.id, title="Finding", experiment_ids=[str(exp.id)]
        )
        db.add(discovery)
        db.flush()
        ids.update(m1=m1.id, m2=m2.id, run=run.id, exp=exp.id, discovery=discovery.id, jobs=[j.id for j in jobs])
    return ids


def test_summary(lab):  # type: ignore[no-untyped-def]
    ids = seed_usage(lab)
    body = lab.get("/api/v1/usage/summary").json()
    assert body["llm"] == {
        "cost_usd": 4.25,
        "calls": 4,
        "failed_calls": 1,
        "input_tokens": 310,
        "output_tokens": 150,
        "cached_tokens": 0,
        "thinking_tokens": 0,
    }
    assert body["compute"]["cost_usd"] == 4.0 and body["compute"]["jobs"] == 2
    assert body["compute"]["cpu_seconds"] == 240.0
    assert body["tools"] == {"cost_usd": 0.1, "invocations": 3}
    assert body["storage"]["bytes_stored"] == 2 * 1024**3
    assert body["total_cost_usd"] == pytest.approx(8.35) and body["currency"] == "USD"
    # mission counters were incremented atomically by the recorder
    from aegis_api.lab.models import Mission

    with lab.db() as db:
        m1 = db.get(Mission, ids["m1"])
        assert m1.spent_llm_usd == Decimal("2.0") and m1.spent_compute_usd == Decimal("3.0")
    # an empty past period
    empty = lab.get("/api/v1/usage/summary", params={"since": "2020-01-01T00:00:00Z", "until": "2020-02-01T00:00:00Z"})
    assert empty.json()["total_cost_usd"] == 0
    assert (
        lab.get(
            "/api/v1/usage/summary", params={"since": "2020-02-01T00:00:00Z", "until": "2020-01-01T00:00:00Z"}
        ).status_code
        == 422
    )


def test_cost_breakdowns(lab):  # type: ignore[no-untyped-def]
    ids = seed_usage(lab)

    def costs(group_by: str) -> dict[Any, dict[str, Any]]:
        r = lab.get("/api/v1/usage/costs", params={"group_by": group_by})
        assert r.status_code == 200, r.text
        return {item["key"]: item for item in r.json()["items"]}

    missions = costs("mission")
    alpha = missions[str(ids["m1"])]
    assert alpha["label"] == "Alpha" and alpha["llm_usd"] == 2.0 and alpha["compute_usd"] == 3.0
    assert alpha["tool_usd"] == 0.1 and alpha["total_usd"] == pytest.approx(5.1) and alpha["calls"] == 2
    assert missions[str(ids["m2"])]["total_usd"] == 3.0
    assert missions[None]["label"] == "No mission" and missions[None]["llm_usd"] == 0.25

    models = costs("model")
    assert models["gemini:gemini-pro"]["llm_usd"] == pytest.approx(3.75) and models["gemini:gemini-pro"]["calls"] == 3
    assert models["gemini:gemini-pro"]["details"]["failed_calls"] == 1
    assert models["gemini:gemini-flash"]["tokens"] == 150

    agents = costs("agent")
    assert agents["LiteratureAgent"]["llm_usd"] == 2.0 and agents["LiteratureAgent"]["tool_usd"] == 0.1
    assert agents[None]["llm_usd"] == 2.25

    experiments = costs("experiment")
    assert experiments[str(ids["exp"])]["compute_usd"] == 3.0 and experiments[None]["compute_usd"] == 1.0

    projects = costs("project")
    assert projects[str(lab.project_id)]["total_usd"] == pytest.approx(8.35)

    discoveries = costs("discovery")
    finding = discoveries[str(ids["discovery"])]
    assert finding["compute_usd"] == 3.0 and finding["llm_usd"] == 2.0 and finding["tool_usd"] == 0.1
    assert finding["details"]["mission_cost_share"] == "1/1"

    org = lab.get("/api/v1/usage/costs", params={"group_by": "organization"}).json()
    assert org["total_usd"] == pytest.approx(8.35) and len(org["items"]) == 1
    assert lab.get("/api/v1/usage/costs", params={"group_by": "planet"}).status_code == 422
    limited = lab.get("/api/v1/usage/costs", params={"group_by": "mission", "limit": 1}).json()
    assert len(limited["items"]) == 1 and limited["total_usd"] == pytest.approx(8.35)


def test_ledger_listings_and_tools(lab, other_lab):  # type: ignore[no-untyped-def]
    ids = seed_usage(lab)
    first = lab.get("/api/v1/usage/models", params={"limit": 3}).json()
    assert len(first["items"]) == 3 and first["next_cursor"]
    second = lab.get("/api/v1/usage/models", params={"limit": 3, "cursor": first["next_cursor"]}).json()
    assert len(second["items"]) == 1 and second["next_cursor"] is None
    seen = {i["id"] for i in first["items"]} | {i["id"] for i in second["items"]}
    assert len(seen) == 4
    filtered = lab.get("/api/v1/usage/models", params={"mission_id": str(ids["m1"])}).json()
    assert {i["mission_id"] for i in filtered["items"]} == {str(ids["m1"])}
    assert len(lab.get("/api/v1/usage/models", params={"success": "false"}).json()["items"]) == 1
    assert lab.get("/api/v1/usage/models", params={"cursor": "garbage!"}).status_code == 422

    compute = lab.get("/api/v1/usage/compute").json()
    assert len(compute["items"]) == 2 and compute["items"][0]["backend"] == "local_docker"
    by_exp = lab.get("/api/v1/usage/compute", params={"experiment_id": str(ids["exp"])}).json()
    assert len(by_exp["items"]) == 1 and by_exp["items"][0]["cost_usd"] == 3.0

    tools = lab.get("/api/v1/usage/tools").json()
    web = next(i for i in tools["items"] if i["tool_name"] == "web_search")
    assert web == {
        "tool_name": "web_search",
        "tool_source": "builtin",
        "invocations": 2,
        "succeeded": 1,
        "failed": 1,
        "denied": 0,
        "cost_usd": 0.1,
        "avg_latency_ms": 100.0,
    }
    assert tools["total_usd"] == 0.1

    # tenancy: the other organization sees nothing
    assert other_lab.get("/api/v1/usage/summary").json()["total_cost_usd"] == 0
    assert other_lab.get("/api/v1/usage/models").json()["items"] == []
    assert other_lab.get("/api/v1/usage/summary", params={"project_id": str(lab.project_id)}).status_code == 404


def human_member(client, lab, role: str):  # type: ignore[no-untyped-def]
    """A second human in the lab organization (selects it with the X-Aegis-Org header)."""
    from aegis_api.db.session import session_factory
    from aegis_api.models import Membership
    from tests.conftest import signup

    ws = signup(client, org=f"Home {uuid.uuid4().hex[:6]}")
    session = session_factory(admin=True)()
    try:
        session.add(Membership(organization_id=lab.org_id, user_id=uuid.UUID(ws.user_id), role=role))
        session.commit()
    finally:
        session.close()
    return ws, {"X-Aegis-Org": str(lab.org_id)}


def test_usage_permissions(lab, client):  # type: ignore[no-untyped-def]
    viewer = api_headers(lab, "viewer")
    assert client.get("/api/v1/usage/summary", headers=viewer).status_code == 200  # usage:read
    assert client.get("/api/v1/billing/subscription", headers=viewer).status_code == 403  # billing:view
    billing_ws, headers = human_member(client, lab, "billing_admin")
    assert billing_ws.get("/api/v1/billing/subscription", headers=headers).status_code == 200
    assert billing_ws.get("/api/v1/usage/costs", headers=headers).status_code == 200
    assert billing_ws.get("/api/v1/governance/policies", headers=headers).status_code == 403
    viewer_ws, headers = human_member(client, lab, "viewer")
    assert viewer_ws.get("/api/v1/billing/invoices", headers=headers).status_code == 403
    r = viewer_ws.post("/api/v1/billing/invoices", headers=headers, json={})
    assert r.status_code == 403


def test_rollup_is_idempotent_and_respects_finalized(lab):  # type: ignore[no-untyped-def]
    from aegis_api.lab.governance.activities import rollup_usage
    from aegis_api.lab.models import UsageRecord
    from aegis_api.lab.usage.billing import METERS, rollup_usage_records
    from aegis_api.lab.usage.service import month_bounds
    from aegis_api.lab.workflows.registry import ActivityContext

    seed_plans()
    seed_usage(lab)
    start, end = month_bounds()
    with lab.db() as db:
        first = {r.meter: (r.id, r.quantity, r.cost_usd) for r in rollup_usage_records(db, lab.org_id, start, end)}
    assert set(first) == set(METERS)
    assert first["llm_cost_usd"][1] == Decimal("4.25") and first["compute_cost_usd"][2] == Decimal("4.00")
    assert first["experiments"][1] == 2 and first["model_tokens"][1] == 460
    assert first["storage_gb_month"][1] > 0

    result = rollup_usage(ActivityContext(actor=lab.actor()), {"period": start.strftime("%Y-%m")})
    assert result["records"] == len(METERS)
    with lab.db() as db:
        records = db.scalars(select(UsageRecord).where(UsageRecord.period_start == start)).all()
        assert len(records) == len(METERS)
        assert {r.meter: r.id for r in records} == {m: v[0] for m, v in first.items()}  # upserted in place
        from aegis_api.lab.usage.recorder import record_model_usage

        record_model_usage(db, organization_id=lab.org_id, provider="gemini", model="m", task_type="t", cost_usd=1)
        db.execute(update(UsageRecord).where(UsageRecord.meter == "compute_cost_usd").values(finalized=True))
    with lab.db() as db:
        refreshed = {r.meter: r for r in rollup_usage_records(db, lab.org_id, start, end)}
        assert refreshed["llm_cost_usd"].quantity == Decimal("5.25")
        assert refreshed["compute_cost_usd"].finalized and refreshed["compute_cost_usd"].quantity == Decimal("4.00")


def test_billing_plans_subscription_and_feature_gate(lab):  # type: ignore[no-untyped-def]
    from aegis_api.db.session import session_factory
    from aegis_api.lab.models import BillingPlan
    from aegis_api.lab.usage.billing import seed_billing_plans

    seed_plans()
    session = session_factory(admin=True)()
    try:
        assert seed_billing_plans(session) == 3  # idempotent
        session.commit()
        keys = set(
            session.scalars(select(BillingPlan.key).where(BillingPlan.key.in_(("free", "team", "enterprise")))).all()
        )
    finally:
        session.close()
    assert keys == {"free", "team", "enterprise"}
    plans = {p["key"]: p for p in lab.get("/api/v1/billing/plans").json()}
    assert plans["free"]["quotas"]["max_concurrent_experiments"] == 1

    sub = lab.get("/api/v1/billing/subscription").json()
    assert sub["is_default"] is True and sub["plan_key"] == "free" and sub["plan"]["key"] == "free"

    r = lab.ws.request("PUT", "/api/v1/billing/subscription", json={"plan_key": "team"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "feature_disabled"
    assert lab.post("/api/v1/billing/rollup", json={}).status_code == 403
    enable_billing(lab)
    r = lab.ws.request("PUT", "/api/v1/billing/subscription", json={"plan_key": "team"})
    assert r.status_code == 200, r.text
    assert r.json()["plan_key"] == "team" and r.json()["is_default"] is False
    r = lab.ws.request("PUT", "/api/v1/billing/subscription", json={"plan_key": "enterprise"})
    assert r.json()["plan_key"] == "enterprise"
    assert lab.get("/api/v1/billing/subscription").json()["plan_key"] == "enterprise"
    assert lab.ws.request("PUT", "/api/v1/billing/subscription", json={"plan_key": "platinum"}).status_code == 404
    from aegis_api.lab.models import Subscription

    with lab.db() as db:
        statuses = sorted(db.scalars(select(Subscription.status)).all())
    assert statuses == ["active", "cancelled"]


def test_rollup_endpoint_records_and_invoices(lab):  # type: ignore[no-untyped-def]
    seed_plans()
    seed_usage(lab)
    enable_billing(lab)
    now = datetime.now(UTC)
    period = now.strftime("%Y-%m")
    r = lab.post("/api/v1/billing/rollup", json={"period": period})
    assert r.status_code == 200, r.text
    assert len(r.json()["records"]) == 6
    assert lab.post("/api/v1/billing/rollup", json={"period": "2026-13"}).status_code == 422
    records = lab.get("/api/v1/billing/usage-records", params={"period": period}).json()
    assert records["meta"]["total"] == 6
    assert lab.get("/api/v1/billing/usage-records", params={"meter": "llm_cost_usd"}).json()["meta"]["total"] == 1

    headers = {"Idempotency-Key": f"invoice-{uuid.uuid4().hex}"}
    r = lab.post("/api/v1/billing/invoices", json={"period": period}, headers=headers)
    assert r.status_code == 201, r.text
    invoice = r.json()
    assert invoice["status"] == "draft" and invoice["provider"] == "none"
    assert invoice["amount_usd"] == pytest.approx(8.25)  # llm 4.25 + compute 4.00; free plan has no fees
    replay = lab.post("/api/v1/billing/invoices", json={"period": period}, headers=headers)
    assert replay.headers.get("Idempotent-Replayed") == "true" and replay.json()["id"] == invoice["id"]
    again = lab.post("/api/v1/billing/invoices", json={"period": period})
    assert again.json()["id"] == invoice["id"]  # one invoice per period
    listed = lab.get("/api/v1/billing/invoices").json()
    assert listed["meta"]["total"] == 1
    future = (now.replace(day=1) + timedelta(days=62)).strftime("%Y-%m")
    assert lab.post("/api/v1/billing/invoices", json={"period": future}).status_code == 422


def test_invoice_for_finished_period_finalizes_records(lab):  # type: ignore[no-untyped-def]
    from aegis_api.lab.models import UsageRecord

    seed_plans()
    enable_billing(lab)
    r = lab.post("/api/v1/billing/invoices", json={"period": "2026-01"})
    assert r.status_code == 201 and r.json()["amount_usd"] == 0
    with lab.db() as db:
        records = db.scalars(
            select(UsageRecord).where(UsageRecord.period_start == datetime(2026, 1, 1, tzinfo=UTC))
        ).all()
    assert records and all(rec.finalized for rec in records)
    assert {r.external_ref for r in records} == {
        lab.get("/api/v1/billing/invoices").json()["items"][0]["external_invoice_id"]
    }
