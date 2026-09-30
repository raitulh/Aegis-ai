"""Platform maintenance: durable-workflow recovery and per-organization retention purges.

Workflow recovery (run by the scheduler every few seconds, per organization, in tenant sessions):

* :func:`start_pending_runs` — (re)start PENDING runs whose start was lost (engine down, crash after commit).
* :func:`resume_stale_runs` — local engine: re-dispatch RUNNING/WAITING runs whose executor stopped
  heartbeating (they replay their recorded steps and continue); cancel orphaned inline children.
  Temporal engine: reconcile runs whose execution closed without its final status being recorded.
* :func:`deliver_pending_signals` — Temporal signal outbox re-delivery and cancellation re-delivery.

Retention (:func:`retention_purge`, daily) follows ``organization_settings.retention``. It never touches
``evidence``, ``experiment_versions``, ``dataset_versions``, ``claim_evidence`` or ``discovery_versions``;
audit logs are purged only when ``audit_logs_days`` is set. Deletes from append-only tables run in their own
short transactions with ``SET LOCAL aegis.allow_evidence_delete = on`` scoped to that transaction, and every
purge batch writes an audit entry (counts only) in the same transaction.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import and_, delete, exists, or_, select, text, update
from sqlalchemy.dialects.postgresql import array as pg_array
from sqlalchemy.orm import Session, aliased

from aegis_api.config import get_settings
from aegis_api.db.base import utcnow
from aegis_api.db.session import admin_session_scope
from aegis_api.errors import ServiceUnavailable
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.audit import audit
from aegis_api.lab.core.deps import tenant_uow
from aegis_api.lab.core.org_settings import DEFAULT_RETENTION, get_org_settings
from aegis_api.lab.models import (
    AgentStep,
    Artifact,
    ArtifactVersion,
    ClaimEvidence,
    Discovery,
    EventConsumerOffset,
    Experiment,
    ExperimentRun,
    LabEvent,
    ModelUsage,
    ResearchEvent,
    UsageRecord,
    WorkflowRun,
    WorkflowStep,
)
from aegis_api.lab.workflows import runs
from aegis_api.models import AuditLog, Organization
from engines.lab.states import ExecutionStatus, ExperimentStatus, WorkflowStatus

log = structlog.get_logger("aegis.lab.maintenance")

W = WorkflowStatus

PENDING_START_GRACE_SECONDS = 30
RECOVERY_BATCH = 100
CANCEL_REDELIVERY_SECONDS = 60
PURGE_BATCH = 1000
PURGE_MAX_BATCHES = 100
MIN_EVENT_RETENTION_DAYS = 30
MIN_WORKFLOW_STEP_RETENTION_DAYS = 30
RETENTION_AUDIT_ACTION = "RETENTION_PURGE"
PURGE_REASON = "purge"


# ---------------------------------------------------------------------------------------------------
# Organizations (owner session; ids only)
# ---------------------------------------------------------------------------------------------------
def organization_ids() -> list[uuid.UUID]:
    with admin_session_scope() as db:
        return list(db.scalars(select(Organization.id).order_by(Organization.id)))


def organization_ids_where(stmt: Any) -> list[uuid.UUID]:
    """Distinct organization ids selected by ``stmt`` (a single-column select), owner session."""
    with admin_session_scope() as db:
        return sorted(set(db.scalars(stmt)))


def organization_ids_with_open_workflows() -> list[uuid.UUID]:
    return organization_ids_where(
        select(WorkflowRun.organization_id).where(WorkflowRun.status.notin_(tuple(runs.TERMINAL_STATUSES))).distinct()
    )


# ---------------------------------------------------------------------------------------------------
# Workflow recovery
# ---------------------------------------------------------------------------------------------------
def _cancel_orphan(organization_id: uuid.UUID, run_id: uuid.UUID) -> bool:
    with tenant_uow(organization_id) as db:
        run = runs.lock_run(db, run_id)
        if run is None or runs.is_terminal(run.status):
            return False
        run.cancel_requested = True
        runs.transition(
            db, run, W.CANCELLED, error="Parent workflow ended before this child finished", reason="orphaned"
        )
        return True


def start_pending_runs(
    organization_id: uuid.UUID, *, older_than_seconds: float = PENDING_START_GRACE_SECONDS, limit: int = RECOVERY_BATCH
) -> dict[str, int]:
    """Start PENDING runs untouched for ``older_than_seconds`` (both engines).

    Children that their (live) parent starts itself — local inline children and Temporal children — are
    skipped; inline children of a finished parent are orphans and are cancelled.
    """
    from aegis_api.lab.workflows.launcher import start_run

    cutoff = utcnow() - timedelta(seconds=older_than_seconds)
    parent = aliased(WorkflowRun)
    with tenant_uow(organization_id) as db:
        rows = db.execute(
            select(
                WorkflowRun.id,
                WorkflowRun.engine,
                WorkflowRun.task_queue,
                WorkflowRun.parent_workflow_run_id,
                parent.status,
            )
            .outerjoin(parent, parent.id == WorkflowRun.parent_workflow_run_id)
            .where(
                WorkflowRun.organization_id == organization_id,
                WorkflowRun.status == W.PENDING,
                WorkflowRun.updated_at < cutoff,
            )
            .order_by(WorkflowRun.updated_at)
            .limit(limit)
        ).all()
    counts = {"started": 0, "skipped": 0, "orphans_cancelled": 0}
    for run_id, engine, task_queue, parent_id, parent_status in rows:
        parent_open = parent_id is not None and parent_status is not None and not runs.is_terminal(parent_status)
        inline_child = parent_id is not None and (
            engine == runs.ENGINE_TEMPORAL or task_queue == runs.LOCAL_INLINE_QUEUE
        )
        if inline_child and parent_open:
            counts["skipped"] += 1
            continue
        if parent_id is not None and task_queue == runs.LOCAL_INLINE_QUEUE:
            counts["orphans_cancelled"] += int(_cancel_orphan(organization_id, run_id))
            continue
        start_run(organization_id, run_id)
        counts["started"] += 1
    return counts


def resume_stale_runs(organization_id: uuid.UUID, *, limit: int = RECOVERY_BATCH) -> dict[str, int]:
    """Re-dispatch local runs whose executor died; reconcile Temporal runs that stopped reporting."""
    from aegis_api.lab.workflows.local_engine import dispatch_local

    stale_after = get_settings().workflow_stale_after_seconds
    cutoff = utcnow() - timedelta(seconds=stale_after)
    parent = aliased(WorkflowRun)
    with tenant_uow(organization_id) as db:
        rows = db.execute(
            select(
                WorkflowRun.id,
                WorkflowRun.engine,
                WorkflowRun.task_queue,
                WorkflowRun.parent_workflow_run_id,
                parent.status,
            )
            .outerjoin(parent, parent.id == WorkflowRun.parent_workflow_run_id)
            .where(
                WorkflowRun.organization_id == organization_id,
                WorkflowRun.status.in_(tuple(runs.ACTIVE_STATUSES)),
                or_(WorkflowRun.heartbeat_at.is_(None), WorkflowRun.heartbeat_at < cutoff),
            )
            .order_by(WorkflowRun.heartbeat_at.nulls_first())
            .limit(limit)
        ).all()
    counts = {"resumed": 0, "orphans_cancelled": 0, "reconciled": 0, "skipped": 0}
    temporal_available = True
    for run_id, engine, task_queue, parent_id, parent_status in rows:
        if engine == runs.ENGINE_LOCAL:
            if task_queue == runs.LOCAL_INLINE_QUEUE and parent_id is not None:
                if parent_status is None or runs.is_terminal(parent_status):
                    counts["orphans_cancelled"] += int(_cancel_orphan(organization_id, run_id))
                else:
                    counts["skipped"] += 1  # resumed through its parent's replay
                continue
            dispatch_local(organization_id, run_id)
            counts["resumed"] += 1
            continue
        if not temporal_available:
            counts["skipped"] += 1
            continue
        try:
            counts["reconciled"] += int(reconcile_temporal_run(organization_id, run_id))
        except ServiceUnavailable:
            temporal_available = False
            counts["skipped"] += 1
        except Exception:
            log.warning("temporal_reconcile_failed", workflow_run_id=str(run_id), exc_info=True)
            counts["skipped"] += 1
    if counts["resumed"] or counts["reconciled"] or counts["orphans_cancelled"]:
        log.info("workflows_recovered", organization_id=str(organization_id), **counts)
    return counts


_TEMPORAL_TERMINAL: dict[str, tuple[str, str | None]] = {
    "FAILED": (W.FAILED, "Temporal execution failed"),
    "TERMINATED": (W.FAILED, "Temporal execution was terminated"),
    "TIMED_OUT": (W.TIMED_OUT, "Temporal execution timed out"),
    "CANCELED": (W.CANCELLED, "Temporal execution was cancelled"),
    "NOT_FOUND": (W.FAILED, "Temporal execution not found"),
}


def reconcile_temporal_run(organization_id: uuid.UUID, workflow_run_id: uuid.UUID) -> bool:
    """Align a RUNNING/WAITING Temporal run with Temporal's view; True when its status changed."""
    from aegis_api.lab.workflows.temporal_engine import get_bridge

    with tenant_uow(organization_id) as db:
        run = db.get(WorkflowRun, workflow_run_id)
        if run is None or run.engine != runs.ENGINE_TEMPORAL or run.status not in runs.ACTIVE_STATUSES:
            return False
        temporal_id = runs.temporal_workflow_id(run)
    bridge = get_bridge()
    state = str(bridge.describe(temporal_id).get("status"))
    result: dict[str, Any] | None = None
    if state == "COMPLETED":
        raw = bridge.result(temporal_id)
        result = dict(raw) if isinstance(raw, dict) else {}
    with tenant_uow(organization_id) as db:
        run = runs.lock_run(db, workflow_run_id)
        if run is None or run.status not in runs.ACTIVE_STATUSES:
            return False
        if state in ("RUNNING", "CONTINUED_AS_NEW"):
            run.heartbeat_at = utcnow()  # verified alive; check again after another stale period
            return False
        if state == "COMPLETED":
            if run.status == W.WAITING:
                runs.transition(db, run, W.RUNNING, reason="reconciled")
            runs.transition(db, run, W.COMPLETED, result=result or {}, reason="reconciled")
            return True
        target, message = _TEMPORAL_TERMINAL.get(state, (W.FAILED, f"Temporal execution status {state}"))
        runs.transition(db, run, target, error=message, reason="reconciled")
        return True


