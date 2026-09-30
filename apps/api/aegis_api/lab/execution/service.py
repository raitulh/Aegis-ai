"""Execution service (contract §4.4): admission of sandboxed compute jobs and the worker-side job runner.

Admission (:func:`submit_job`, inside the caller's transaction): project access, image / command / env /
resource / network validation against deployment settings ∩ organization execution policy, input ownership
and split visibility checks, worst-case cost estimate, concurrency quota, mission compute budget and the
governance policy ``execution.submit`` (deny → ``PolicyDenied``; require_approval → a human approval is
requested and the job waits in ``QUEUED``). Governance is called through small lazily-importing wrappers
(``_evaluate_policy`` …) and fails CLOSED when the policy engine is unavailable.

Execution (:func:`run_job`, worker-side, blocking): generated code never runs in this process — the job runs
in the configured sandbox backend while the runner only orchestrates. Database work happens in short
``tenant_uow`` transactions around (never across) backend calls. The runner claims the job with a row lock,
heartbeats every ``HEARTBEAT_INTERVAL_SECONDS``, streams throttled log events, enforces the wall-clock
timeout and disk budget, honours cancellation, collects outputs through the safe extractor, stores them as
immutable artifacts, records compute usage and cost, appends hash-chained evidence and emits events. A job
whose worker died (stale heartbeat) is re-attached by the next ``run_job`` call when its sandbox still
exists, otherwise it fails with ``worker_lost``; ``attempt`` acts as an ownership token so a superseded
worker never overwrites the new owner's results.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import shutil
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from aegis_api.config import Settings, get_settings
from aegis_api.db.base import utcnow
from aegis_api.errors import Conflict, Forbidden, NotFound, ValidationFailed
from aegis_api.lab.core.access import effective_permissions, get_owned, load_project, visible_project_ids
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.audit import AuditAction, audit
from aegis_api.lab.core.deps import tenant_uow
from aegis_api.lab.core.errors import (
    ApprovalRequired,
    BudgetExceeded,
    ExecutionUnavailable,
    PolicyDenied,
    QuotaExceeded,
    TransientError,
)
from aegis_api.lab.core.events import EventType, emit
from aegis_api.lab.core.evidence import append_evidence
from aegis_api.lab.core.features import feature_enabled
from aegis_api.lab.core.locks import advisory_xact_lock
from aegis_api.lab.core.org_settings import get_org_settings, quota
from aegis_api.lab.core.pagination import CursorPage, CursorParams, paginate_keyset
from aegis_api.lab.execution import integrations
from aegis_api.lab.execution.archive import ArchiveLimitExceeded, ExtractionReport, ExtractLimits
from aegis_api.lab.execution.backends import (
    BackendHandle,
    BackendState,
    BackendStatus,
    ExecutionBackend,
    JobContext,
    get_backend,
)
from aegis_api.lab.execution.dispatch import dispatch_in_session
from aegis_api.lab.execution.inputs import InputStagingError, stage_inputs
from aegis_api.lab.execution.outputs import JobRef, persist_log, persist_outputs, read_metrics
from aegis_api.lab.execution.schemas import (
    ComputeJobLogsOut,
    ComputeJobOut,
    JobInput,
    JobResult,
    JobSpec,
    LogLineOut,
)
from aegis_api.lab.models import (
    Approval,
    ArtifactVersion,
    CodeSnapshot,
    ComputeJob,
    DatasetVersion,
    ExecutionEnvironment,
    Experiment,
    ExperimentRun,
    LabEvent,
    Mission,
    Project,
    WorkflowRun,
)
from aegis_api.lab.observability import metrics
from aegis_api.lab.usage.recorder import record_compute_usage
from engines.lab.compute_cost import ComputePrices, compute_cost, estimate_cost
from engines.lab.sandbox import (
    OUTPUT_DIR,
    PARAMS_PATH,
    SANDBOX_TMP,
    WORKDIR,
    EgressConfig,
    NetworkPolicy,
    ResourceRequest,
    SandboxLimits,
    SandboxSpec,
    normalize_hosts,
    parse_user,
    validate_spec,
)
from engines.lab.states import MISSION_TERMINAL, ApprovalStatus, ExecutionStatus, assert_transition

__all__ = [
    "JobInput",
    "JobResult",
    "JobSpec",
    "NetworkPolicy",
    "PolicyOutcome",
    "ResourceRequest",
    "cancel_job",
    "egress_config",
    "execution_limits",
    "get_job",
    "job_logs",
    "job_result",
    "list_jobs",
    "reconcile_stale_jobs",
    "run_job",
    "submit_job",
]

log = structlog.get_logger("aegis.lab.execution")

HEARTBEAT_INTERVAL_SECONDS = 15.0
STALE_AFTER_SECONDS = 2 * HEARTBEAT_INTERVAL_SECONDS
POLL_INTERVAL_SECONDS = 1.0
FOLLOW_POLL_SECONDS = 2.0
LOG_EVENT_INTERVAL_SECONDS = 2.0
LOG_EVENT_MAX_LINES = 50
LOG_LINE_MAX_CHARS = 1000
LOG_EVENTS_MAX_PER_JOB = 500
LOG_READ_MAX_BYTES = 256 * 1024
DISK_CHECK_INTERVAL_SECONDS = 15.0
MAX_BACKEND_ERRORS = 30
MAX_INLINE_BYTES = 1024 * 1024
MAX_PARAMETERS_BYTES = 256 * 1024
MIB = 1024 * 1024

_E = ExecutionStatus
ACTIVE_STATUSES = frozenset({_E.QUEUED, _E.PROVISIONING, _E.RUNNING, _E.PAUSED})
IN_FLIGHT_STATUSES = frozenset({_E.PROVISIONING, _E.RUNNING, _E.PAUSED})
FINISHED_STATUSES = frozenset(
    {_E.SUCCEEDED, _E.FAILED, _E.TIMED_OUT, _E.CANCELLED, _E.VERIFICATION_PENDING, _E.VERIFIED}
)


# =============================================================================================
# Governance wrappers (lazy imports; the governance context is a separate module)
# =============================================================================================
@dataclass(frozen=True)
class PolicyOutcome:
    effect: str  # allow | deny | require_approval
    reasons: tuple[str, ...] = ()
    matched_rules: tuple[str, ...] = ()
    policy_versions: tuple[Any, ...] = ()
    obligations: dict[str, Any] = field(default_factory=dict)
    approver_permission: str | None = None

    @classmethod
    def from_decision(cls, decision: Any) -> PolicyOutcome:
        def get(name: str, default: Any = None) -> Any:
            if isinstance(decision, dict):
                return decision.get(name, default)
            return getattr(decision, name, default)

        return cls(
            effect=str(get("effect", "deny")),
            reasons=tuple(str(r) for r in get("reasons", ()) or ()),
            matched_rules=tuple(str(r) for r in get("matched_rules", ()) or ()),
            policy_versions=tuple(get("policy_versions", ()) or ()),
            obligations=dict(get("obligations", {}) or {}),
            approver_permission=get("approver_permission"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "effect": self.effect,
            "reasons": list(self.reasons),
            "matched_rules": list(self.matched_rules),
            "policy_versions": [str(v) for v in self.policy_versions],
            "obligations": self.obligations,
            "approver_permission": self.approver_permission,
        }


@dataclass(frozen=True)
class BudgetOutcome:
    ok: bool
    reason: str | None = None
    remaining_usd: Decimal | None = None


def _evaluate_policy(
    db: Session,
    actor: Actor,
    action: str,
    context: dict[str, Any],
    *,
    project_id: uuid.UUID | None = None,
    mission: Mission | None = None,
) -> PolicyOutcome:
    try:
        from aegis_api.lab.governance.policies import evaluate_policy
    except ImportError:
        log.error("policy_engine_unavailable", action=action)
        return PolicyOutcome(
            effect="deny", reasons=("The governance policy engine is unavailable; execution is denied (fail closed)",)
        )
    decision = evaluate_policy(db, actor, action, context, project_id=project_id, mission=mission)
    return PolicyOutcome.from_decision(decision)


def _request_approval(db: Session, actor: Actor, **kwargs: Any) -> uuid.UUID:
    try:
        from aegis_api.lab.governance.approvals import request_approval
    except ImportError as exc:
        raise PolicyDenied("Human approval is required but the approvals service is unavailable") from exc
    approval = request_approval(db, actor, **kwargs)
    return uuid.UUID(str(approval.id))


def _check_quota(db: Session, organization_id: uuid.UUID, key: str) -> None:
    try:
        from aegis_api.lab.governance.quotas import check_quota
    except ImportError:
        # Never skip the quota: count active jobs against the organization's configured limit.
        limit = quota(db, organization_id, key)
        if key != "max_concurrent_experiments" or limit is None:
            return
        current = int(
            db.scalar(
                select(func.count(ComputeJob.id)).where(
                    ComputeJob.organization_id == organization_id, ComputeJob.status.in_(ACTIVE_STATUSES)
                )
            )
            or 0
        )
        if current + 1 > int(limit):
            raise QuotaExceeded(
                f"Quota '{key}' reached ({current}/{limit})",
                details={"key": key, "limit": int(limit), "current": current},
            ) from None
        return
    check_quota(db, organization_id, key, increment=1)


def _check_budget(db: Session, mission: Mission, kind: str, *, estimated_usd: Decimal) -> BudgetOutcome:
    try:
        from aegis_api.lab.governance.budgets import check_budget
    except ImportError:
        budget = mission.budget or {}
        remaining: Decimal | None = None
        for limit_key, spent in (
            ("max_compute_cost_usd", mission.spent_compute_usd),
            ("max_total_cost_usd", mission.spent_total_usd),
        ):
            limit = budget.get(limit_key)
            if limit is None:
                continue
            left = Decimal(str(limit)) - (spent or Decimal(0))
            remaining = left if remaining is None else min(remaining, left)
        ok = remaining is None or estimated_usd <= remaining
        return BudgetOutcome(
            ok=ok, reason=None if ok else "estimated cost exceeds the remaining budget", remaining_usd=remaining
        )
    check = check_budget(db, mission, kind, estimated_usd=estimated_usd)
    remaining_raw = getattr(check, "remaining_usd", None)
    return BudgetOutcome(
        ok=bool(getattr(check, "ok", False)),
        reason=getattr(check, "reason", None),
        remaining_usd=Decimal(str(remaining_raw)) if remaining_raw is not None else None,
    )


def _cancel_approval(db: Session, actor: Actor, approval_id: uuid.UUID) -> None:
    """Best effort: withdraw the pending approval of a job cancelled before it ran (never blocks the cancel)."""
    try:
        from aegis_api.lab.governance.approvals import cancel_approval
    except ImportError:
        return
    try:
        with db.begin_nested():
            cancel_approval(db, actor, approval_id, reason="The compute job was cancelled")
    except Exception as exc:
        log.info("approval_not_withdrawn", approval_id=str(approval_id), reason=type(exc).__name__)


def _approval_status(db: Session, organization_id: uuid.UUID, approval_id: uuid.UUID) -> str | None:
    approval = db.get(Approval, approval_id)
    if approval is None or approval.organization_id != organization_id:
        return None
    if approval.status == ApprovalStatus.PENDING and approval.expires_at is not None and approval.expires_at < utcnow():
        return ApprovalStatus.EXPIRED
    return str(approval.status)


# =============================================================================================
# Limits and configuration
# =============================================================================================
def egress_config(settings: Settings | None = None, backend: str | None = None) -> EgressConfig | None:
    """Allowlisted egress is available only when the operator configured the proxy (and, for Docker, the
    internal network the sandbox joins)."""
    s = settings or get_settings()
    proxy = s.execution_egress_proxy_url
    if not proxy:
        return None
    if (backend or s.execution_backend) == "local_docker":
        return EgressConfig(network=s.execution_egress_network, proxy_url=proxy) if s.execution_egress_network else None
    return EgressConfig(network=None, proxy_url=proxy)


def _prices(settings: Settings) -> ComputePrices:
    return ComputePrices.from_values(
        settings.execution_cpu_price_per_hour_usd,
        settings.execution_memory_gb_price_per_hour_usd,
        settings.gpu_prices,
    )


def execution_limits(db: Session, organization_id: uuid.UUID) -> tuple[SandboxLimits, list[str] | None]:
    """Deployment caps narrowed by the organization's ``execution_policy`` (+ its image allowlist)."""
    s = get_settings()
    org = get_org_settings(db, organization_id)
    policy: dict[str, Any] = dict(org.execution_policy or {})

    def cap(key: str, default: Any, conv: Callable[[Any], Any]) -> Any:
        value = policy.get(key)
        if value is None:
            return default
        try:
            return min(default, conv(value))
        except (TypeError, ValueError):
            return default

    gpu_allowed = (
        bool(s.execution_gpu_enabled)
        and policy.get("gpu_enabled") is True
        and feature_enabled(db, organization_id, "gpu_execution")
    )
    org_hosts = [h.strip().lower() for h in (org.egress_allowlist or []) if isinstance(h, str) and h.strip()]
    allowlist = tuple(dict.fromkeys([*org_hosts, *s.tool_egress_allowlist_hosts]))
    images = policy.get("allowed_images")
    org_images = [str(i) for i in images] if isinstance(images, list) else None
    limits = SandboxLimits(
        max_cpu=cap("max_cpu", float(s.execution_max_cpu), float),
        max_memory_mb=cap("max_memory_mb", int(s.execution_max_memory_mb), int),
        max_disk_mb=cap("max_disk_mb", int(s.execution_max_disk_mb), int),
        max_timeout_seconds=cap("max_timeout_seconds", int(s.execution_max_timeout_seconds), int),
        pids_limit=int(s.execution_pids_limit),
        tmpfs_mb=int(s.execution_tmpfs_mb),
        max_log_bytes=int(s.execution_max_log_bytes),
        gpu_allowed=gpu_allowed,
        egress_available=egress_config(s) is not None,
        egress_allowlist=allowlist,
    )
    return limits, org_images


