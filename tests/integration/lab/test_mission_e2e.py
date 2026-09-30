"""End-to-end research loop through the HTTP API with real PostgreSQL, the real Docker sandbox and the
platform-owned evaluation harness (the model is scripted; mocks are test-only).

mission → plan → hypotheses → critique → selection → experiment (12 sandboxed runs) → harness metrics →
statistics → claims → verification with reproductions → discovery (human review, separation of duties) →
report → lineage → reproducibility package.
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile

import pytest

from tests.integration.lab.conftest import drive_until_idle, invite, requires_docker

pytestmark = [requires_docker, pytest.mark.docker, pytest.mark.e2e]


def test_full_research_loop(client, workspace, lab):
    from aegis_api.services.lab import seed

    ids = seed.seed_lab_demo(uuid.UUID(workspace.org_id), uuid.UUID(workspace.user_id))
    mission_id = ids["mission_id"]

    # Human configures the mission: skip literature (no network in CI) and raise autonomy to L3.
    mission = workspace.get(f"/api/v1/missions/{mission_id}").json()
    r = workspace.patch(
        f"/api/v1/missions/{mission_id}",
        json={"config": {**mission["config"], "skip_research": True}},
        headers={"If-Match": str(mission["lock_version"])},
    )
    assert r.status_code == 200, r.text
    r = workspace.patch(f"/api/v1/missions/{mission_id}", json={"title": "stale"}, headers={"If-Match": "1"})
    assert r.status_code == 409  # optimistic concurrency
    r = workspace.post(
        f"/api/v1/missions/{mission_id}/autonomy",
        json={"autonomy_level": "L3_AUTOMATED_EXECUTION", "reason": "sandboxed demo"},
    )
    assert r.status_code == 200 and r.json()["autonomy_level"] == "L3_AUTOMATED_EXECUTION"

    key = f"launch-{uuid.uuid4().hex}"
    r = workspace.post(f"/api/v1/missions/{mission_id}/launch", headers={"Idempotency-Key": key})
    assert r.status_code == 202, r.text
    first = r.json()
    replay = workspace.post(f"/api/v1/missions/{mission_id}/launch", headers={"Idempotency-Key": key})
    assert replay.status_code == 202 and replay.json() == first
    assert replay.headers.get("Idempotent-Replayed") == "true"

    drive_until_idle(workspace.org_id)

    mission = workspace.get(f"/api/v1/missions/{mission_id}").json()
    assert mission["status"] == "completed", mission.get("status_reason")
    assert mission["phase"] == "done"

    events = workspace.get(f"/api/v1/missions/{mission_id}/events?limit=1000").json()
    types = [e["event_type"] for e in events]
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs), "event ids must be gap-free and increasing"
    for expected in (
        "MISSION_STARTED",
        "AGENT_STARTED",
        "HYPOTHESIS_CREATED",
        "EXPERIMENT_QUEUED",
        "EXPERIMENT_STARTED",
        "EXPERIMENT_COMPLETED",
        "EVALUATION_COMPLETED",
        "VERIFICATION_STARTED",
        "VERIFICATION_COMPLETED",
        "DISCOVERY_CREATED",
        "APPROVAL_REQUESTED",
        "REPORT_GENERATED",
        "MISSION_COMPLETED",
    ):
        assert expected in types, f"missing {expected}"

    # SSE replays from Last-Event-ID and terminates for a finished mission.
    with client.stream(
        "GET",
        f"/api/v1/missions/{mission_id}/events/stream",
        cookies=workspace.cookies,
        headers={"Last-Event-ID": str(seqs[-5])},
    ) as stream:
        body = "".join(chunk for chunk in stream.iter_text())
    streamed_ids = [int(line[4:]) for line in body.splitlines() if line.startswith("id: ")]
    assert streamed_ids == seqs[-4:]
    assert "event: stream_end" in body

    # Measurements came from the harness, not from the code under test.
    exps = workspace.get(f"/api/v1/experiments?mission_id={mission_id}").json()["items"]
    assert len(exps) == 1
    exp_id = exps[0]["id"]
    runs = workspace.get(f"/api/v1/experiments/{exp_id}/runs").json()
    completed = [r for r in runs if r["status"] == "completed"]
    assert len(completed) >= 10
    assert all(not r["self_reported"] and "objective_value" in r["metrics"] for r in completed)
    assert all(r["resources"].get("backend") == "docker" for r in completed)

    comparisons = workspace.get(f"/api/v1/experiments/{exp_id}/comparisons").json()
    assert any(c["metric"] == "objective_value" and c["p_adjusted"] is not None for c in comparisons)

    claims = workspace.get(f"/api/v1/claims?mission_id={mission_id}").json()["items"]
    verified = [c for c in claims if c["status"] == "verified"]
    assert verified, [c["status"] for c in claims]
    claim_id = verified[0]["id"]

    lineage = workspace.get(f"/api/v1/claims/{claim_id}/lineage").json()
    node_types = {n["type"] for n in lineage["nodes"]}
    assert {
        "claim",
        "evidence",
        "verification",
        "evaluation",
        "experiment",
        "experiment_run",
        "code_snapshot",
        "environment",
        "raw_artifact",
    } <= node_types

    chain = workspace.get(f"/api/v1/missions/{mission_id}/evidence/verify").json()
    assert chain["valid"] is True and chain["records"] > 10

    # Discovery: pending human review; the launcher cannot approve their own mission's discovery.
    discoveries = workspace.get(f"/api/v1/discoveries?mission_id={mission_id}").json()["items"]
    assert len(discoveries) == 1 and discoveries[0]["status"] == "human_review"
    disc_id = discoveries[0]["id"]
    denied = workspace.post(f"/api/v1/discoveries/{disc_id}/review", json={"approve": True, "reason": "self"})
    assert denied.status_code == 403
    reviewer = invite(client, workspace, "reviewer")
    ok = reviewer.post(f"/api/v1/discoveries/{disc_id}/review", json={"approve": True, "reason": "independent review"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "approved"
    drive_until_idle(workspace.org_id)  # the DiscoveryWorkflow observes the decision and completes

    reports = workspace.get(f"/api/v1/research-reports?mission_id={mission_id}").json()["items"]
    assert reports and reports[0]["citation_check"]["narrative"] in ("accepted", "rejected")
    assert reports[0]["cited_evidence_ids"]

    package = workspace.get(f"/api/v1/experiments/{exp_id}/reproducibility-package")
    assert package.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(package.content)).namelist()
    assert {"spec.json", "metrics.csv", "evaluations.json", "README.md", "code/main.py"} <= set(names)

    usage = workspace.get(f"/api/v1/usage?mission_id={mission_id}").json()
    assert usage["spend"]["llm_tokens"] > 0 and usage["spend"]["compute_seconds"] > 0

    obs = workspace.get(f"/api/v1/missions/{mission_id}/observability").json()
    assert obs["agents"]["runs"] >= 5 and obs["workflow"]["status"] == "completed"

    # Every agent prompt kept untrusted content fenced and never offered tools a role may not use.
    roles_with_tools = {r.metadata.get("role") for r in lab.provider.requests if r.tools}
    assert roles_with_tools <= {
        "planner",
        "literature",
        "knowledge",
        "hypothesis",
        "hypothesis_critic",
        "experiment_designer",
        "failure_analyzer",
        "evolution",
    }
    assert all("SYSTEM POLICY" in (r.system or "") for r in lab.provider.requests)
    json.dumps(lineage)  # machine-readable