def deliver_pending_signals(organization_id: uuid.UUID, *, limit: int = RECOVERY_BATCH) -> dict[str, int]:
    """Temporal engine: re-deliver undelivered signals and re-send lost cancellation requests."""
    from aegis_api.lab.workflows.launcher import _cancel_temporal, deliver_signals

    cancel_cutoff = utcnow() - timedelta(seconds=CANCEL_REDELIVERY_SECONDS)
    with tenant_uow(organization_id) as db:
        base = select(WorkflowRun.id).where(
            WorkflowRun.organization_id == organization_id,
            WorkflowRun.engine == runs.ENGINE_TEMPORAL,
            WorkflowRun.status.notin_(tuple(runs.TERMINAL_STATUSES)),
        )
        with_signals = list(db.scalars(base.where(WorkflowRun.signals.contains([{"delivered": False}])).limit(limit)))
        cancels = db.execute(
            select(WorkflowRun.id, WorkflowRun.external_id)
            .where(
                WorkflowRun.organization_id == organization_id,
                WorkflowRun.engine == runs.ENGINE_TEMPORAL,
                WorkflowRun.status.in_(tuple(runs.ACTIVE_STATUSES)),
                WorkflowRun.cancel_requested.is_(True),
                WorkflowRun.updated_at < cancel_cutoff,
            )
            .limit(limit)
        ).all()
    counts = {"signals_delivered": 0, "cancels_resent": 0}
    for run_id in with_signals:
        counts["signals_delivered"] += deliver_signals(organization_id, run_id)
    for run_id, external_id in cancels:
        _cancel_temporal(f"{organization_id}:{external_id}", run_id)
        with tenant_uow(organization_id) as db:  # bump updated_at so the re-send is rate limited
            db.execute(
                update(WorkflowRun)
                .where(WorkflowRun.id == run_id)
                .values(updated_at=utcnow())
                .execution_options(synchronize_session=False)
            )
        counts["cancels_resent"] += 1
    return counts