# =============================================================================================
# Admission
# =============================================================================================
def _load_mission(db: Session, actor: Actor, mission_id: uuid.UUID | None, project: Project) -> Mission | None:
    if mission_id is None:
        return None
    mission = get_owned(db, Mission, mission_id, actor, label="Mission")
    if mission.project_id != project.id:
        raise ValidationFailed("mission_id belongs to a different project")
    if mission.status in MISSION_TERMINAL:
        raise Conflict(f"Mission is {mission.status}; no new compute jobs can be submitted", code="mission_not_active")
    return mission


def _same_project(row: Any, project: Project, what: str) -> None:
    if getattr(row, "project_id", None) != project.id:
        raise ValidationFailed(f"{what} belongs to a different project")


def _load_related(db: Session, actor: Actor, spec: JobSpec, project: Project) -> ExecutionEnvironment | None:
    if spec.experiment_id is not None:
        _same_project(
            get_owned(db, Experiment, spec.experiment_id, actor, label="Experiment"), project, "experiment_id"
        )
    if spec.experiment_run_id is not None:
        run = get_owned(db, ExperimentRun, spec.experiment_run_id, actor, label="Experiment run")
        _same_project(run, project, "experiment_run_id")
        if spec.experiment_id is not None and run.experiment_id != spec.experiment_id:
            raise ValidationFailed("experiment_run_id does not belong to experiment_id")
    if spec.code_snapshot_id is not None:
        _same_project(
            get_owned(db, CodeSnapshot, spec.code_snapshot_id, actor, label="Code snapshot"),
            project,
            "code_snapshot_id",
        )
    if spec.environment_id is not None:
        return get_owned(db, ExecutionEnvironment, spec.environment_id, actor, label="Execution environment")
    return None


def _prepare_inputs(
    db: Session, actor: Actor, spec: JobSpec, project: Project
) -> tuple[list[dict[str, Any]], list[str]]:
    """Validate inputs (ownership, visibility, split policy, sizes) → (serialized inputs, problems)."""
    problems: list[str] = []
    items = list(spec.inputs)
    if spec.code_snapshot_id is not None and not any(
        i.kind == "code_snapshot" and i.ref_id == spec.code_snapshot_id for i in items
    ):
        items.insert(0, JobInput(kind="code_snapshot", ref_id=spec.code_snapshot_id))
    serialized: list[dict[str, Any]] = []
    file_paths: set[str] = set()
    tree_paths: set[str] = set()
    inline_total = 0
    for item in items:
        path = str(item.path)
        record: dict[str, Any] = {"kind": item.kind, "path": path}
        if item.kind == "code_snapshot":
            if any(path == t or path.startswith(t + "/") or t.startswith(path + "/") for t in tree_paths):
                problems.append(f"inputs: code snapshot paths overlap at {path!r}")
            tree_paths.add(path)
        else:
            if path in file_paths:
                problems.append(f"inputs: duplicate path {path!r}")
            file_paths.add(path)
        if item.kind == "inline":
            data = item.decoded_content()
            inline_total += len(data)
            record.update({"content": item.content, "encoding": item.encoding, "size": len(data)})
        elif item.kind == "dataset_version":
            version = get_owned(db, DatasetVersion, item.ref_id, actor, label="Dataset version")
            if version.project_id != project.id:
                load_project(db, actor, version.project_id, "dataset:read")
            splits = version.splits or {}
            if item.split is not None:
                info = splits.get(item.split)
                if not isinstance(info, dict):
                    problems.append(f"inputs: dataset version has no split {item.split!r}")
                elif info.get("visibility") == "evaluator_only":
                    problems.append(
                        f"inputs: split {item.split!r} is evaluator-only (held-out data is never mounted into sandboxes)"
                    )
            elif any(isinstance(v, dict) and v.get("visibility") == "evaluator_only" for v in splits.values()):
                problems.append(
                    "inputs: this dataset version has evaluator-only splits; name an experiment split explicitly"
                )
            record.update({"ref_id": str(version.id), "split": item.split, "size": version.size_bytes})
        elif item.kind == "artifact_version":
            artifact = get_owned(db, ArtifactVersion, item.ref_id, actor, label="Artifact version")
            if artifact.project_id != project.id:
                load_project(db, actor, artifact.project_id, "artifact:read")
            if artifact.scan_status == "infected":
                problems.append(f"inputs: artifact version {artifact.id} failed malware scanning")
            record.update({"ref_id": str(artifact.id), "size": artifact.size_bytes})
        else:  # code_snapshot
            snapshot = get_owned(db, CodeSnapshot, item.ref_id, actor, label="Code snapshot")
            if snapshot.project_id != project.id:
                load_project(db, actor, snapshot.project_id, "experiment:read")
            if not snapshot.storage_key:
                problems.append(f"inputs: code snapshot {snapshot.id} has no stored files")
            record.update({"ref_id": str(snapshot.id), "content_hash": snapshot.content_hash})
        serialized.append(record)
    for path in file_paths:
        if any(path == t or path.startswith(t + "/") for t in tree_paths):
            problems.append(f"inputs: {path!r} collides with a code snapshot directory")
    if inline_total > MAX_INLINE_BYTES:
        problems.append(f"inputs: inline content exceeds {MAX_INLINE_BYTES} bytes (upload an artifact instead)")
    if spec.parameters:
        try:
            raw = json.dumps(spec.parameters, allow_nan=False, sort_keys=True)
        except (TypeError, ValueError):
            problems.append("parameters: must be JSON-serializable without NaN/Infinity")
        else:
            if len(raw) > MAX_PARAMETERS_BYTES:
                problems.append(f"parameters: exceed {MAX_PARAMETERS_BYTES} bytes")
            serialized.append({"kind": "parameters", "path": PARAMS_PATH, "content": spec.parameters, "size": len(raw)})
    return serialized, problems


