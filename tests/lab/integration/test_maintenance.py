"""Retention purge (per-organization policy) against real PostgreSQL: what is purged, what is never purged,
auditing, tenant isolation and idempotency."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, text

from tests.conftest import requires_db

pytestmark = [requires_db, pytest.mark.db]


def _now():  # type: ignore[no-untyped-def]
    from aegis_api.db.base import utcnow

    return utcnow()


@pytest.fixture
def storage(tmp_path) -> Iterator[Any]:  # type: ignore[no-untyped-def]
    from aegis_api.lab.storage import configure_storage
    from aegis_api.lab.storage.local import LocalFilesystemStorage

    instance = LocalFilesystemStorage(str(tmp_path / "objects"))
    configure_storage(instance)
    yield instance
    configure_storage(None)


def set_retention(lab, **values: Any) -> None:  # type: ignore[no-untyped-def]
    from aegis_api.lab.core.org_settings import get_org_settings

    with lab.db() as db:
        settings = get_org_settings(db, lab.org_id)
        settings.retention = {**(settings.retention or {}), **values}


def purge(lab) -> dict[str, int]:  # type: ignore[no-untyped-def]
    from aegis_api.lab.workflows.maintenance import retention_purge

    return retention_purge(lab.org_id)


def make_agent_run(db, lab):  # type: ignore[no-untyped-def]
    from aegis_api.lab.models import Agent, AgentRun, AgentVersion

    agent = Agent(organization_id=lab.org_id, role="LiteratureAgent", name=f"lit-{uuid.uuid4().hex[:8]}")
    db.add(agent)
    db.flush()
    version = AgentVersion(
        organization_id=lab.org_id,
        agent_id=agent.id,
        version=1,
        prompt_key="literature.synthesize",
        config_hash="0" * 64,
    )
    db.add(version)
    db.flush()
    run = AgentRun(
        organization_id=lab.org_id,
        workspace_id=lab.workspace_id,
        project_id=lab.project_id,
        agent_id=agent.id,
        agent_version_id=version.id,
        role="LiteratureAgent",
    )
    db.add(run)
    db.flush()
    return run


def add_steps(db, lab, run, seqs: range, created_at) -> None:  # type: ignore[no-untyped-def]
    from aegis_api.lab.models import AgentStep

    for seq in seqs:
        db.add(
            AgentStep(
                organization_id=lab.org_id,
                agent_run_id=run.id,
                seq=seq,
                kind="llm_call",
                summary=f"step {seq}",
                created_at=created_at,
            )
        )
    db.flush()


def make_claim(db, lab):  # type: ignore[no-untyped-def]
    from aegis_api.lab.models import ScientificClaim

    claim = ScientificClaim(
        organization_id=lab.org_id,
        workspace_id=lab.workspace_id,
        project_id=lab.project_id,
        statement="Method A outperforms B",
        source_type="human",
    )
    db.add(claim)
    db.flush()
    return claim


def count(lab, model, *where) -> int:  # type: ignore[no-untyped-def]
    with lab.db() as db:
        return int(
            db.scalar(select(func.count()).select_from(model).where(model.organization_id == lab.org_id, *where))
        )


# ---------------------------------------------------------------------------------------------------
def test_purge_removes_old_agent_steps_but_never_evidence(lab, other_lab) -> None:  # type: ignore[no-untyped-def]
    from aegis_api.lab.core.evidence import append_evidence, verify_lab_chain
    from aegis_api.lab.models import AgentStep, ClaimEvidence
    from aegis_api.models import AuditLog, Evidence

    set_retention(lab, agent_logs_days=30)
    old, recent = _now() - timedelta(days=400), _now() - timedelta(days=2)
    with lab.db() as db:
        run = make_agent_run(db, lab)
        cited_run = make_agent_run(db, lab)
        add_steps(db, lab, run, range(1, 4), old)
        add_steps(db, lab, run, range(4, 6), recent)
        add_steps(db, lab, cited_run, range(1, 3), old)
        claim = make_claim(db, lab)
        db.add(
            ClaimEvidence(organization_id=lab.org_id, claim_id=claim.id, evidence_type="agent_run", ref_id=cited_run.id)
        )
        for i in range(3):
            append_evidence(db, organization_id=lab.org_id, kind="test", title=f"evidence {i}", content={"i": i})
        run_id, cited_id = run.id, cited_run.id
    with other_lab.db() as db:
        other_run = make_agent_run(db, other_lab)
        add_steps(db, other_lab, other_run, range(1, 3), old)
        other_run_id = other_run.id
    evidence_before = count(lab, Evidence)

    counts = purge(lab)
    assert counts["agent_steps"] == 3
    assert count(lab, AgentStep, AgentStep.agent_run_id == run_id) == 2  # recent steps kept
    assert count(lab, AgentStep, AgentStep.agent_run_id == cited_id) == 2  # cited as claim evidence: kept
    assert count(other_lab, AgentStep, AgentStep.agent_run_id == other_run_id) == 2  # other tenant untouched
    # Evidence is never purged and its hash chain stays valid.
    assert count(lab, Evidence) == evidence_before
    with lab.db() as db:
        assert verify_lab_chain(db, lab.org_id)["valid"]
        entries = db.scalars(
            select(AuditLog).where(
                AuditLog.organization_id == lab.org_id,
                AuditLog.action == "RETENTION_PURGE",
                AuditLog.resource_type == "agent_steps",
            )
        ).all()
    assert [e.after["deleted"] for e in entries] == [3]
    assert entries[0].actor_type == "system"
    # Idempotent: nothing left to purge.
    assert purge(lab)["agent_steps"] == 0


def test_append_only_switch_does_not_leak_outside_the_purge_transaction(lab) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy.exc import DBAPIError

    set_retention(lab, agent_logs_days=30)
    with lab.db() as db:
        run = make_agent_run(db, lab)
        add_steps(db, lab, run, range(1, 3), _now() - timedelta(days=100))
        add_steps(db, lab, run, range(3, 4), _now())
        run_id = run.id
    assert purge(lab)["agent_steps"] == 2
    with pytest.raises(DBAPIError), lab.db() as db:
        db.execute(text("delete from agent_steps where agent_run_id = :id"), {"id": run_id})


def test_events_purge_respects_consumer_offsets_and_minimum_age(lab) -> None:  # type: ignore[no-untyped-def]
    from aegis_api.lab.models import EventConsumerOffset, LabEvent, ResearchEvent, ResearchTask

    set_retention(lab, research_events_days=10)
    with lab.db() as db:
        events = [
            LabEvent(
                organization_id=lab.org_id, type="TEST_EVENT", payload={"n": n}, created_at=_now() - timedelta(days=60)
            )
            for n in range(3)
        ]
        db.add_all(events)
        db.flush()
        recent = LabEvent(
            organization_id=lab.org_id, type="TEST_EVENT", payload={}, created_at=_now() - timedelta(days=20)
        )
        db.add(recent)
        db.flush()
        db.add(EventConsumerOffset(consumer="test-consumer", organization_id=lab.org_id, last_event_id=events[1].id))
        task = ResearchTask(
            organization_id=lab.org_id,
            workspace_id=lab.workspace_id,
            project_id=lab.project_id,
            kind="literature_search",
            title="t",
            query="q",
        )
        db.add(task)
        db.flush()
        db.add_all(
            [
                ResearchEvent(
                    organization_id=lab.org_id,
                    research_task_id=task.id,
                    seq=1,
                    type="progress",
                    created_at=_now() - timedelta(days=15),
                ),
                ResearchEvent(organization_id=lab.org_id, research_task_id=task.id, seq=2, type="progress"),
            ]
        )
        ids = [e.id for e in events]
        recent_id = recent.id
    counts = purge(lab)
    # Old AND consumed by every tracking consumer: the first two. The third is past retention but unconsumed;
    # the 20-day-old event is within the 30-day minimum.
    assert counts["events"] == 2
    remaining = {e for e in [*ids, recent_id] if count(lab, LabEvent, LabEvent.id == e) == 1}
    assert remaining == {ids[2], recent_id}
    assert counts["research_events"] == 1


def _artifact(db, lab, storage, *, created_at, retention_class="standard", legal_hold=False):  # type: ignore[no-untyped-def]
    from aegis_api.lab.models import Artifact, ArtifactVersion
    from aegis_api.lab.storage.keys import object_key

    artifact = Artifact(
        organization_id=lab.org_id,
        workspace_id=lab.workspace_id,
        project_id=lab.project_id,
        kind="log",
        name=f"artifact-{uuid.uuid4().hex[:6]}",
        retention_class=retention_class,
        legal_hold=legal_hold,
        created_at=created_at,
    )
    db.add(artifact)
    db.flush()
    data = f"payload for {artifact.id}".encode()
    key = object_key(lab.org_id, lab.project_id, "artifacts", str(artifact.id), "v1", "out.txt")
    storage.put_bytes(key, data, "text/plain")
    version = ArtifactVersion(
        organization_id=lab.org_id,
        artifact_id=artifact.id,
        project_id=lab.project_id,
        version=1,
        storage_key=key,
        size_bytes=len(data),
        checksum=hashlib.sha256(data).hexdigest(),
        mime_type="text/plain",
    )
    db.add(version)
    db.flush()
    artifact.current_version_id = version.id
    return artifact, version


def test_artifact_purge_keeps_referenced_and_protected_artifacts(lab, storage) -> None:  # type: ignore[no-untyped-def]
    from aegis_api.lab.models import Artifact, ClaimEvidence, Discovery, StorageUsage

    set_retention(lab, artifacts_days=30, raw_outputs_days=7)
    old, fresh = _now() - timedelta(days=100), _now() - timedelta(days=3)
    with lab.db() as db:
        purge_std, v_std = _artifact(db, lab, storage, created_at=old)
        purge_eph, v_eph = _artifact(
            db, lab, storage, created_at=_now() - timedelta(days=10), retention_class="ephemeral"
        )
        keep_fresh, v_fresh = _artifact(db, lab, storage, created_at=fresh)
        keep_evidence, v_evidence = _artifact(db, lab, storage, created_at=old, retention_class="evidence")
        keep_hold, v_hold = _artifact(db, lab, storage, created_at=old, legal_hold=True)
        keep_cited, v_cited = _artifact(db, lab, storage, created_at=old)
        keep_discovery, v_discovery = _artifact(db, lab, storage, created_at=old)
        claim = make_claim(db, lab)
        db.add(
            ClaimEvidence(
                organization_id=lab.org_id, claim_id=claim.id, evidence_type="artifact_version", ref_id=v_cited.id
            )
        )
        db.add(
            Discovery(
                organization_id=lab.org_id,
                workspace_id=lab.workspace_id,
                project_id=lab.project_id,
                claim_id=claim.id,
                title="A discovery",
                evidence_ids=[str(v_discovery.id)],
            )
        )
        purged = {purge_std.id: v_std, purge_eph.id: v_eph}
        kept = {
            keep_fresh.id: v_fresh,
            keep_evidence.id: v_evidence,
            keep_hold.id: v_hold,
            keep_cited.id: v_cited,
            keep_discovery.id: v_discovery,
        }
        sizes = sum(v.size_bytes for v in purged.values())
    counts = purge(lab)
    assert (counts["artifacts"], counts["artifact_objects"], counts["artifact_bytes"]) == (2, 2, sizes)
    with lab.db() as db:
        for artifact_id, version in purged.items():
            assert db.get(Artifact, artifact_id).deleted_at is not None
            assert not storage.exists(version.storage_key)
        for artifact_id, version in kept.items():
            assert db.get(Artifact, artifact_id).deleted_at is None
            assert storage.exists(version.storage_key)
        reclaimed = db.scalars(
            select(StorageUsage.bytes_delta).where(
                StorageUsage.organization_id == lab.org_id, StorageUsage.reason == "purge"
            )
        ).all()
    assert sorted(reclaimed) == sorted(-v.size_bytes for v in purged.values())
    assert purge(lab)["artifacts"] == 0


def test_model_usage_purged_only_for_finalized_periods(lab) -> None:  # type: ignore[no-untyped-def]
    from aegis_api.lab.models import ModelUsage, UsageRecord

    set_retention(lab, llm_metadata_days=30)
    now = _now()

    def usage(days_ago: int) -> Any:
        return ModelUsage(
            organization_id=lab.org_id,
            provider="test",
            model="test-model",
            task_type="unit",
            created_at=now - timedelta(days=days_ago),
        )

    with lab.db() as db:
        finalized_row, open_row, uncovered_row = usage(400), usage(200), usage(300)
        db.add_all([finalized_row, open_row, uncovered_row])
        db.add_all(
            [
                UsageRecord(
                    organization_id=lab.org_id,
                    meter="llm_cost",
                    period_start=now - timedelta(days=410),
                    period_end=now - timedelta(days=380),
                    finalized=True,
                ),
                UsageRecord(
                    organization_id=lab.org_id,
                    meter="llm_cost",
                    period_start=now - timedelta(days=210),
                    period_end=now - timedelta(days=190),
                    finalized=False,
                ),
            ]
        )
        db.flush()
        ids = (finalized_row.id, open_row.id, uncovered_row.id)
    assert purge(lab)["model_usage"] == 1
    assert count(lab, ModelUsage, ModelUsage.id == ids[0]) == 0
    assert count(lab, ModelUsage, ModelUsage.id == ids[1]) == 1
    assert count(lab, ModelUsage, ModelUsage.id == ids[2]) == 1


def test_workflow_history_of_finished_runs_is_purged_but_failed_runs_keep_it(lab) -> None:  # type: ignore[no-untyped-def]
    from aegis_api.lab.models import WorkflowRun, WorkflowStep
    from aegis_api.lab.workflows import runs

    set_retention(lab, agent_logs_days=30)
    old = _now() - timedelta(days=100)

    def finished_run(db: Any, status: str) -> uuid.UUID:
        run, _ = runs.create_run(
            db,
            organization_id=lab.org_id,
            kind="ExecutionJobWorkflow",
            subject_type="test",
            subject_id=str(uuid.uuid4()),
            workflow_key="default",
            flow_input={},
            actor_payload_for=lambda rid: runs.workflow_actor_payload(lab.actor(), rid),
            engine="local",
            task_queue="local",
            project_id=lab.project_id,
            mission_id=None,
            parent_workflow_run_id=None,
            created_by_id=None,
        )
        run.status = status
        run.completed_at = old
        db.add(
            WorkflowStep(
                organization_id=lab.org_id, workflow_run_id=run.id, step_key="x#1", activity="x", status="completed"
            )
        )
        db.flush()
        return run.id

    with lab.db() as db:
        done = finished_run(db, "COMPLETED")
        failed = finished_run(db, "FAILED")
    assert purge(lab)["workflow_steps"] == 1
    assert count(lab, WorkflowStep, WorkflowStep.workflow_run_id == done) == 0
    assert count(lab, WorkflowStep, WorkflowStep.workflow_run_id == failed) == 1
    assert count(lab, WorkflowRun, WorkflowRun.id == done) == 1  # the run record itself is kept


def test_audit_logs_follow_audit_logs_days_only_when_set(lab) -> None:  # type: ignore[no-untyped-def]
    from aegis_api.models import AuditLog

    with lab.db() as db:
        entry = AuditLog(
            organization_id=lab.org_id,
            actor_type="system",
            action="TEST_OLD_ACTION",
            resource_type="test",
            created_at=_now() - timedelta(days=500),
        )
        db.add(entry)
        db.flush()
        entry_id = entry.id
    assert purge(lab)["audit_logs"] == 0  # default: keep audit logs
    assert count(lab, AuditLog, AuditLog.id == entry_id) == 1
    set_retention(lab, audit_logs_days=365)
    assert purge(lab)["audit_logs"] >= 1
    assert count(lab, AuditLog, AuditLog.id == entry_id) == 0
    assert count(lab, AuditLog, AuditLog.action == "RETENTION_PURGE") >= 1


def test_retention_days_parsing() -> None:
    from aegis_api.lab.workflows.maintenance import retention_days

    policy = {"a": 30, "b": None, "c": "15", "d": 0, "e": -5, "f": "x", "g": True}
    assert [retention_days(policy, k) for k in "abcdefgh"] == [30, None, 15, None, None, None, None, None]


def test_maintenance_activities_require_a_platform_actor(lab) -> None:  # type: ignore[no-untyped-def]
    from aegis_api.errors import Forbidden
    from aegis_api.lab.core.actor import Actor
    from aegis_api.lab.workflows import activities  # noqa: F401  (registers workflows.*)
    from aegis_api.lab.workflows.registry import ACTIVITIES, ActivityContext

    purge_activity = ACTIVITIES["workflows.retention_purge"].fn
    result = purge_activity(ActivityContext(actor=Actor.system(lab.org_id)), {})
    assert set(result["counts"]) >= {"agent_steps", "events", "artifacts", "model_usage"}
    with pytest.raises(Forbidden):
        purge_activity(ActivityContext(actor=lab.actor()), {})
    resume = ACTIVITIES["workflows.resume_stale"].fn
    assert "started" in resume(ActivityContext(actor=Actor.system(lab.org_id)), {})["counts"]
    with pytest.raises(Forbidden):
        resume(ActivityContext(actor=lab.actor(role="viewer")), {})