# ---------------------------------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------------------------------
def retention_days(retention: dict[str, Any], key: str) -> int | None:
    """Positive integer days, or ``None`` (= keep indefinitely) for missing/invalid values."""
    value = retention.get(key)
    if value is None or isinstance(value, bool):
        return None
    try:
        days = int(value)
    except (TypeError, ValueError):
        return None
    return days if days >= 1 else None


def _rowcount(result: Any) -> int:
    return int(getattr(result, "rowcount", 0) or 0)


def _allow_append_only_delete(db: Session) -> None:
    """Transaction-scoped switch honoured by the append-only triggers (released at commit/rollback)."""
    db.execute(text("SET LOCAL aegis.allow_evidence_delete = on"))


def _purge_loop(
    organization_id: uuid.UUID,
    actor: Actor,
    table: str,
    cutoff: datetime,
    delete_batch: Callable[[Session, int], int],
    *,
    append_only: bool,
    batch_size: int,
    max_batches: int,
) -> int:
    total = 0
    for _ in range(max_batches):
        with tenant_uow(organization_id) as db:
            if append_only:
                _allow_append_only_delete(db)
            deleted = delete_batch(db, batch_size)
            if deleted:
                audit(
                    db,
                    actor,
                    RETENTION_AUDIT_ACTION,
                    table,
                    None,
                    after={"table": table, "deleted": deleted, "cutoff": cutoff.isoformat()},
                )
        total += deleted
        if deleted < batch_size:
            break
    return total