def submit_job(db: Session, actor: Actor, spec: JobSpec) -> ComputeJob:
    """Validate, govern and enqueue a compute job (idempotent on ``spec.idempotency_key``). Does not commit."""
    settings = get_settings()
    if settings.execution_backend == "disabled":
        raise ExecutionUnavailable("Sandboxed execution is disabled in this deployment")
    project = load_project(db, actor, spec.project_id, "experiment:execute")
    org_id = actor.organization_id

    idempotency_key = spec.idempotency_key or f"auto:{uuid.uuid4().hex}"
    if spec.idempotency_key:
        advisory_xact_lock(db, f"compute-job-idem:{org_id}:{idempotency_key}")
        existing = db.scalar(
            select(ComputeJob).where(
                ComputeJob.organization_id == org_id, ComputeJob.idempotency_key == idempotency_key
            )
        )
        if existing is not None:
            if (
                existing.project_id != project.id
                or list(existing.command or []) != list(spec.command)
                or (spec.image is not None and existing.image != spec.image)
            ):
                raise Conflict(
                    "This idempotency key was already used for a different compute job",
                    code="idempotency_key_reused",
                )
            return existing

    mission = _load_mission(db, actor, spec.mission_id, project)
    environment = _load_related(db, actor, spec, project)
    limits, org_images = execution_limits(db, org_id)
    image = spec.image or (environment.image if environment is not None else None) or settings.execution_default_image
    if environment is not None and spec.image and spec.image != environment.image:
        raise ValidationFailed("image differs from the execution environment's image")
    timeout = spec.timeout_seconds or min(settings.execution_default_timeout_seconds, limits.max_timeout_seconds)
    sandbox = SandboxSpec(
        image=image,
        command=list(spec.command),
        env=dict(spec.env),
        resources=spec.resources,
        timeout_seconds=timeout,
        network=spec.network,
        user=settings.execution_user,
    )
    problems = validate_spec(sandbox, limits, settings.execution_allowed_image_list, org_images)
    if environment is not None:
        allowed_vars = (environment.env_policy or {}).get("allowed_vars")
        if isinstance(allowed_vars, list):
            extra = sorted(set(spec.env) - {str(v) for v in allowed_vars})
            if extra:
                problems.append(f"env: not allowed by the execution environment: {', '.join(extra)}")
    inputs, input_problems = _prepare_inputs(db, actor, spec, project)
    problems += input_problems
    if problems:
        raise ValidationFailed("The compute job specification is invalid", details={"problems": problems})

    hosts = normalize_hosts(spec.network.hosts) if spec.network.mode == "allowlist" else []
    estimated, basis = estimate_cost(spec.resources, timeout, _prices(settings))

    _check_quota(db, org_id, "max_concurrent_experiments")
    budget_remaining: Decimal | None = None
    if mission is not None:
        budget = _check_budget(db, mission, "compute", estimated_usd=estimated)
        budget_remaining = budget.remaining_usd
        if budget_remaining is not None and budget_remaining <= 0:
            raise BudgetExceeded(
                "The mission's compute budget is exhausted",
                details={"remaining_usd": float(budget_remaining), "reason": budget.reason},
            )

    secrets_requested = sorted({s.strip() for s in spec.secrets if s.strip()})
    context: dict[str, Any] = {
        "autonomy_level": actor.autonomy_level or (mission.autonomy_level if mission is not None else None),
        "estimated_cost_usd": float(estimated),
        "network_mode": spec.network.mode,
        "egress_hosts": hosts,
        "gpu_count": spec.resources.gpu_count,
        "secrets_requested": secrets_requested,
        "actor_kind": actor.kind,
        "is_human": actor.is_human,
        "production": False,
    }
    if budget_remaining is not None:
        context["budget_remaining_usd"] = float(budget_remaining)
    context = {k: v for k, v in context.items() if v is not None}
    decision = _evaluate_policy(db, actor, "execution.submit", context, project_id=project.id, mission=mission)
    if decision.effect == "deny":
        raise PolicyDenied(
            "Compute job denied by governance policy: " + ("; ".join(decision.reasons) or "denied"),
            details={"reasons": list(decision.reasons), "matched_rules": list(decision.matched_rules)},
        )
    if decision.effect not in ("allow", "require_approval"):
        raise PolicyDenied(f"Unrecognized policy effect {decision.effect!r}; execution denied (fail closed)")
    if secrets_requested:
        # Fail closed: the sandbox has no secret-injection channel, so a job that needs secrets cannot run.
        raise PolicyDenied(
            "Secrets cannot be injected into sandboxed jobs in this deployment; remove 'secrets' from the job",
            details={"secrets_requested": secrets_requested},
        )

    resources = spec.resources.model_dump()
    resources["pids"] = min(spec.resources.pids or limits.pids_limit, limits.pids_limit)
    job = ComputeJob(
        organization_id=org_id,
        workspace_id=project.workspace_id,
        project_id=project.id,
        mission_id=mission.id if mission is not None else None,
        experiment_id=spec.experiment_id,
        experiment_run_id=spec.experiment_run_id,
        purpose=spec.purpose,
        backend=settings.execution_backend,
        image=image,
        command=list(spec.command),
        environment_id=spec.environment_id,
        code_snapshot_id=spec.code_snapshot_id,
        inputs=inputs,
        resource_request=resources,
        timeout_seconds=timeout,
        network_policy={"mode": spec.network.mode, "hosts": hosts},
        filesystem_policy={
            "root": "read_only",
            "workspace": WORKDIR,
            "workspace_disk_mb": spec.resources.disk_mb,
            "inputs": "read_only",
            "outputs": OUTPUT_DIR,
            "tmp": {"path": SANDBOX_TMP, "size_mb": limits.tmpfs_mb, "noexec": True},
            "max_output_bytes": settings.execution_max_output_bytes,
            "max_output_files": settings.execution_max_output_files,
        },
        secrets_policy={"allowed": [], "requested": secrets_requested},
        env=dict(spec.env),
        status=_E.QUEUED,
        idempotency_key=idempotency_key,
        attempt=1,
        queued_at=utcnow(),
        estimated_cost_usd=estimated,
        cost_usd=Decimal(0),
        cancel_requested=False,
        workflow_run_id=actor.workflow_run_id,
        created_by_id=actor.user_id,
        created_by_agent_run_id=actor.agent_run_id,
        resource_usage={"estimate_basis": basis},
    )
    db.add(job)
    db.flush()
    if decision.effect == "require_approval":
        risky = spec.network.mode != "none" or spec.resources.gpu_count > 0
        job.approval_id = _request_approval(
            db,
            actor,
            action="execution.submit",
            subject_type="compute_job",
            subject_id=str(job.id),
            title=f"Run sandboxed compute job ({image}, {timeout}s, {spec.resources.cpu} vCPU)",
            payload={
                "image": image,
                "command": list(spec.command)[:20],
                "resources": resources,
                "timeout_seconds": timeout,
                "network": {"mode": spec.network.mode, "hosts": hosts},
                "estimated_cost_usd": float(estimated),
                "reasons": list(decision.reasons),
            },
            risk_level="HIGH" if risky else "MEDIUM",
            estimated_cost_usd=estimated,
            decision=decision.as_dict(),
            project_id=project.id,
            mission_id=mission.id if mission is not None else None,
            workflow_run_id=actor.workflow_run_id,
        )
        job.status_reason = "awaiting_approval"
    emit(
        db,
        organization_id=org_id,
        type=EventType.EXPERIMENT_QUEUED,
        payload={
            "job_id": str(job.id),
            "status": job.status,
            "image": image,
            "purpose": job.purpose,
            "estimated_cost_usd": float(estimated),
            "approval_id": str(job.approval_id) if job.approval_id else None,
            "experiment_id": str(job.experiment_id) if job.experiment_id else None,
            "experiment_run_id": str(job.experiment_run_id) if job.experiment_run_id else None,
        },
        mission_id=job.mission_id,
        project_id=project.id,
        workspace_id=project.workspace_id,
        subject_type="compute_job",
        subject_id=job.id,
        actor=actor,
    )
    metrics.COMPUTE_JOBS.labels(job.backend, _E.QUEUED).inc()
    db.flush()
    return job