def retention_purge(
    organization_id: uuid.UUID,
    *,
    now: datetime | None = None,
    batch_size: int = PURGE_BATCH,
    max_batches: int = PURGE_MAX_BATCHES,
) -> dict[str, int]:
    """Apply the organization's retention policy; returns deleted/purged counts per data class."""
    now = now or utcnow()
    org = organization_id
    with tenant_uow(org) as db:
        retention = {**DEFAULT_RETENTION, **(get_org_settings(db, org).retention or {})}
    actor = Actor.system(org, label="retention")
    counts: dict[str, int] = {
        "agent_steps": 0,
        "research_events": 0,
        "events": 0,
        "artifacts": 0,
        "artifact_objects": 0,
        "artifact_bytes": 0,
        "model_usage": 0,
        "workflow_steps": 0,
        "audit_logs": 0,
    }

    def loop(table: str, cutoff: datetime, fn: Callable[[Session, int], int], *, append_only: bool) -> int:
        return _purge_loop(
            org, actor, table, cutoff, fn, append_only=append_only, batch_size=batch_size, max_batches=max_batches
        )

    if (days := retention_days(retention, "agent_logs_days")) is not None:
        cutoff = now - timedelta(days=days)
        counts["agent_steps"] = loop(
            "agent_steps", cutoff, lambda db, n: _delete_agent_steps(db, org, cutoff, n), append_only=True
        )
        step_cutoff = now - timedelta(days=max(days, MIN_WORKFLOW_STEP_RETENTION_DAYS))
        counts["workflow_steps"] = loop(
            "workflow_steps",
            step_cutoff,
            lambda db, n: _delete_workflow_steps(db, org, step_cutoff, n),
            append_only=False,
        )
    if (days := retention_days(retention, "research_events_days")) is not None:
        cutoff = now - timedelta(days=days)
        counts["research_events"] = loop(
            "research_events", cutoff, lambda db, n: _delete_research_events(db, org, cutoff, n), append_only=False
        )
        event_cutoff = now - timedelta(days=max(MIN_EVENT_RETENTION_DAYS, days))
        counts["events"] = loop(
            "events", event_cutoff, lambda db, n: _delete_events(db, org, event_cutoff, n), append_only=True
        )
    standard_days = retention_days(retention, "artifacts_days")
    ephemeral_days = retention_days(retention, "raw_outputs_days") or standard_days
    if standard_days is not None or ephemeral_days is not None:
        purged = _purge_artifacts(
            org,
            actor,
            standard_cutoff=now - timedelta(days=standard_days) if standard_days is not None else None,
            ephemeral_cutoff=now - timedelta(days=ephemeral_days) if ephemeral_days is not None else None,
            now=now,
            batch_size=min(batch_size, 200),
            max_batches=max_batches,
        )
        counts.update(purged)
    if (days := retention_days(retention, "llm_metadata_days")) is not None:
        cutoff = now - timedelta(days=days)
        counts["model_usage"] = loop(
            "model_usage", cutoff, lambda db, n: _delete_model_usage(db, org, cutoff, n), append_only=True
        )
    if (days := retention_days(retention, "audit_logs_days")) is not None:
        cutoff = now - timedelta(days=days)
        counts["audit_logs"] = loop(
            "audit_logs", cutoff, lambda db, n: _delete_audit_logs(db, org, cutoff, n), append_only=True
        )
    if any(counts.values()):
        log.info("retention_purge_completed", organization_id=str(org), **counts)
    return counts


def _delete_agent_steps(db: Session, org: uuid.UUID, cutoff: datetime, limit: int) -> int:
    # Steps of agent runs cited as claim evidence are kept (evidence needed for reproducibility).
    cited = select(ClaimEvidence.ref_id).where(
        ClaimEvidence.organization_id == org, ClaimEvidence.evidence_type == "agent_run"
    )
    ids = (
        select(AgentStep.id)
        .where(AgentStep.organization_id == org, AgentStep.created_at < cutoff, AgentStep.agent_run_id.notin_(cited))
        .limit(limit)
    )
    return _rowcount(
        db.execute(delete(AgentStep).where(AgentStep.id.in_(ids)).execution_options(synchronize_session=False))
    )


def _delete_research_events(db: Session, org: uuid.UUID, cutoff: datetime, limit: int) -> int:
    ids = (
        select(ResearchEvent.id)
        .where(ResearchEvent.organization_id == org, ResearchEvent.created_at < cutoff)
        .limit(limit)
    )
    return _rowcount(
        db.execute(delete(ResearchEvent).where(ResearchEvent.id.in_(ids)).execution_options(synchronize_session=False))
    )


def _delete_events(db: Session, org: uuid.UUID, cutoff: datetime, limit: int) -> int:
    """Outbox events past retention that every consumer tracking this organization has processed."""
    offsets = list(
        db.scalars(select(EventConsumerOffset.last_event_id).where(EventConsumerOffset.organization_id == org))
    )
    conditions = [LabEvent.organization_id == org, LabEvent.created_at < cutoff]
    if offsets:
        conditions.append(LabEvent.id <= min(offsets))
    ids = select(LabEvent.id).where(*conditions).order_by(LabEvent.id).limit(limit)
    return _rowcount(
        db.execute(delete(LabEvent).where(LabEvent.id.in_(ids)).execution_options(synchronize_session=False))
    )


def _delete_workflow_steps(db: Session, org: uuid.UUID, cutoff: datetime, limit: int) -> int:
    """Replay history of runs that finished successfully or were cancelled long ago (never of FAILED runs,
    which keep their steps so a retry can resume)."""
    finished = select(WorkflowRun.id).where(
        WorkflowRun.organization_id == org,
        WorkflowRun.status.in_((W.COMPLETED, W.CANCELLED)),
        WorkflowRun.completed_at < cutoff,
    )
    ids = (
        select(WorkflowStep.id)
        .where(WorkflowStep.organization_id == org, WorkflowStep.workflow_run_id.in_(finished))
        .limit(limit)
    )
    return _rowcount(
        db.execute(delete(WorkflowStep).where(WorkflowStep.id.in_(ids)).execution_options(synchronize_session=False))
    )


def _delete_model_usage(db: Session, org: uuid.UUID, cutoff: datetime, limit: int) -> int:
    """LLM call metadata past retention, only for periods whose usage records are all finalized."""
    covering = and_(
        UsageRecord.organization_id == org,
        UsageRecord.period_start <= ModelUsage.created_at,
        ModelUsage.created_at < UsageRecord.period_end,
    )
    finalized = exists().where(covering, UsageRecord.finalized.is_(True))
    still_open = exists().where(covering, UsageRecord.finalized.is_(False))
    ids = (
        select(ModelUsage.id)
        .where(ModelUsage.organization_id == org, ModelUsage.created_at < cutoff, finalized, ~still_open)
        .limit(limit)
    )
    return _rowcount(
        db.execute(delete(ModelUsage).where(ModelUsage.id.in_(ids)).execution_options(synchronize_session=False))
    )