# =============================================================================================
# Queries and cancellation
# =============================================================================================
def get_job(db: Session, actor: Actor, job_id: uuid.UUID | str) -> ComputeJob:
    job = get_owned(db, ComputeJob, job_id, actor, label="Compute job")
    load_project(db, actor, job.project_id, "execution:read")
    return job


def list_jobs(
    db: Session,
    actor: Actor,
    params: CursorParams,
    *,
    status: str | None = None,
    project_id: uuid.UUID | None = None,
    mission_id: uuid.UUID | None = None,
    experiment_id: uuid.UUID | None = None,
) -> CursorPage[ComputeJobOut]:
    stmt = select(ComputeJob).where(ComputeJob.organization_id == actor.organization_id)
    if project_id is not None:
        load_project(db, actor, project_id, "execution:read")
        stmt = stmt.where(ComputeJob.project_id == project_id)
    else:
        visible = visible_project_ids(db, actor)
        if visible is not None:
            stmt = stmt.where(ComputeJob.project_id.in_(visible))
    if status is not None:
        if status not in {s.value for s in ExecutionStatus}:
            raise ValidationFailed(f"Unknown status {status!r}")
        stmt = stmt.where(ComputeJob.status == status)
    if mission_id is not None:
        stmt = stmt.where(ComputeJob.mission_id == mission_id)
    if experiment_id is not None:
        stmt = stmt.where(ComputeJob.experiment_id == experiment_id)
    return paginate_keyset(
        db, stmt, params, time_col=ComputeJob.created_at, id_col=ComputeJob.id, mapper=ComputeJobOut.from_job
    )


def cancel_job(db: Session, actor: Actor, job_id: uuid.UUID | str) -> ComputeJob:
    """Cancel a job: ``QUEUED`` → ``CANCELLED`` immediately; running jobs are killed by their worker at the
    next heartbeat (``cancel_requested``). Requires ``execution:manage`` or ``experiment:cancel``."""
    job = get_owned(db, ComputeJob, job_id, actor, label="Compute job")
    project = load_project(db, actor, job.project_id)
    granted = effective_permissions(db, actor, project)
    if not ({"execution:manage", "experiment:cancel"} & granted):
        raise Forbidden("Missing required permission(s): execution:manage or experiment:cancel")
    locked = db.scalar(
        select(ComputeJob).where(ComputeJob.id == job.id).with_for_update().execution_options(populate_existing=True)
    )
    assert locked is not None
    job = locked
    if job.status == _E.QUEUED:
        assert_transition("execution", job.status, _E.CANCELLED)
        before = job.status
        job.status = _E.CANCELLED
        job.status_reason = "cancelled_by_user" if actor.is_human else f"cancelled_by_{actor.kind}"
        job.cancel_requested = True
        job.completed_at = utcnow()
        metrics.COMPUTE_JOBS.labels(job.backend, _E.CANCELLED).inc()
        if job.approval_id is not None:
            _cancel_approval(db, actor, job.approval_id)
    elif job.status in IN_FLIGHT_STATUSES:
        if job.cancel_requested:
            return job
        before = job.status
        job.cancel_requested = True
        job.status_reason = "cancel_requested"
    else:
        assert_transition("execution", job.status, _E.CANCELLED)  # raises: already finished
        return job  # pragma: no cover - unreachable
    audit(
        db,
        actor,
        AuditAction.EXPERIMENT_CANCELLED,
        "compute_job",
        job.id,
        before={"status": before},
        after={"status": job.status, "cancel_requested": True},
    )
    emit(
        db,
        organization_id=job.organization_id,
        type=EventType.COMPUTE_JOB_UPDATED,
        payload={"job_id": str(job.id), "status": job.status, "cancel_requested": True, "reason": job.status_reason},
        mission_id=job.mission_id,
        project_id=job.project_id,
        workspace_id=job.workspace_id,
        subject_type="compute_job",
        subject_id=job.id,
        actor=actor,
    )
    db.flush()
    return job


def job_result(job: ComputeJob) -> JobResult:
    manifest = job.output_manifest or {}
    files = manifest.get("files") or {}
    usage = job.resource_usage or {}
    return JobResult(
        job_id=str(job.id),
        status=job.status,
        reason=job.status_reason,
        exit_code=job.exit_code,
        duration_seconds=usage.get("wall_seconds"),
        outputs={p: str(i.get("artifact_version_id")) for p, i in files.items() if isinstance(i, dict)},
        metrics=manifest.get("metrics"),
        logs_artifact_version_id=(manifest.get("logs") or {}).get("artifact_version_id"),
        resource_usage=dict(usage),
        cost_usd=job.cost_usd or Decimal(0),
        error=job.error,
    )


def job_logs(db: Session, actor: Actor, job_id: uuid.UUID | str, *, limit_bytes: int) -> ComputeJobLogsOut:
    """Logs of a finished job (from its stored log object) or the recent streamed lines of a running job.

    Log text is untrusted program output: render it as text, never as markup or instructions.
    """
    job = get_job(db, actor, job_id)
    logs = (job.output_manifest or {}).get("logs") or {}
    key = logs.get("storage_key")
    if job.status in FINISHED_STATUSES and key:
        max_bytes = max(int(get_settings().execution_max_log_bytes), int(logs.get("size") or 0)) + 1
        data = integrations.get_storage().get_bytes(str(key), max_bytes=max_bytes)
        tail = data[-limit_bytes:]
        return ComputeJobLogsOut(
            job_id=str(job.id),
            status=job.status,
            source="artifact",
            text=tail.decode("utf-8", "replace"),
            truncated=bool(logs.get("truncated")) or len(data) > len(tail),
            logs_artifact_version_id=logs.get("artifact_version_id"),
        )
    events = db.scalars(
        select(LabEvent)
        .where(
            LabEvent.organization_id == job.organization_id,
            LabEvent.subject_type == "compute_job",
            LabEvent.subject_id == str(job.id),
            LabEvent.type == EventType.EXPERIMENT_LOG,
        )
        .order_by(LabEvent.id.desc())
        .limit(200)
    ).all()
    lines: list[LogLineOut] = []
    truncated = False
    for event in reversed(events):
        payload = event.payload or {}
        truncated = truncated or bool(payload.get("truncated"))
        for line in payload.get("lines") or []:
            if isinstance(line, dict):
                lines.append(LogLineOut(ts=line.get("ts"), stream=line.get("stream"), text=str(line.get("text", ""))))
    text = "\n".join(line.text for line in lines)
    if len(text.encode()) > limit_bytes:
        text = text.encode()[-limit_bytes:].decode("utf-8", "replace")
        truncated = True
    return ComputeJobLogsOut(
        job_id=str(job.id),
        status=job.status,
        source="events" if lines else "none",
        text=text,
        lines=lines[-2000:],
        truncated=truncated,
    )


# =============================================================================================
# Worker-side execution
# =============================================================================================
@dataclass(frozen=True)
class _Snapshot:
    id: uuid.UUID
    organization_id: uuid.UUID
    workspace_id: uuid.UUID
    project_id: uuid.UUID
    mission_id: uuid.UUID | None
    experiment_id: uuid.UUID | None
    experiment_run_id: uuid.UUID | None
    backend: str
    image: str
    command: list[str]
    env: dict[str, str]
    inputs: list[dict[str, Any]]
    resource_request: dict[str, Any]
    timeout_seconds: int
    network_policy: dict[str, Any]
    status: str
    attempt: int
    started_at: datetime | None
    previous_heartbeat_at: datetime | None

    @classmethod
    def of(cls, job: ComputeJob, previous_heartbeat_at: datetime | None = None) -> _Snapshot:
        return cls(
            id=job.id,
            organization_id=job.organization_id,
            workspace_id=job.workspace_id,
            project_id=job.project_id,
            mission_id=job.mission_id,
            experiment_id=job.experiment_id,
            experiment_run_id=job.experiment_run_id,
            backend=job.backend,
            image=job.image,
            command=list(job.command or []),
            env={str(k): str(v) for k, v in (job.env or {}).items()},
            inputs=[dict(i) for i in (job.inputs or []) if isinstance(i, dict)],
            resource_request=dict(job.resource_request or {}),
            timeout_seconds=int(job.timeout_seconds),
            network_policy=dict(job.network_policy or {}),
            status=job.status,
            attempt=int(job.attempt or 1),
            started_at=job.started_at,
            previous_heartbeat_at=previous_heartbeat_at,
        )

    @property
    def resources(self) -> ResourceRequest:
        known = set(ResourceRequest.model_fields)
        return ResourceRequest.model_validate({k: v for k, v in self.resource_request.items() if k in known})