def _delete_audit_logs(db: Session, org: uuid.UUID, cutoff: datetime, limit: int) -> int:
    ids = select(AuditLog.id).where(AuditLog.organization_id == org, AuditLog.created_at < cutoff).limit(limit)
    return _rowcount(
        db.execute(delete(AuditLog).where(AuditLog.id.in_(ids)).execution_options(synchronize_session=False))
    )


def _artifact_candidates(
    db: Session,
    org: uuid.UUID,
    standard_cutoff: datetime | None,
    ephemeral_cutoff: datetime | None,
    limit: int,
    exclude: set[uuid.UUID],
) -> list[tuple[Artifact, list[ArtifactVersion]]]:
    windows = []
    if standard_cutoff is not None:
        windows.append(and_(Artifact.retention_class == "standard", Artifact.created_at < standard_cutoff))
    if ephemeral_cutoff is not None:
        windows.append(and_(Artifact.retention_class == "ephemeral", Artifact.created_at < ephemeral_cutoff))
    if not windows:
        return []
    cited = exists().where(
        ClaimEvidence.organization_id == org,
        ClaimEvidence.evidence_type == "artifact_version",
        ClaimEvidence.ref_id == ArtifactVersion.id,
        ArtifactVersion.artifact_id == Artifact.id,
    )
    verified_experiments = select(Experiment.id).where(
        Experiment.organization_id == org, Experiment.status == ExperimentStatus.VERIFIED
    )
    verified = exists().where(
        ExperimentRun.id == Artifact.experiment_run_id,
        or_(ExperimentRun.status == ExecutionStatus.VERIFIED, ExperimentRun.experiment_id.in_(verified_experiments)),
    )
    stmt = (
        select(Artifact)
        .where(
            Artifact.organization_id == org,
            Artifact.deleted_at.is_(None),
            Artifact.legal_hold.is_(False),
            or_(*windows),
            ~cited,
            ~verified,
        )
        .order_by(Artifact.created_at, Artifact.id)
        .limit(limit + len(exclude))
    )
    artifacts = [a for a in db.scalars(stmt) if a.id not in exclude][:limit]
    if not artifacts:
        return []
    versions: dict[uuid.UUID, list[ArtifactVersion]] = {a.id: [] for a in artifacts}
    for version in db.scalars(select(ArtifactVersion).where(ArtifactVersion.artifact_id.in_(list(versions)))):
        versions[version.artifact_id].append(version)
    # Artifacts cited by discoveries (directly, through a version, or through their experiment) are kept.
    run_ids = [a.experiment_run_id for a in artifacts if a.experiment_run_id is not None]
    experiment_of_run: dict[uuid.UUID, uuid.UUID] = {}
    if run_ids:
        experiment_of_run = dict(
            db.execute(select(ExperimentRun.id, ExperimentRun.experiment_id).where(ExperimentRun.id.in_(run_ids)))
            .tuples()
            .all()
        )
    refs = {str(a.id) for a in artifacts} | {str(v.id) for vs in versions.values() for v in vs}
    experiment_refs = {str(e) for e in experiment_of_run.values()}
    cited_strings: set[str] = set()
    discovery_filter = [Discovery.evidence_ids.op("?|")(pg_array(sorted(refs)))]
    if experiment_refs:
        discovery_filter.append(Discovery.experiment_ids.op("?|")(pg_array(sorted(experiment_refs))))
    for evidence_ids, experiment_ids in db.execute(
        select(Discovery.evidence_ids, Discovery.experiment_ids).where(
            Discovery.organization_id == org, or_(*discovery_filter)
        )
    ).all():
        cited_strings.update(str(x) for x in (evidence_ids or []))
        cited_strings.update(str(x) for x in (experiment_ids or []))
    out: list[tuple[Artifact, list[ArtifactVersion]]] = []
    for artifact in artifacts:
        ids = {str(artifact.id)} | {str(v.id) for v in versions[artifact.id]}
        experiment_id = experiment_of_run.get(artifact.experiment_run_id) if artifact.experiment_run_id else None
        if ids & cited_strings or (experiment_id is not None and str(experiment_id) in cited_strings):
            continue
        out.append((artifact, versions[artifact.id]))
    return out


def _purge_artifacts(
    org: uuid.UUID,
    actor: Actor,
    *,
    standard_cutoff: datetime | None,
    ephemeral_cutoff: datetime | None,
    now: datetime,
    batch_size: int,
    max_batches: int,
) -> dict[str, int]:
    """Delete stored bytes of expired, unreferenced artifacts and soft-delete them (versions are immutable
    metadata and stay; a negative storage-usage entry records the reclaimed bytes)."""
    from aegis_api.lab.storage import ObjectNotFound, StorageUnavailable, get_storage
    from aegis_api.lab.usage.recorder import record_storage_usage

    counts = {"artifacts": 0, "artifact_objects": 0, "artifact_bytes": 0}
    skipped: set[uuid.UUID] = set()
    storage = get_storage()
    for _ in range(max_batches):
        with tenant_uow(org) as db:
            batch = [
                (artifact.id, artifact.project_id, [(v.id, v.storage_key, v.size_bytes) for v in versions])
                for artifact, versions in _artifact_candidates(
                    db, org, standard_cutoff, ephemeral_cutoff, batch_size, skipped
                )
            ]
        if not batch:
            break
        removed: list[tuple[uuid.UUID, uuid.UUID, list[tuple[uuid.UUID, int]]]] = []
        storage_down = False
        for artifact_id, project_id, versions in batch:
            ok = True
            reclaimed: list[tuple[uuid.UUID, int]] = []
            for version_id, key, size in versions:
                try:
                    storage.delete(key)
                except ObjectNotFound:
                    pass
                except StorageUnavailable:
                    storage_down = True
                    ok = False
                    break
                except Exception:
                    log.warning("artifact_purge_delete_failed", artifact_id=str(artifact_id), exc_info=True)
                    ok = False
                    break
                reclaimed.append((version_id, int(size or 0)))
            if storage_down:
                break
            if ok:
                removed.append((artifact_id, project_id, reclaimed))
            else:
                skipped.add(artifact_id)
        if removed:
            with tenant_uow(org) as db:
                soft_deleted = 0
                objects = 0
                reclaimed_bytes = 0
                for artifact_id, project_id, reclaimed in removed:
                    row = db.execute(
                        select(Artifact).where(Artifact.id == artifact_id).with_for_update()
                    ).scalar_one_or_none()
                    if row is None or row.deleted_at is not None:
                        continue
                    row.deleted_at = now
                    soft_deleted += 1
                    for version_id, size in reclaimed:
                        objects += 1
                        reclaimed_bytes += size
                        if size:
                            record_storage_usage(
                                db,
                                organization_id=org,
                                bytes_delta=-size,
                                reason=PURGE_REASON,
                                project_id=project_id,
                                artifact_version_id=version_id,
                            )
                if soft_deleted:
                    audit(
                        db,
                        actor,
                        RETENTION_AUDIT_ACTION,
                        "artifacts",
                        None,
                        after={
                            "table": "artifacts",
                            "deleted": soft_deleted,
                            "objects": objects,
                            "bytes": reclaimed_bytes,
                        },
                    )
            counts["artifacts"] += soft_deleted
            counts["artifact_objects"] += objects
            counts["artifact_bytes"] += reclaimed_bytes
        if storage_down:
            log.warning("artifact_purge_storage_unavailable", organization_id=str(org))
            break
        if len(batch) < batch_size:
            break
    return counts


def stuck_run_recovery(organization_id: uuid.UUID) -> dict[str, int]:
    """One recovery pass for an organization: start pending, resume stale, deliver signals."""
    counts: dict[str, int] = {}
    for part in (
        start_pending_runs(organization_id),
        resume_stale_runs(organization_id),
        deliver_pending_signals(organization_id),
    ):
        for key, value in part.items():
            counts[key] = counts.get(key, 0) + value
    return counts