@dataclass(frozen=True)
class _Outcome:
    status: str
    reason: str | None = None
    error: str | None = None
    exit_code: int | None = None
    exited: bool = False  # the job's process finished by itself → outputs are collected
    started_at: datetime | None = None
    finished_at: datetime | None = None


class _Superseded(Exception):
    """Another worker took over this job (its ``attempt`` changed) — stop without touching it."""


def run_job(
    organization_id: uuid.UUID,
    job_id: uuid.UUID,
    *,
    heartbeat: Callable[[Any], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> JobResult:
    """Run (or re-attach to, or wait for) a compute job until it finishes. Worker-side only; blocking.

    Raises ``ApprovalRequired`` while the job's approval is pending. Returns the stored result for jobs that
    already finished, so it is safe to call repeatedly (activity retries, reconciliation).
    """
    return _JobRunner(organization_id, uuid.UUID(str(job_id)), heartbeat, is_cancelled).run()


class _JobRunner:
    def __init__(
        self,
        organization_id: uuid.UUID,
        job_id: uuid.UUID,
        heartbeat: Callable[[Any], None] | None,
        is_cancelled: Callable[[], bool] | None,
    ) -> None:
        self.org = organization_id
        self.job_id = job_id
        self.actor = Actor.system(organization_id, label="execution-worker")
        self.heartbeat_cb = heartbeat
        self.is_cancelled = is_cancelled
        self.settings = get_settings()
        self.snap: _Snapshot | None = None
        self.attempt = 0
        self.backend: ExecutionBackend | None = None
        self.handle: BackendHandle | None = None
        self.image_digest: str | None = None
        self.started_at: datetime | None = None
        self.log_cursor: int | None = None
        self.log_events = 0
        self.last_log_at = 0.0
        self.cpu_seconds: float | None = None
        self.max_memory_bytes: int | None = None
        self.max_disk_bytes: int | None = None
        self.final_logs: tuple[bytes, bool] | None = None

    # -- top level --------------------------------------------------------------------------------
    def run(self) -> JobResult:
        while True:
            mode, result = self._claim()
            if result is not None:
                return result
            try:
                if mode == "follow":
                    followed = self._follow()
                    if followed is not None:
                        return followed
                    continue
                if mode == "reattach":
                    return self._reattach()
                if mode == "reprovision":
                    self._remove_leftovers()
                return self._provision_and_run()
            except _Superseded:
                log.warning("compute_job_superseded", job_id=str(self.job_id), attempt=self.attempt)
                followed = self._follow()
                if followed is not None:
                    return followed

    @property
    def s(self) -> _Snapshot:
        assert self.snap is not None
        return self.snap

    def _lock(self, db: Session) -> ComputeJob:
        job = db.scalar(
            select(ComputeJob)
            .where(ComputeJob.id == self.job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if job is None or job.organization_id != self.org:
            raise NotFound("Compute job not found")
        return job

    def _check_owner(self, job: ComputeJob) -> None:
        if job.attempt != self.attempt or job.status in FINISHED_STATUSES or job.status == _E.QUEUED:
            raise _Superseded()

    def _emit(self, db: Session, job: ComputeJob, event_type: str, payload: dict[str, Any]) -> None:
        emit(
            db,
            organization_id=job.organization_id,
            type=event_type,
            payload={"job_id": str(job.id), **payload},
            mission_id=job.mission_id,
            project_id=job.project_id,
            workspace_id=job.workspace_id,
            subject_type="compute_job",
            subject_id=job.id,
            actor=self.actor,
        )

    def _beat(self, details: dict[str, Any]) -> None:
        if self.heartbeat_cb is None:
            return
        try:
            self.heartbeat_cb(details)
        except Exception:
            log.warning("activity_heartbeat_failed", job_id=str(self.job_id), exc_info=True)

    def _cancelled_by_caller(self) -> bool:
        if self.is_cancelled is None:
            return False
        try:
            return bool(self.is_cancelled())
        except Exception:
            return False

    # -- claim ------------------------------------------------------------------------------------
    def _claim(self) -> tuple[str, JobResult | None]:
        now = utcnow()
        with tenant_uow(self.org) as db:
            job = self._lock(db)
            if job.status in FINISHED_STATUSES:
                return "done", job_result(job)
            if job.status == _E.QUEUED:
                if job.cancel_requested:
                    self._close_unstarted(db, job, _E.CANCELLED, "cancelled_before_start")
                    return "done", job_result(job)
                if job.approval_id is not None:
                    state = _approval_status(db, self.org, job.approval_id)
                    if state == ApprovalStatus.PENDING:
                        raise ApprovalRequired(
                            "The compute job is waiting for human approval", approval_id=str(job.approval_id)
                        )
                    if state != ApprovalStatus.APPROVED:
                        self._close_unstarted(db, job, _E.CANCELLED, f"approval_{(state or 'missing').lower()}")
                        return "done", job_result(job)
                assert_transition("execution", job.status, _E.PROVISIONING)
                job.status = _E.PROVISIONING
                job.status_reason = None
                job.heartbeat_at = now
                self._emit(db, job, EventType.COMPUTE_JOB_UPDATED, {"status": job.status, "attempt": job.attempt})
                self.snap = _Snapshot.of(job)
                self.attempt = job.attempt
                return "fresh", None
            fresh = job.heartbeat_at is not None and now - job.heartbeat_at < timedelta(seconds=STALE_AFTER_SECONDS)
            if fresh:
                self.snap = _Snapshot.of(job)
                return "follow", None
            previous = job.heartbeat_at
            job.attempt = int(job.attempt or 1) + 1
            job.heartbeat_at = now
            self.snap = _Snapshot.of(job, previous_heartbeat_at=previous)
            self.attempt = job.attempt
            self._emit(
                db, job, EventType.COMPUTE_JOB_UPDATED, {"status": job.status, "attempt": job.attempt, "takeover": True}
            )
            return ("reprovision" if job.status == _E.PROVISIONING else "reattach"), None

    def _close_unstarted(self, db: Session, job: ComputeJob, status: str, reason: str) -> None:
        assert_transition("execution", job.status, status)
        job.status = status
        job.status_reason = reason[:200]
        job.completed_at = utcnow()
        event = EventType.EXPERIMENT_FAILED if status == _E.FAILED else EventType.COMPUTE_JOB_UPDATED
        self._emit(db, job, event, {"status": status, "reason": reason})
        metrics.COMPUTE_JOBS.labels(job.backend, status).inc()

    # -- follow (another live worker owns the job) --------------------------------------------------
    def _follow(self) -> JobResult | None:
        cancel_forwarded = False
        while True:
            time.sleep(FOLLOW_POLL_SECONDS)
            with tenant_uow(self.org) as db:
                job = db.scalar(
                    select(ComputeJob).where(ComputeJob.id == self.job_id).execution_options(populate_existing=True)
                )
                if job is None:
                    raise NotFound("Compute job not found")
                if job.status in FINISHED_STATUSES:
                    return job_result(job)
                if job.status == _E.QUEUED:
                    return None
                if not cancel_forwarded and self._cancelled_by_caller():
                    job.cancel_requested = True
                    cancel_forwarded = True
                stale = job.heartbeat_at is None or utcnow() - job.heartbeat_at >= timedelta(
                    seconds=STALE_AFTER_SECONDS
                )
            if stale:
                return None
            self._beat({"job_id": str(self.job_id), "following": True})

    # -- provisioning -------------------------------------------------------------------------------
    def _sandbox_spec(self) -> SandboxSpec:
        s = self.s
        network = NetworkPolicy.model_validate(s.network_policy or {"mode": "none"})
        return SandboxSpec(
            image=s.image,
            command=s.command,
            env=s.env,
            resources=s.resources,
            timeout_seconds=s.timeout_seconds,
            network=network,
            user=self.settings.execution_user,
        )

    @contextmanager
    def _background_heartbeat(self) -> Iterator[None]:
        """Keep the claim alive during long provisioning steps (image pulls, large input transfers)."""
        stop = threading.Event()

        def beat() -> None:
            while not stop.wait(HEARTBEAT_INTERVAL_SECONDS):
                try:
                    with tenant_uow(self.org) as db:
                        job = self._lock(db)
                        if job.attempt != self.attempt or job.status != _E.PROVISIONING:
                            return
                        job.heartbeat_at = utcnow()
                except Exception:
                    log.warning("provisioning_heartbeat_failed", job_id=str(self.job_id), exc_info=True)
                    continue
                self._beat({"job_id": str(self.job_id), "status": _E.PROVISIONING})

        # A copied context keeps engine-provided activity context (e.g. Temporal's) available to the heartbeat.
        context = contextvars.copy_context()
        thread = threading.Thread(target=context.run, args=(beat,), name=f"aegis-hb-{self.job_id}", daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=5)

    def _provision_and_run(self) -> JobResult:
        workdir = Path(tempfile.mkdtemp(prefix="aegis-job-"))
        try:
            try:
                with self._background_heartbeat():
                    spec = self._sandbox_spec()
                    uid, gid = parse_user(spec.user)
                    staged = stage_inputs(
                        self.actor,
                        self.s.inputs,
                        workdir / "stage",
                        max_bytes=spec.resources.disk_mb * MIB,
                        uid=uid,
                        gid=gid,
                    )
                    self.backend = get_backend(self.s.backend)
                    started = self.backend.start(
                        JobContext(
                            job_id=self.s.id,
                            organization_id=self.s.organization_id,
                            project_id=self.s.project_id,
                            spec=spec,
                            inputs_path=staged.path,
                            inputs_size=staged.size,
                            egress=egress_config(self.settings, self.s.backend)
                            if spec.network.mode == "allowlist"
                            else None,
                            pids_limit=int(self.s.resource_request.get("pids") or self.settings.execution_pids_limit),
                            tmpfs_mb=int(self.settings.execution_tmpfs_mb),
                            log_max_bytes=int(self.settings.execution_max_log_bytes),
                            allowed_images=tuple(self.settings.execution_allowed_image_list),
                        )
                    )
            except TransientError as exc:
                self._requeue(str(exc))
                raise
            except _Superseded:
                raise
            except Exception as exc:
                reason = "provisioning_failed"
                if isinstance(exc, InputStagingError | ArchiveLimitExceeded):
                    reason = "inputs_unavailable"
                elif isinstance(exc, ExecutionUnavailable):
                    reason = "backend_unavailable"
                log.warning("compute_job_provisioning_failed", job_id=str(self.job_id), reason=reason, error=str(exc))
                return self._finalize(_Outcome(status=_E.FAILED, reason=reason, error=str(exc)[:2000]), workdir)
            self.handle = started.handle
            self.image_digest = started.image_digest
            self._mark_running()
            outcome = self._monitor()
            return self._finalize(outcome, workdir)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def _requeue(self, error: str) -> None:
        try:
            with tenant_uow(self.org) as db:
                job = self._lock(db)
                if job.attempt == self.attempt and job.status == _E.PROVISIONING:
                    assert_transition("execution", job.status, _E.QUEUED)
                    job.status = _E.QUEUED
                    job.status_reason = f"requeued after transient error: {error}"[:500]
                    job.heartbeat_at = None
        except Exception:
            log.warning("compute_job_requeue_failed", job_id=str(self.job_id), exc_info=True)

    def _remove_leftovers(self) -> None:
        try:
            backend = get_backend(self.s.backend)
            handle = backend.find_by_job_id(self.job_id)
            if handle is not None:
                backend.cleanup(handle)
        except Exception:
            log.warning("compute_job_leftover_cleanup_failed", job_id=str(self.job_id), exc_info=True)

    def _mark_running(self) -> None:
        assert self.handle is not None
        with tenant_uow(self.org) as db:
            job = self._lock(db)
            try:
                self._check_owner(job)
            except _Superseded:
                # Our sandbox must not survive: the new owner provisions its own.
                self._abandon_sandbox()
                raise
            assert_transition("execution", job.status, _E.RUNNING)
            now = utcnow()
            job.status = _E.RUNNING
            job.started_at = now
            job.heartbeat_at = now
            job.backend_job_id = self.handle.backend_job_id[:200]
            if self.image_digest:
                job.image_digest = self.image_digest[:100]
            self.started_at = now
            self._emit(
                db,
                job,
                EventType.EXPERIMENT_STARTED,
                {
                    "status": job.status,
                    "backend": job.backend,
                    "image": job.image,
                    "image_digest": job.image_digest,
                    "attempt": job.attempt,
                    "experiment_id": str(job.experiment_id) if job.experiment_id else None,
                    "experiment_run_id": str(job.experiment_run_id) if job.experiment_run_id else None,
                },
            )
            audit(
                db,
                self.actor,
                AuditAction.EXPERIMENT_EXECUTED,
                "compute_job",
                job.id,
                organization_id=job.organization_id,
                after={
                    "backend": job.backend,
                    "image": job.image,
                    "image_digest": job.image_digest,
                    "command": list(job.command or [])[:20],
                    "network_mode": (job.network_policy or {}).get("mode", "none"),
                    "attempt": job.attempt,
                },
            )
        self._beat({"job_id": str(self.job_id), "status": _E.RUNNING})

    def _abandon_sandbox(self) -> None:
        if self.backend is None or self.handle is None:
            return
        try:
            self.backend.kill(self.handle)
            self.backend.cleanup(self.handle)
        except Exception:
            log.warning("compute_job_abandon_failed", job_id=str(self.job_id), exc_info=True)
        self.handle = None

    # -- re-attach -----------------------------------------------------------------------------------
    def _reattach(self) -> JobResult:
        s = self.s
        self.backend = get_backend(s.backend)
        handle = self.backend.find_by_job_id(self.job_id)
        if handle is None:
            log.warning("compute_job_worker_lost", job_id=str(self.job_id))
            return self._finalize(
                _Outcome(
                    status=_E.FAILED,
                    reason="worker_lost",
                    error="The worker running this job was lost and its sandbox no longer exists",
                    finished_at=s.previous_heartbeat_at,
                ),
                None,
            )
        log.info("compute_job_reattached", job_id=str(self.job_id), attempt=self.attempt)
        self.handle = handle
        self.started_at = s.started_at or utcnow()
        workdir = Path(tempfile.mkdtemp(prefix="aegis-job-"))
        try:
            outcome = self._monitor()
            return self._finalize(outcome, workdir)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    # -- monitoring ---------------------------------------------------------------------------------
    def _monitor(self) -> _Outcome:
        assert self.backend is not None and self.handle is not None and self.started_at is not None
        backend, handle, s = self.backend, self.handle, self.s
        deadline = self.started_at + timedelta(seconds=s.timeout_seconds)
        disk_limit = s.resources.disk_mb * MIB
        last_hb = time.monotonic()
        last_disk = 0.0
        errors = 0
        while True:
            try:
                status = backend.status(handle)
                errors = 0
            except (TransientError, ExecutionUnavailable):
                errors += 1
                if errors >= MAX_BACKEND_ERRORS:
                    raise
                log.warning("backend_status_failed", job_id=str(self.job_id), consecutive=errors)
                status = None
            if status is not None:
                if status.image_digest and not self.image_digest:
                    self.image_digest = status.image_digest
                if status.state == BackendState.MISSING:
                    return _Outcome(
                        status=_E.FAILED, reason="backend_lost", error="The sandbox disappeared before it finished"
                    )
                if status.state == BackendState.EXITED:
                    self._pump_logs(force=True)
                    return self._exit_outcome(status)
            if utcnow() >= deadline:
                return self._terminate(_E.TIMED_OUT, "timeout", f"Exceeded the {s.timeout_seconds}s wall-clock limit")
            mono = time.monotonic()
            include_disk = mono - last_disk >= DISK_CHECK_INTERVAL_SECONDS
            if include_disk:
                last_disk = mono
            self._sample(include_disk)
            if self.max_disk_bytes is not None and self.max_disk_bytes > disk_limit:
                return self._terminate(
                    _E.FAILED, "disk_limit_exceeded", f"Workspace exceeded its {s.resources.disk_mb} MiB disk budget"
                )
            if mono - last_hb >= HEARTBEAT_INTERVAL_SECONDS:
                cancel = self._heartbeat()
                last_hb = mono
                if cancel:
                    return self._terminate(_E.CANCELLED, "cancel_requested", "Cancelled on request")
            if self._cancelled_by_caller():
                return self._terminate(_E.CANCELLED, "workflow_cancelled", "Cancelled by the orchestrating workflow")
            self._pump_logs()
            time.sleep(POLL_INTERVAL_SECONDS)

    def _sample(self, include_disk: bool) -> None:
        assert self.backend is not None and self.handle is not None
        try:
            sample = self.backend.sample_usage(self.handle, include_disk=include_disk)
        except Exception:
            return
        if sample is None:
            return
        if sample.cpu_seconds is not None:
            self.cpu_seconds = max(self.cpu_seconds or 0.0, sample.cpu_seconds)
        if sample.memory_bytes is not None:
            self.max_memory_bytes = max(self.max_memory_bytes or 0, sample.memory_bytes)
        if sample.disk_bytes is not None:
            self.max_disk_bytes = max(self.max_disk_bytes or 0, sample.disk_bytes)

    def _heartbeat(self) -> bool:
        with tenant_uow(self.org) as db:
            job = self._lock(db)
            self._check_owner(job)
            job.heartbeat_at = utcnow()
            if self.image_digest and not job.image_digest:
                job.image_digest = self.image_digest[:100]
            cancel = bool(job.cancel_requested)
        self._beat({"job_id": str(self.job_id), "status": _E.RUNNING})
        return cancel

    def _exit_outcome(self, status: BackendStatus) -> _Outcome:
        times = _Outcome(status=_E.FAILED, started_at=status.started_at, finished_at=status.finished_at)
        if status.error and status.exit_code is None:
            return replace(times, reason=status.reason or "backend_error", error=status.error[:2000])
        if status.reason == "deadline_exceeded":
            return replace(
                times,
                status=_E.TIMED_OUT,
                reason="timeout",
                error=f"Exceeded the {self.s.timeout_seconds}s wall-clock limit",
            )
        if status.oom_killed:
            return replace(
                times,
                reason="oom",
                error=f"Out of memory (limit {self.s.resources.memory_mb} MiB)",
                exit_code=status.exit_code,
                exited=True,
            )
        if status.exit_code == 0:
            return replace(times, status=_E.SUCCEEDED, exit_code=0, exited=True)
        return replace(
            times,
            reason="nonzero_exit",
            error=f"The job exited with code {status.exit_code}",
            exit_code=status.exit_code,
            exited=True,
        )

    def _fetch_logs(self) -> tuple[bytes, bool]:
        assert self.backend is not None and self.handle is not None
        try:
            return self.backend.fetch_logs(self.handle, max_bytes=int(self.settings.execution_max_log_bytes))
        except Exception:
            log.warning("compute_job_log_fetch_failed", job_id=str(self.job_id), exc_info=True)
            return b"", False

    def _terminate(self, status: str, reason: str, error: str) -> _Outcome:
        assert self.backend is not None and self.handle is not None
        before = self._fetch_logs()
        try:
            self.backend.kill(self.handle)
        except Exception:
            log.warning("compute_job_kill_failed", job_id=str(self.job_id), exc_info=True)
        after = self._fetch_logs()
        self.final_logs = after if len(after[0]) >= len(before[0]) else before
        return _Outcome(status=status, reason=reason, error=error, finished_at=utcnow())

    def _pump_logs(self, *, force: bool = False) -> None:
        if self.backend is None or self.handle is None or self.log_events >= LOG_EVENTS_MAX_PER_JOB:
            return
        mono = time.monotonic()
        if not force and mono - self.last_log_at < LOG_EVENT_INTERVAL_SECONDS:
            return
        self.last_log_at = mono
        try:
            batch = self.backend.read_logs(self.handle, since_ns=self.log_cursor, max_bytes=LOG_READ_MAX_BYTES)
        except Exception:
            log.debug("compute_job_log_read_failed", job_id=str(self.job_id), exc_info=True)
            return
        if batch.cursor_ns is not None:
            self.log_cursor = batch.cursor_ns
        if not batch.lines:
            return
        lines = batch.lines[-LOG_EVENT_MAX_LINES:]
        dropped = len(batch.lines) - len(lines)
        payload = {
            "seq": self.log_events,
            "lines": [
                {
                    "ts": datetime.fromtimestamp(line.ts_ns / 1e9, tz=UTC).isoformat(),
                    "stream": line.stream,
                    "text": line.text[:LOG_LINE_MAX_CHARS],
                }
                for line in lines
            ],
            "dropped_lines": dropped,
            "truncated": batch.truncated or dropped > 0 or any(len(line.text) > LOG_LINE_MAX_CHARS for line in lines),
        }
        try:
            with tenant_uow(self.org) as db:
                job = db.get(ComputeJob, self.job_id)
                if job is None:
                    return
                self._emit(db, job, EventType.EXPERIMENT_LOG, payload)
        except Exception:
            log.warning("compute_job_log_event_failed", job_id=str(self.job_id), exc_info=True)
            return
        self.log_events += 1

    # -- finalization -------------------------------------------------------------------------------
    def _output_limits(self) -> ExtractLimits:
        return ExtractLimits(
            max_total_bytes=int(self.settings.execution_max_output_bytes),
            max_files=int(self.settings.execution_max_output_files),
        )

    def _finalize(self, outcome: _Outcome, workdir: Path | None) -> JobResult:
        s = self.s
        report: ExtractionReport | None = None
        outputs: dict[str, dict[str, Any]] = {}
        logs_info: dict[str, Any] | None = None
        notes: list[str] = []
        ref = JobRef(
            organization_id=s.organization_id,
            project_id=s.project_id,
            job_id=s.id,
            mission_id=s.mission_id,
            experiment_run_id=s.experiment_run_id,
        )
        scratch = workdir or Path(tempfile.mkdtemp(prefix="aegis-job-"))
        try:
            if self.backend is not None and self.handle is not None:
                log_data, log_truncated = self.final_logs or self._fetch_logs()
                if outcome.exited:
                    try:
                        report = self.backend.collect_outputs(self.handle, scratch / "outputs", self._output_limits())
                    except ArchiveLimitExceeded as exc:
                        outcome = replace(
                            outcome, status=_E.FAILED, reason="output_limit_exceeded", error=str(exc)[:2000]
                        )
                    except Exception as exc:
                        log.warning("compute_job_output_collection_failed", job_id=str(self.job_id), exc_info=True)
                        outcome = replace(
                            outcome, status=_E.FAILED, reason="output_collection_failed", error=str(exc)[:2000]
                        )
                if report is not None and report.rejected:
                    notes.append(f"{len(report.rejected)} unsafe output entries were ignored")
                if report is not None and report.files and outcome.reason != "output_limit_exceeded":
                    try:
                        outputs = persist_outputs(self.actor, ref, report.files)
                    except Exception as exc:
                        log.warning("compute_job_output_persist_failed", job_id=str(self.job_id), exc_info=True)
                        outcome = replace(
                            outcome, status=_E.FAILED, reason="output_persist_failed", error=str(exc)[:2000]
                        )
                if log_data:
                    log_path = scratch / "job.log"
                    log_path.write_bytes(log_data)
                    try:
                        logs_info = persist_log(
                            self.actor,
                            ref,
                            log_path,
                            size=len(log_data),
                            sha256=hashlib.sha256(log_data).hexdigest(),
                            truncated=log_truncated,
                        )
                        logs_info["storage_key"] = integrations.object_key(
                            s.organization_id, s.project_id, "compute-jobs", str(s.id), "job.log"
                        )
                    except Exception:
                        log.warning("compute_job_log_persist_failed", job_id=str(self.job_id), exc_info=True)
                        notes.append("the job log could not be stored")
            metrics_doc = read_metrics(report) if outcome.reason != "output_limit_exceeded" else None
            return self._commit_result(outcome, outputs, metrics_doc, logs_info, report, notes)
        finally:
            if workdir is None:
                shutil.rmtree(scratch, ignore_errors=True)

    def _commit_result(
        self,
        outcome: _Outcome,
        outputs: dict[str, dict[str, Any]],
        metrics_doc: dict[str, Any] | None,
        logs_info: dict[str, Any] | None,
        report: ExtractionReport | None,
        notes: list[str],
    ) -> JobResult:
        s = self.s
        resources = s.resources
        started = outcome.started_at or self.started_at or s.started_at
        finished = outcome.finished_at or utcnow()
        wall = max((finished - started).total_seconds(), 0.0) if started is not None else 0.0
        cost, basis = compute_cost(
            wall_seconds=wall,
            cpu=resources.cpu,
            memory_mb=resources.memory_mb,
            gpu_type=resources.gpu_type,
            gpu_count=resources.gpu_count,
            prices=_prices(self.settings),
        )
        if started is None:
            cost, basis = Decimal(0), "not started"
        cpu_seconds = self.cpu_seconds if self.cpu_seconds is not None else resources.cpu * wall
        usage: dict[str, Any] = {
            "wall_seconds": round(wall, 3),
            "cpu_seconds": round(cpu_seconds, 3),
            "cpu_seconds_measured": self.cpu_seconds is not None,
            "max_memory_bytes": self.max_memory_bytes,
            "max_disk_bytes": self.max_disk_bytes,
            "reserved": {
                "cpu": resources.cpu,
                "memory_mb": resources.memory_mb,
                "disk_mb": resources.disk_mb,
                "gpu_type": resources.gpu_type,
                "gpu_count": resources.gpu_count,
            },
            "cost_basis": basis,
        }
        manifest: dict[str, Any] = {
            "files": outputs,
            "metrics": metrics_doc,
            "logs": logs_info,
            "total_bytes": report.total_bytes if report is not None else 0,
        }
        if report is not None and report.rejected:
            manifest["rejected"] = [{"name": r.name, "reason": r.reason} for r in report.rejected[:100]]
        if notes:
            manifest["notes"] = notes
        with tenant_uow(self.org) as db:
            job = self._lock(db)
            if job.attempt != self.attempt or job.status in FINISHED_STATUSES or job.status == _E.QUEUED:
                log.warning("compute_job_result_discarded", job_id=str(self.job_id), attempt=self.attempt)
                if job.status in FINISHED_STATUSES:
                    return job_result(job)
                raise _Superseded()
            assert_transition("execution", job.status, outcome.status)
            job.status = outcome.status
            job.status_reason = outcome.reason
            job.error = outcome.error
            job.exit_code = outcome.exit_code
            job.completed_at = utcnow()
            job.heartbeat_at = job.completed_at
            if self.image_digest and not job.image_digest:
                job.image_digest = self.image_digest[:100]
            job.output_manifest = manifest
            job.artifact_ids = [str(info["artifact_version_id"]) for info in outputs.values()]
            job.logs_artifact_id = uuid.UUID(logs_info["artifact_id"]) if logs_info else None
            job.resource_usage = {**(job.resource_usage or {}), **usage}
            job.cost_usd = cost
            if started is not None:
                record_compute_usage(
                    db,
                    organization_id=job.organization_id,
                    compute_job_id=job.id,
                    backend=job.backend,
                    cpu_seconds=float(cpu_seconds),
                    gpu_seconds=float(resources.gpu_count * wall),
                    gpu_type=resources.gpu_type,
                    memory_mb_seconds=float(resources.memory_mb * wall),
                    wall_seconds=float(wall),
                    cost_usd=cost,
                    cost_basis=basis,
                    project_id=job.project_id,
                    mission_id=job.mission_id,
                    experiment_id=job.experiment_id,
                )
            append_evidence(
                db,
                organization_id=job.organization_id,
                kind="compute_job",
                title=f"Compute job {job.id} {job.status}",
                content={
                    "compute_job_id": str(job.id),
                    "status": job.status,
                    "reason": job.status_reason,
                    "backend": job.backend,
                    "image": job.image,
                    "image_digest": job.image_digest,
                    "command": list(job.command or []),
                    "exit_code": job.exit_code,
                    "network_mode": (job.network_policy or {}).get("mode", "none"),
                    "started_at": started.isoformat() if started else None,
                    "completed_at": job.completed_at.isoformat(),
                    "wall_seconds": usage["wall_seconds"],
                    "outputs": {path: info["sha256"] for path, info in sorted(outputs.items())},
                    "logs_sha256": logs_info.get("sha256") if logs_info else None,
                    "metrics_source": "self_reported" if metrics_doc else None,
                    "cost_usd": str(cost),
                },
            )
            event_type: str = EventType.EXPERIMENT_FAILED
            if outcome.status == _E.SUCCEEDED:
                event_type = EventType.EXPERIMENT_COMPLETED
            elif outcome.status == _E.CANCELLED:
                event_type = EventType.COMPUTE_JOB_UPDATED
            self._emit(
                db,
                job,
                event_type,
                {
                    "status": job.status,
                    "reason": job.status_reason,
                    "exit_code": job.exit_code,
                    "duration_seconds": usage["wall_seconds"],
                    "cost_usd": float(cost),
                    "outputs": len(outputs),
                    "experiment_id": str(job.experiment_id) if job.experiment_id else None,
                    "experiment_run_id": str(job.experiment_run_id) if job.experiment_run_id else None,
                },
            )
            metrics.COMPUTE_JOBS.labels(job.backend, job.status).inc()
            if started is not None:
                metrics.EXPERIMENT_DURATION.labels(job.backend, job.status).observe(wall)
            result = job_result(job)
        # Only after the result is durable: remove the sandbox (a crash before this point re-attaches).
        if self.backend is not None and self.handle is not None:
            try:
                self.backend.cleanup(self.handle)
            except Exception:
                log.warning("compute_job_cleanup_failed", job_id=str(self.job_id), exc_info=True)
        log.info("compute_job_finished", job_id=str(self.job_id), status=result.status, reason=result.reason)
        return result


# =============================================================================================
# Reconciliation
# =============================================================================================
_WORKFLOW_TERMINAL = frozenset({"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"})


def _workflow_alive(db: Session, workflow_run_id: uuid.UUID | None) -> bool:
    """True when a non-terminal workflow is responsible for the job (it will re-run ``run_job`` itself)."""
    if workflow_run_id is None:
        return False
    run = db.get(WorkflowRun, workflow_run_id)
    return run is not None and run.status not in _WORKFLOW_TERMINAL


def _reconcile_emit(db: Session, actor: Actor, job: ComputeJob, event_type: str) -> None:
    emit(
        db,
        organization_id=job.organization_id,
        type=event_type,
        payload={"job_id": str(job.id), "status": job.status, "reason": job.status_reason},
        mission_id=job.mission_id,
        project_id=job.project_id,
        workspace_id=job.workspace_id,
        subject_type="compute_job",
        subject_id=job.id,
        actor=actor,
    )


def reconcile_stale_jobs(organization_id: uuid.UUID, *, limit: int = 100) -> dict[str, list[str]]:
    """Recover jobs whose worker died and jobs whose approval was decided while nobody was waiting.

    * ``PROVISIONING``/``RUNNING`` with a stale heartbeat and a live sandbox: left to its workflow when one is
      still active (the engine retries ``run_job``, which re-attaches), otherwise handed to a new runner.
    * Stale with no sandbox: ``RUNNING`` fails with ``worker_lost``; ``PROVISIONING`` is re-queued and
      dispatched again (a fresh attempt).
    * ``QUEUED`` with a decided approval: approved → dispatched (unless its workflow is waiting for the
      decision itself); rejected / expired / cancelled → ``CANCELLED``.
    """
    report: dict[str, list[str]] = {
        "reattach_dispatched": [],
        "workflow_managed": [],
        "failed": [],
        "requeued": [],
        "dispatched": [],
        "cancelled": [],
        "pending": [],
        "undispatched": [],
    }
    actor = Actor.system(organization_id, label="execution-reconciler")
    cutoff = utcnow() - timedelta(seconds=STALE_AFTER_SECONDS)
    stamp = utcnow().strftime("%Y%m%d%H%M%S")
    with tenant_uow(organization_id) as db:
        stale = [
            (row.id, row.backend)
            for row in db.execute(
                select(ComputeJob.id, ComputeJob.backend)
                .where(
                    ComputeJob.organization_id == organization_id,
                    ComputeJob.status.in_(IN_FLIGHT_STATUSES),
                    or_(ComputeJob.heartbeat_at.is_(None), ComputeJob.heartbeat_at < cutoff),
                )
                .limit(limit)
            ).all()
        ]
        awaiting = [
            row.id
            for row in db.execute(
                select(ComputeJob.id)
                .where(
                    ComputeJob.organization_id == organization_id,
                    ComputeJob.status == _E.QUEUED,
                    ComputeJob.approval_id.is_not(None),
                )
                .limit(limit)
            ).all()
        ]

    for job_id, backend_name in stale:
        try:
            handle = get_backend(backend_name).find_by_job_id(job_id)
        except Exception:
            log.warning("reconcile_backend_unavailable", job_id=str(job_id), exc_info=True)
            report["undispatched"].append(str(job_id))
            continue
        with tenant_uow(organization_id) as db:
            job = db.scalar(select(ComputeJob).where(ComputeJob.id == job_id).with_for_update())
            if job is None or job.status not in IN_FLIGHT_STATUSES:
                continue
            if job.heartbeat_at is not None and job.heartbeat_at >= cutoff:
                continue  # a worker revived it meanwhile
            if handle is not None:
                if _workflow_alive(db, job.workflow_run_id):
                    report["workflow_managed"].append(str(job_id))
                    continue
                mode = dispatch_in_session(db, actor, job, workflow_key=f"attempt-{job.attempt}-reattach-{stamp}")
                report["undispatched" if mode == "none" else "reattach_dispatched"].append(str(job_id))
                continue
            if job.status == _E.PROVISIONING:
                assert_transition("execution", job.status, _E.QUEUED)
                job.status = _E.QUEUED
                job.status_reason = "requeued: provisioning worker lost"
                job.heartbeat_at = None
                job.attempt = int(job.attempt or 1) + 1
                _reconcile_emit(db, actor, job, EventType.COMPUTE_JOB_UPDATED)
                report["requeued"].append(str(job_id))
                if not _workflow_alive(db, job.workflow_run_id):
                    dispatch_in_session(db, actor, job)
                continue
            assert_transition("execution", job.status, _E.FAILED)
            job.status = _E.FAILED
            job.status_reason = "worker_lost"
            job.error = "The worker running this job was lost and its sandbox no longer exists"
            job.completed_at = utcnow()
            _reconcile_emit(db, actor, job, EventType.EXPERIMENT_FAILED)
            metrics.COMPUTE_JOBS.labels(job.backend, _E.FAILED).inc()
            report["failed"].append(str(job_id))

    for job_id in awaiting:
        with tenant_uow(organization_id) as db:
            job = db.scalar(select(ComputeJob).where(ComputeJob.id == job_id).with_for_update())
            if job is None or job.status != _E.QUEUED or job.approval_id is None:
                continue
            state = _approval_status(db, organization_id, job.approval_id)
            if state == ApprovalStatus.PENDING:
                report["pending"].append(str(job_id))
            elif state == ApprovalStatus.APPROVED:
                if _workflow_alive(db, job.workflow_run_id):
                    report["workflow_managed"].append(str(job_id))
                else:
                    mode = dispatch_in_session(db, actor, job)
                    report["undispatched" if mode == "none" else "dispatched"].append(str(job_id))
            else:
                assert_transition("execution", job.status, _E.CANCELLED)
                job.status = _E.CANCELLED
                job.status_reason = f"approval_{(state or 'missing').lower()}"
                job.completed_at = utcnow()
                _reconcile_emit(db, actor, job, EventType.COMPUTE_JOB_UPDATED)
                metrics.COMPUTE_JOBS.labels(job.backend, _E.CANCELLED).inc()
                report["cancelled"].append(str(job_id))
    return report
