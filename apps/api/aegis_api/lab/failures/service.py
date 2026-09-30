"""Failure intelligence service: classify → diagnose → lesson → similar failures → recovery proposal.

:func:`record_failure` runs the deterministic pipeline for one observed failure:

1. **Redact** every text signal (bearer tokens, API keys, ``password=…`` assignments, credentials in URLs, JWTs,
   private keys, AWS key ids and the deployment's own configured secrets) *before* anything is classified or
   stored; the stored traceback keeps its last 20 000 characters and the log excerpt its last 8 000.
2. **Classify** with :func:`engines.lab.failures.classify_failure` (ordered rule table; reproducible) → type,
   subtype, confidence, matched rules, rule-based root cause (``root_cause_source="rule"``) and a normalised
   **signature**.
3. **Recurrence and similarity**: one row per occurrence; ``recurrence_count`` = occurrences of the same signature in
   the organization within 30 days (this one included); ``similar_failure_ids`` = the five most similar recent
   failures the actor can see (signature equality first, then token-shingle similarity of the tracebacks).
4. **Lesson**: an existing ACTIVE/PROPOSED lesson with the same signature in the project is reused
   (``times_applied += 1``); otherwise :func:`engines.lab.failures.extract_lesson` drafts a PROPOSED lesson, mirrored
   into FAILURE memory through the knowledge context's write policy (when that context is deployed).
5. **Recovery proposal**: :func:`engines.lab.failures.propose_recovery` with the experiment's current resources,
   seeds, dependencies and the organization's execution limits; the patch is re-checked by the recovery guardrails
   (:func:`validate_recovery_patch`: only resources/timeout/seeds/parameters/environment/reproducibility may change,
   never network, secrets, code, data or the scientific design). Policy failures and legitimate negative results
   (hypothesis failures) are never auto-recovered.
6. The failure becomes ``DIAGNOSED``; ``FAILURE_ANALYZED`` (+ ``LESSON_LEARNED`` for a new lesson) is emitted and an
   immutable evidence record is appended.

Applying recoveries and recording their outcomes lives in :mod:`aegis_api.lab.failures.recovery`.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import re
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import structlog
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.db.base import utcnow
from aegis_api.errors import AppError, Conflict, ServiceUnavailable, ValidationFailed
from aegis_api.lab.core.access import get_owned, load_project, visible_project_ids
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.audit import audit
from aegis_api.lab.core.errors import InvalidTransition
from aegis_api.lab.core.events import EventType, emit
from aegis_api.lab.core.evidence import append_evidence
from aegis_api.lab.core.locks import advisory_xact_lock
from aegis_api.lab.core.pagination import CursorPage, CursorParams, paginate, paginate_keyset
from aegis_api.lab.failures.schemas import DiagnosisOverride, FailureInput
from aegis_api.lab.models import (
    AgentRun,
    ComputeJob,
    ExecutionEnvironment,
    Experiment,
    ExperimentRun,
    ExperimentVersion,
    Failure,
    Lesson,
    Mission,
    Project,
    StrategyVersion,
)
from aegis_api.logging import _redact_value
from aegis_api.schemas.common import Page, PageParams
from engines.lab.experiment_spec import MAX_SEED_VALUE, ExperimentSpec
from engines.lab.failures import (
    FAILURE_ENGINE_VERSION,
    MAX_SIGNAL_CHARS,
    Classification,
    FailureSignals,
    RecoveryContext,
    RecoveryProposal,
    classify_failure,
    extract_lesson,
    non_finite_metric_keys,
    propose_recovery,
    rank_similar,
)
from engines.lab.states import ExecutionStatus, FailureStatus, FailureType, assert_transition

log = structlog.get_logger("aegis.lab.failures")

MAX_TRACEBACK_CHARS = 20_000
MAX_LOGS_CHARS = 8_000
MAX_RAW_TEXT_CHARS = 256_000
LOG_TAIL_BYTES = 64 * 1024
RECURRENCE_WINDOW_DAYS = 30
SIMILARITY_WINDOW_DAYS = 180
SIMILARITY_CANDIDATES = 500
SIMILAR_LIMIT = 5
SIMILARITY_THRESHOLD = 0.5
MAX_EVIDENCE_ENTRIES = 100
MAX_LESSON_EVIDENCE = 50
LESSON_ACTIVATION_SUCCESSES = 2
MAX_RECOVERY_SEEDS = 64
MAX_PATCH_BYTES = 64 * 1024

#: Failure types that are never recovered automatically (governance decisions; negative results are results).
NO_AUTO_RECOVERY_TYPES: frozenset[str] = frozenset({FailureType.POLICY_FAILURE, FailureType.HYPOTHESIS_FAILURE})

FAILURE_RECORDED = "FAILURE_RECORDED"
FAILURE_DIAGNOSIS_OVERRIDDEN = "FAILURE_DIAGNOSIS_OVERRIDDEN"
FAILURE_STATUS_CHANGED = "FAILURE_STATUS_CHANGED"
LESSON_APPROVED = "LESSON_APPROVED"
LESSON_RETIRED = "LESSON_RETIRED"

LESSON_TRANSITIONS: dict[str, frozenset[str]] = {
    "PROPOSED": frozenset({"ACTIVE", "RETIRED"}),
    "ACTIVE": frozenset({"RETIRED"}),
    "RETIRED": frozenset(),
}
_RUN_TERMINAL = frozenset(
    {
        ExecutionStatus.SUCCEEDED,
        ExecutionStatus.FAILED,
        ExecutionStatus.TIMED_OUT,
        ExecutionStatus.CANCELLED,
        ExecutionStatus.VERIFICATION_PENDING,
        ExecutionStatus.VERIFIED,
    }
)
_RUN_SUCCESS = frozenset({ExecutionStatus.SUCCEEDED, ExecutionStatus.VERIFICATION_PENDING, ExecutionStatus.VERIFIED})
_JOB_FINISHED = frozenset(
    {ExecutionStatus.SUCCEEDED, ExecutionStatus.FAILED, ExecutionStatus.TIMED_OUT, ExecutionStatus.CANCELLED}
)
_TRACEBACK_HEADER = "Traceback (most recent call last):"
_REJECTED_METRIC_RE = re.compile(r"^metric '([^']{1,120})': value must be a finite number")


# =============================================================================================
# Redaction
# =============================================================================================
_ASSIGNMENT_RE = re.compile(
    r"(?i)(\b[\w.-]*(?:passw(?:or)?d|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|credential|"
    r"client[_-]?secret|session[_-]?id|cookie)[\w.-]*[\"']?\s*[:=]\s*)(\"[^\"\n]*\"|'[^'\n]*'|[^\s,;&\"']+)"
)
_URL_CREDENTIALS_RE = re.compile(r"(?i)(\b[a-z][a-z0-9+.-]{1,20}://)[^\s/:@]+:[^\s/@]+@")
_BASIC_AUTH_RE = re.compile(r"(?i)(\bBasic\s+)[A-Za-z0-9+/=]{8,}")
_AWS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")
_PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)")
_HARMLESS_VALUE_RE = re.compile(r"^[-+]?\d+(?:\.\d+)?$|^(?:none|null|true|false)$", re.I)
_SECRET_SETTINGS = (
    "supabase_jwt_secret",
    "supabase_service_role_key",
    "secrets_encryption_key",
    "gemini_api_key",
    "openai_api_key",
    "anthropic_api_key",
    "jwt_secret",
    "temporal_api_key",
    "object_storage_access_key",
    "object_storage_secret_key",
    "metrics_token",
    "api_key_pepper",
)
REDACTED = "[REDACTED]"


def _configured_secrets() -> list[str]:
    settings = get_settings()
    values: list[str] = []
    for name in _SECRET_SETTINGS:
        value = getattr(settings, name, None)
        if isinstance(value, str) and len(value) >= 8:
            values.append(value)
    jwt_map = getattr(settings, "jwt_secret_map", None)
    if isinstance(jwt_map, dict):
        values.extend(v for v in jwt_map.values() if isinstance(v, str) and len(v) >= 8)
    return sorted(set(values), key=len, reverse=True)


def _redact_assignment(match: re.Match[str]) -> str:
    value = match.group(2)
    if _HARMLESS_VALUE_RE.match(value.strip("\"'")):
        return match.group(0)
    return f"{match.group(1)}{REDACTED}"


def redact_text(text: str | None, max_chars: int | None = None) -> str | None:
    """Strip secrets from untrusted program output and keep its informative tail (``max_chars``)."""
    if text is None:
        return None
    value = str(text).replace("\x00", "")
    if len(value) > MAX_RAW_TEXT_CHARS:
        value = value[-MAX_RAW_TEXT_CHARS:]
    for secret in _configured_secrets():
        value = value.replace(secret, REDACTED)
    value = _PRIVATE_KEY_RE.sub("[REDACTED PRIVATE KEY]", value)
    value = _URL_CREDENTIALS_RE.sub(rf"\1{REDACTED}@", value)
    value = _BASIC_AUTH_RE.sub(rf"\1{REDACTED}", value)
    value = _JWT_RE.sub(REDACTED, value)
    value = _AWS_KEY_RE.sub(REDACTED, value)
    value = _ASSIGNMENT_RE.sub(_redact_assignment, value)
    value = str(_redact_value(value))
    if max_chars is not None and len(value) > max_chars:
        dropped = len(value) - max_chars
        value = f"[… {dropped} earlier characters truncated …]\n" + value[-max_chars:]
    return value


# =============================================================================================
# Recovery guardrails and context
# =============================================================================================
RECOVERY_PATCH_KEYS: frozenset[str] = frozenset(
    {"resources", "timeout_seconds", "seeds", "parameters", "environment", "reproducibility"}
)
RECOVERY_RESOURCE_KEYS: frozenset[str] = frozenset({"cpu", "memory_mb", "disk_mb"})
RECOVERY_ENVIRONMENT_KEYS: frozenset[str] = frozenset({"dependencies", "image_digest", "python_version"})
RECOVERY_REPRODUCIBILITY_KEYS: frozenset[str] = frozenset({"deterministic_flags", "require_digest_pin"})
_PINNED_REQUIREMENT_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}(?:\[[A-Za-z0-9_,.-]{1,100}\])?==[A-Za-z0-9][A-Za-z0-9.+!_-]{0,63}$"
)
_REQUIREMENT_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_FLAG_RE = re.compile(r"^([A-Z_][A-Z0-9_]{0,63})=([^\s]{0,200})$")
_SENSITIVE_FLAG_RE = re.compile(r"KEY|TOKEN|SECRET|PASSW|CREDENTIAL|AUTH|COOKIE", re.I)
_DANGEROUS_FLAGS = frozenset(
    {
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "PATH",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "PYTHONHOME",
        "HOME",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "ALL_PROXY",
    }
)
_DIGEST_RE = re.compile(r"sha256:[a-f0-9]{64}")
_PYTHON_VERSION_RE = re.compile(r"^3\.\d{1,2}(\.\d{1,3})?$")


@dataclass(frozen=True)
class RecoveryLimits:
    """Execution caps a recovery must respect (deployment caps narrowed by the organization's policy)."""

    max_cpu: float
    max_memory_mb: int
    max_disk_mb: int
    max_timeout_seconds: int
    max_seeds: int = MAX_RECOVERY_SEEDS

    def context_limits(self) -> dict[str, Any]:
        return {
            "memory_mb": self.max_memory_mb,
            "disk_mb": self.max_disk_mb,
            "timeout_seconds": self.max_timeout_seconds,
            "seeds": self.max_seeds,
            "cpu": self.max_cpu,
        }


def recovery_limits(db: Session, organization_id: uuid.UUID) -> RecoveryLimits:
    """The organization's effective execution limits (see ``execution.service.execution_limits``)."""
    from aegis_api.lab.execution.service import execution_limits

    limits, _images = execution_limits(db, organization_id)
    return RecoveryLimits(
        max_cpu=float(limits.max_cpu),
        max_memory_mb=int(limits.max_memory_mb),
        max_disk_mb=int(limits.max_disk_mb),
        max_timeout_seconds=int(limits.max_timeout_seconds),
    )


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(float(value))


def _requirement_name(requirement: str) -> str | None:
    match = _REQUIREMENT_NAME_RE.match(requirement)
    return re.sub(r"[-_.]+", "-", match.group(1)).lower() if match else None


def _check_resources(value: Any, limits: RecoveryLimits, out: list[str]) -> None:
    if not isinstance(value, Mapping):
        out.append("resources must be an object")
        return
    caps: dict[str, float] = {
        "cpu": limits.max_cpu,
        "memory_mb": float(limits.max_memory_mb),
        "disk_mb": float(limits.max_disk_mb),
    }
    for key, amount in value.items():
        if key not in RECOVERY_RESOURCE_KEYS:
            out.append(f"resources.{key} may not be changed by a recovery (allowed: cpu, memory_mb, disk_mb)")
        elif not _is_number(amount) or float(amount) <= 0:
            out.append(f"resources.{key} must be a positive number")
        elif key != "cpu" and not float(amount).is_integer():
            out.append(f"resources.{key} must be a whole number")
        elif float(amount) > caps[key]:
            out.append(f"resources.{key}={amount} exceeds the execution limit ({caps[key]:g})")


def _check_seeds(value: Any, current: Sequence[Any], limits: RecoveryLimits, out: list[str]) -> None:
    if not isinstance(value, list) or not all(isinstance(s, int) and not isinstance(s, bool) for s in value):
        out.append("seeds must be a list of integers")
        return
    if len(set(value)) != len(value):
        out.append("seeds must be unique")
    if any(s < 0 or s > MAX_SEED_VALUE for s in value):
        out.append(f"seeds must be within [0, {MAX_SEED_VALUE}]")
    if len(value) > limits.max_seeds:
        out.append(f"at most {limits.max_seeds} seeds may be scheduled by a recovery")
    removed = [s for s in current if s not in value]
    if removed:
        out.append(f"a recovery may add seeds but never drop pre-registered ones (dropped: {removed[:10]})")


def _check_environment(value: Any, current: Mapping[str, Any], out: list[str]) -> None:
    if not isinstance(value, Mapping):
        out.append("environment must be an object")
        return
    for key in value:
        if key not in RECOVERY_ENVIRONMENT_KEYS:
            out.append(
                f"environment.{key} may not be changed by a recovery (allowed: dependencies, image_digest, "
                "python_version)"
            )
    if "dependencies" in value:
        deps = value["dependencies"]
        if not isinstance(deps, list) or not all(isinstance(d, str) and d.strip() for d in deps):
            out.append("environment.dependencies must be a list of requirement strings")
        else:
            existing = [str(d) for d in current.get("dependencies") or []]
            kept_names = {_requirement_name(d) for d in deps}
            dropped = [d for d in existing if _requirement_name(d) not in kept_names]
            if dropped:
                out.append(f"a recovery may not remove dependencies (removed: {dropped[:5]})")
            unpinned = [d for d in deps if d not in existing and not _PINNED_REQUIREMENT_RE.match(d.strip())]
            if unpinned:
                out.append(f"new dependencies must be pinned exactly as name==version (got: {unpinned[:5]})")
    if "image_digest" in value and (
        not isinstance(value["image_digest"], str) or not re.fullmatch(_DIGEST_RE, value["image_digest"])
    ):
        out.append("environment.image_digest must look like sha256:<64 hex>")
    if "python_version" in value and (
        not isinstance(value["python_version"], str) or not _PYTHON_VERSION_RE.match(value["python_version"])
    ):
        out.append("environment.python_version must look like 3.x or 3.x.y")


def _check_reproducibility(value: Any, out: list[str]) -> None:
    if not isinstance(value, Mapping):
        out.append("reproducibility must be an object")
        return
    for key in value:
        if key not in RECOVERY_REPRODUCIBILITY_KEYS:
            out.append(f"reproducibility.{key} may not be changed by a recovery")
    flags = value.get("deterministic_flags")
    if "deterministic_flags" in value:
        if not isinstance(flags, list) or not all(isinstance(f, str) for f in flags):
            out.append("reproducibility.deterministic_flags must be a list of NAME=VALUE strings")
        else:
            for flag in flags:
                match = _FLAG_RE.match(flag)
                if match is None:
                    out.append(f"invalid deterministic flag {flag[:80]!r} (expected NAME=VALUE)")
                elif match.group(1) in _DANGEROUS_FLAGS or _SENSITIVE_FLAG_RE.search(match.group(1)):
                    out.append(f"deterministic flag {match.group(1)} is not allowed")
    if "require_digest_pin" in value and not isinstance(value["require_digest_pin"], bool):
        out.append("reproducibility.require_digest_pin must be a boolean")


def validate_recovery_patch(patch: Any, current_spec: Mapping[str, Any], limits: RecoveryLimits) -> list[str]:
    """Guardrails for any recovery patch (rule-based or model-proposed) → list of violations (empty = allowed).

    A recovery may only change resources (cpu/memory/disk within the execution limits), the timeout, the seeds
    (adding, never dropping pre-registered ones), parameters (no deletions), the environment's dependencies (new
    ones pinned; none removed), image digest and Python version, and deterministic execution flags. It may never
    touch the network policy, secrets, command, code, datasets, metrics, success criteria, statistical plan,
    baseline or objective.
    """
    if not isinstance(patch, Mapping) or not patch:
        return ["the recovery patch must be a non-empty object"]
    try:
        encoded = json.dumps(patch, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError):
        return ["the recovery patch must be JSON without NaN or infinity"]
    violations: list[str] = []
    if len(encoded.encode("utf-8")) > MAX_PATCH_BYTES:
        violations.append(f"the recovery patch exceeds {MAX_PATCH_BYTES} bytes")
    for key in patch:
        if key not in RECOVERY_PATCH_KEYS:
            violations.append(
                f"'{key}' may not be changed by a recovery (allowed: {', '.join(sorted(RECOVERY_PATCH_KEYS))})"
            )
    if "resources" in patch:
        _check_resources(patch["resources"], limits, violations)
    if "timeout_seconds" in patch:
        timeout = patch["timeout_seconds"]
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
            violations.append("timeout_seconds must be a positive integer")
        elif timeout > limits.max_timeout_seconds:
            violations.append(f"timeout_seconds={timeout} exceeds the execution limit ({limits.max_timeout_seconds})")
    if "seeds" in patch:
        _check_seeds(patch["seeds"], list(current_spec.get("seeds") or []), limits, violations)
    if "parameters" in patch:
        params = patch["parameters"]
        if not isinstance(params, Mapping):
            violations.append("parameters must be an object")
        elif any(v is None for v in params.values()):
            violations.append("a recovery may not delete parameters")
    if "environment" in patch:
        _check_environment(patch["environment"], dict(current_spec.get("environment") or {}), violations)
    if "reproducibility" in patch:
        _check_reproducibility(patch["reproducibility"], violations)
    return violations


def normalized_spec(spec: Mapping[str, Any] | None) -> dict[str, Any]:
    """The spec with defaults materialised (the raw document when it no longer parses)."""
    if not spec:
        return {}
    try:
        return ExperimentSpec.model_validate(dict(spec)).model_dump(mode="json")
    except PydanticValidationError:
        return dict(spec)


def _digest_of(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = _DIGEST_RE.search(value)
    return match.group(0) if match else None


def known_dependency_versions(db: Session, organization_id: uuid.UUID, *, limit: int = 50) -> dict[str, str]:
    """Pinned versions actually used by the organization's recent execution environments (never guessed)."""
    known: dict[str, str] = {}
    rows = db.scalars(
        select(ExecutionEnvironment)
        .where(ExecutionEnvironment.organization_id == organization_id)
        .order_by(ExecutionEnvironment.created_at.desc())
        .limit(limit)
    )
    for env in rows:
        for requirement in env.dependencies or []:
            if isinstance(requirement, str) and _PINNED_REQUIREMENT_RE.match(requirement.strip()):
                name, version = requirement.strip().split("==", 1)
                known.setdefault(re.sub(r"\[.*\]$", "", name), version)
    return known


def build_recovery_context(
    db: Session,
    organization_id: uuid.UUID,
    spec: Mapping[str, Any],
    *,
    run: ExperimentRun | None = None,
    job: ComputeJob | None = None,
    limits: RecoveryLimits,
) -> RecoveryContext:
    environment = dict(spec.get("environment") or {})
    reproducibility = dict(spec.get("reproducibility") or {})
    digest = (
        _digest_of(environment.get("image_digest"))
        or _digest_of((run.environment_manifest or {}).get("image_digest") if run is not None else None)
        or _digest_of(job.image_digest if job is not None else None)
    )
    timeout = spec.get("timeout_seconds")
    return RecoveryContext(
        resources=dict(spec.get("resources") or {}),
        limits=limits.context_limits(),
        dependencies=[str(d) for d in environment.get("dependencies") or []],
        known_versions=known_dependency_versions(db, organization_id),
        seeds=[int(s) for s in spec.get("seeds") or [] if isinstance(s, int) and not isinstance(s, bool)],
        statistical_plan=dict(spec.get("statistical_plan") or {}),
        timeout_seconds=int(timeout) if isinstance(timeout, int) and not isinstance(timeout, bool) else None,
        image_digest=digest,
        deterministic_flags=[str(f) for f in reproducibility.get("deterministic_flags") or []],
        parameters=dict(spec.get("parameters") or {}),
    )


def recovery_record(
    classification: Classification,
    proposal: RecoveryProposal,
    spec: Mapping[str, Any],
    limits: RecoveryLimits,
    *,
    source: str = "rule",
) -> tuple[dict[str, Any], str]:
    """The stored ``recovery_action`` document and the resulting ``recovery_status``."""
    applicable = classification.failure_type not in NO_AUTO_RECOVERY_TYPES
    patch = dict(proposal.spec_patch)
    violations = validate_recovery_patch(patch, spec, limits) if patch else []
    actionable = applicable and bool(patch) and not violations and bool(spec)
    record: dict[str, Any] = {
        **proposal.model_dump(mode="json"),
        "source": source,
        "applicable": applicable,
        "automatic": actionable and not proposal.requires_approval,
        "guardrail_violations": violations,
        "limits": limits.context_limits(),
        "status": "proposed" if actionable else "advice",
        "engine_version": FAILURE_ENGINE_VERSION,
    }
    if not applicable:
        record["note"] = "Governance decisions and legitimate negative results are never recovered automatically."
    elif patch and not spec:
        record["note"] = "No experiment specification is linked, so the patch cannot be applied."
    return record, ("proposed" if actionable else "none")


# =============================================================================================
# Loading helpers
# =============================================================================================
def get_failure(db: Session, actor: Actor, failure_id: uuid.UUID | str, *, permission: str = "failure:read") -> Failure:
    failure = get_owned(db, Failure, failure_id, actor, label="Failure")
    if failure.project_id is not None:
        load_project(db, actor, failure.project_id, permission)
    else:
        actor.require(permission)
    return failure


def get_lesson(db: Session, actor: Actor, lesson_id: uuid.UUID | str, *, permission: str = "failure:read") -> Lesson:
    lesson = get_owned(db, Lesson, lesson_id, actor, label="Lesson")
    if lesson.project_id is not None:
        load_project(db, actor, lesson.project_id, permission)
    else:
        actor.require(permission)
    return lesson


def _same_project(row: Any, project: Project, label: str) -> None:
    if getattr(row, "project_id", None) not in (None, project.id):
        raise ValidationFailed(f"{label} belongs to a different project")


@dataclass
class _Links:
    mission: Mission | None = None
    experiment: Experiment | None = None
    run: ExperimentRun | None = None
    job: ComputeJob | None = None
    agent_run: AgentRun | None = None
    strategy_version_id: uuid.UUID | None = None


def _load_links(db: Session, actor: Actor, project: Project, body: FailureInput) -> _Links:
    links = _Links()
    if body.experiment_run_id is not None:
        links.run = get_owned(db, ExperimentRun, body.experiment_run_id, actor, label="Experiment run")
        _same_project(links.run, project, "Experiment run")
    experiment_id = body.experiment_id or (links.run.experiment_id if links.run else None)
    if experiment_id is not None:
        links.experiment = get_owned(db, Experiment, experiment_id, actor, label="Experiment")
        _same_project(links.experiment, project, "Experiment")
        if links.run is not None and links.run.experiment_id != links.experiment.id:
            raise ValidationFailed("experiment_run_id does not belong to experiment_id")
    job_id = body.compute_job_id or (links.run.compute_job_id if links.run else None)
    if job_id is not None:
        links.job = get_owned(db, ComputeJob, job_id, actor, label="Compute job")
        _same_project(links.job, project, "Compute job")
    mission_id = body.mission_id or (links.experiment.mission_id if links.experiment else None)
    if mission_id is not None:
        links.mission = get_owned(db, Mission, mission_id, actor, label="Mission")
        _same_project(links.mission, project, "Mission")
    if body.agent_run_id is not None:
        links.agent_run = get_owned(db, AgentRun, body.agent_run_id, actor, label="Agent run")
        _same_project(links.agent_run, project, "Agent run")
    if body.strategy_version_id is not None:
        get_owned(db, StrategyVersion, body.strategy_version_id, actor, label="Strategy version")
        links.strategy_version_id = body.strategy_version_id
    return links


def _spec_for(db: Session, links: _Links) -> dict[str, Any]:
    version: ExperimentVersion | None = None
    if links.experiment is not None and links.experiment.current_version_id is not None:
        version = db.get(ExperimentVersion, links.experiment.current_version_id)
    if version is None and links.run is not None:
        version = db.get(ExperimentVersion, links.run.experiment_version_id)
    return normalized_spec(version.spec if version is not None else None)


def _signals(stage: str, raw: Mapping[str, Any]) -> FailureSignals:
    data = dict(raw)
    data["stage"] = stage
    for key in ("traceback", "logs", "error_message"):
        if data.get(key) is not None:
            data[key] = redact_text(str(data[key]), MAX_SIGNAL_CHARS)
    for key in ("error_class", "provider_error_class", "tool_name", "reproduction_verdict", "policy_decision"):
        if isinstance(data.get(key), str):
            data[key] = redact_text(data[key], 200)
    try:
        return FailureSignals.model_validate(data)
    except PydanticValidationError as exc:
        raise ValidationFailed(
            "Invalid failure signals",
            details=[{"field": ".".join(map(str, e["loc"])), "message": e["msg"]} for e in exc.errors()],
        ) from exc


def _signals_summary(signals: FailureSignals) -> dict[str, Any]:
    summary = signals.model_dump(mode="json", exclude={"traceback", "logs", "error_message", "metrics"})
    summary["has_traceback"] = bool(signals.traceback)
    summary["has_logs"] = bool(signals.logs)
    if signals.metrics is not None:
        summary["metric_names"] = sorted(str(k) for k in signals.metrics)[:100]
        summary["non_finite_metrics"] = non_finite_metric_keys(signals.metrics)[:100]
    return {k: v for k, v in summary.items() if v not in (None, [], {})}


def _signature_text(signals: FailureSignals) -> str | None:
    return signals.traceback or signals.error_message or signals.logs


def _similar(
    db: Session, actor: Actor, classification: Classification, text: str | None, *, exclude: uuid.UUID | None
) -> list[str]:
    since = utcnow() - timedelta(days=SIMILARITY_WINDOW_DAYS)
    stmt = select(Failure).where(Failure.organization_id == actor.organization_id, Failure.created_at >= since)
    visible = visible_project_ids(db, actor)
    if visible is not None:
        stmt = stmt.where(or_(Failure.project_id.in_(visible), Failure.project_id.is_(None)))
    if exclude is not None:
        stmt = stmt.where(Failure.id != exclude)
    candidates = db.scalars(stmt.order_by(Failure.created_at.desc()).limit(SIMILARITY_CANDIDATES)).all()
    ranked = rank_similar(
        text,
        ((str(f.id), f.signature, f.traceback or f.logs_excerpt or f.root_cause) for f in candidates),
        SIMILARITY_THRESHOLD,
        target_signature=classification.signature,
        limit=SIMILAR_LIMIT,
    )
    return [failure_id for failure_id, _score in ranked]


def _recurrence(db: Session, organization_id: uuid.UUID, signature: str) -> int:
    since = utcnow() - timedelta(days=RECURRENCE_WINDOW_DAYS)
    prior = db.scalar(
        select(func.count(Failure.id)).where(
            Failure.organization_id == organization_id,
            Failure.signature == signature,
            Failure.created_at >= since,
        )
    )
    return int(prior or 0) + 1


# =============================================================================================
# Lessons and memory
# =============================================================================================
def _memory_source(actor: Actor) -> str:
    if actor.kind in ("user", "api_key"):
        return "human"
    if actor.kind in ("agent", "service_account"):
        return "agent"
    return "system"


def _write_lesson_memory(db: Session, actor: Actor, lesson: Lesson, failure: Failure) -> uuid.UUID | None:
    """Mirror a lesson into FAILURE memory through the knowledge context's write policy (when deployed)."""
    try:
        module = importlib.import_module("aegis_api.lab.knowledge.memory")
    except ModuleNotFoundError as exc:
        if exc.name not in ("aegis_api.lab.knowledge.memory", "aegis_api.lab.knowledge"):
            raise
        log.info("lesson_memory_skipped", reason="knowledge memory service not deployed", lesson_id=str(lesson.id))
        return None
    write_memory = getattr(module, "write_memory", None)
    if write_memory is None:
        return None
    schema = getattr(module, "MemoryWrite", None)
    if schema is None:
        try:
            schema = getattr(importlib.import_module("aegis_api.lab.knowledge.schemas"), "MemoryWrite", None)
        except ModuleNotFoundError:
            schema = None
    provenance: dict[str, Any] = {
        "component": "failures.lessons",
        "lesson_id": str(lesson.id),
        "failure_id": str(failure.id),
        "signature": lesson.signature,
        "engine_version": FAILURE_ENGINE_VERSION,
    }
    for key, value in (
        ("user_id", actor.user_id),
        ("agent_run_id", actor.agent_run_id),
        ("mission_id", failure.mission_id),
        ("experiment_run_id", failure.experiment_run_id),
    ):
        if value is not None:
            provenance[key] = str(value)
    recommendation = dict(lesson.recommendation or {})
    action = str(recommendation.get("action") or "")
    subtype = str((recommendation.get("applies_to") or {}).get("subtype") or "")
    payload: dict[str, Any] = {
        "category": "FAILURE",
        "scope": "project",
        "project_id": str(failure.project_id) if failure.project_id else None,
        "mission_id": str(failure.mission_id) if failure.mission_id else None,
        "title": f"Lesson: {lesson.failure_type} ({subtype})"[:500],
        "content": f"{lesson.statement}\nRecommended action: {action}".strip(),
        "source_type": _memory_source(actor),
        "source_ref": {"type": "lesson", "id": str(lesson.id), "failure_id": str(failure.id)},
        "confidence": float(lesson.confidence),
        "provenance": provenance,
        "tags": [t for t in ("lesson", lesson.failure_type.lower(), subtype, action) if t],
        "sensitivity": "normal",
    }
    try:
        with db.begin_nested():
            memory = write_memory(db, actor, schema.model_validate(payload) if schema is not None else payload)
    except (AppError, PydanticValidationError, ValueError, TypeError) as exc:
        log.warning("lesson_memory_write_failed", lesson_id=str(lesson.id), error=type(exc).__name__)
        return None
    memory_id = getattr(memory, "id", None)
    return memory_id if isinstance(memory_id, uuid.UUID) else None


def append_bounded(items: Iterable[Any], entry: Any, limit: int) -> list[Any]:
    out = [*list(items or []), entry]
    return out[-limit:]


def _link_lesson(
    db: Session, actor: Actor, failure: Failure, classification: Classification, proposal: RecoveryProposal
) -> tuple[Lesson, bool]:
    """Reuse the project's ACTIVE/PROPOSED lesson for this signature, or draft a new PROPOSED one."""
    advisory_xact_lock(db, f"lesson:{actor.organization_id}:{failure.project_id}:{classification.signature}")
    existing = db.scalars(
        select(Lesson)
        .where(
            Lesson.organization_id == actor.organization_id,
            Lesson.project_id == failure.project_id,
            Lesson.signature == classification.signature,
            Lesson.status.in_(("ACTIVE", "PROPOSED")),
        )
        .order_by(Lesson.created_at.desc())
        .limit(10)
    ).all()
    entry = {"failure_id": str(failure.id), "at": utcnow().isoformat()}
    if existing:
        lesson = sorted(existing, key=lambda row: row.status != "ACTIVE")[0]
        lesson.times_applied = int(lesson.times_applied or 0) + 1
        lesson.evidence = append_bounded(lesson.evidence, {**entry, "event": "reused"}, MAX_LESSON_EVIDENCE)
        db.flush()
        return lesson, False
    draft = extract_lesson(classification, proposal)
    lesson = Lesson(
        id=uuid.uuid4(),
        organization_id=actor.organization_id,
        workspace_id=failure.workspace_id,
        project_id=failure.project_id,
        failure_type=draft.failure_type.value,
        signature=draft.signature,
        statement=draft.statement,
        recommendation=dict(draft.recommendation),
        evidence=[{**entry, "event": "extracted"}],
        confidence=draft.confidence,
        status="PROPOSED",
        source="rule",
        times_applied=1,
        times_succeeded=0,
    )
    db.add(lesson)
    db.flush()
    lesson.memory_id = _write_lesson_memory(db, actor, lesson, failure)
    db.flush()
    return lesson, True


# =============================================================================================
# Events and evidence
# =============================================================================================
def failure_event(db: Session, actor: Actor, failure: Failure, phase: str, **extra: Any) -> None:
    emit(
        db,
        organization_id=failure.organization_id,
        type=EventType.FAILURE_ANALYZED,
        payload={
            "failure_id": str(failure.id),
            "phase": phase,
            "failure_type": failure.failure_type,
            "status": failure.status,
            "signature": failure.signature,
            "recovery_status": failure.recovery_status,
            **extra,
        },
        mission_id=failure.mission_id,
        project_id=failure.project_id,
        workspace_id=failure.workspace_id,
        subject_type="failure",
        subject_id=failure.id,
        actor=actor,
    )


def lesson_event(db: Session, actor: Actor, lesson: Lesson, phase: str, **extra: Any) -> None:
    emit(
        db,
        organization_id=lesson.organization_id,
        type=EventType.LESSON_LEARNED,
        payload={
            "lesson_id": str(lesson.id),
            "phase": phase,
            "failure_type": lesson.failure_type,
            "signature": lesson.signature,
            "status": lesson.status,
            "confidence": lesson.confidence,
            "times_applied": lesson.times_applied,
            "times_succeeded": lesson.times_succeeded,
            **extra,
        },
        project_id=lesson.project_id,
        workspace_id=lesson.workspace_id,
        subject_type="lesson",
        subject_id=lesson.id,
        actor=actor,
    )


def failure_evidence(db: Session, failure: Failure, title: str, content: dict[str, Any], *, kind: str) -> str:
    record = append_evidence(
        db,
        organization_id=failure.organization_id,
        kind=kind,
        title=title,
        content={"failure_id": str(failure.id), **content},
    )
    failure.evidence = append_bounded(
        failure.evidence,
        {"kind": "evidence_record", "evidence_id": str(record.id), "title": title[:200]},
        MAX_EVIDENCE_ENTRIES,
    )
    return str(record.id)


def _detected_by(actor: Actor) -> str:
    if actor.is_human:
        return "human"
    return "agent" if actor.kind == "agent" else "platform"


def _sha256(text: str | None) -> str | None:
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None


# =============================================================================================
# Recording
# =============================================================================================
def _find_by_request_key(db: Session, organization_id: uuid.UUID, key: str) -> Failure | None:
    return db.scalars(
        select(Failure)
        .where(
            Failure.organization_id == organization_id,
            Failure.evidence.contains([{"kind": "request", "idempotency_key": key}]),
        )
        .limit(1)
    ).first()


def record_failure(
    db: Session,
    actor: Actor,
    data: FailureInput | Mapping[str, Any],
    *,
    detected_by: str | None = None,
    idempotency_key: str | None = None,
) -> Failure:
    """Run the failure-intelligence pipeline (see module docstring) → a ``DIAGNOSED`` failure.

    ``failure:write`` in the project. ``idempotency_key`` (workflow activities) returns the failure already
    recorded for the same request.
    """
    try:
        body = data if isinstance(data, FailureInput) else FailureInput.model_validate(dict(data))
    except PydanticValidationError as exc:
        raise ValidationFailed(
            "Invalid failure",
            details=[{"field": ".".join(map(str, e["loc"])), "message": e["msg"]} for e in exc.errors()],
        ) from exc
    project = load_project(db, actor, body.project_id, "failure:write")
    if idempotency_key is not None:
        advisory_xact_lock(db, f"failure-request:{actor.organization_id}:{idempotency_key}")
        existing = _find_by_request_key(db, actor.organization_id, idempotency_key)
        if existing is not None:
            return existing
    links = _load_links(db, actor, project, body)
    signals = _signals(body.stage, body.signals)
    classification = classify_failure(signals)
    advisory_xact_lock(db, f"failure-signature:{actor.organization_id}:{classification.signature}")
    text = _signature_text(signals)
    similar_ids = _similar(db, actor, classification, text, exclude=None)
    recurrence = _recurrence(db, actor.organization_id, classification.signature)
    spec = _spec_for(db, links)
    limits = recovery_limits(db, actor.organization_id)
    context = build_recovery_context(db, actor.organization_id, spec, run=links.run, job=links.job, limits=limits)
    proposal = propose_recovery(classification, context)
    recovery_action, recovery_status = recovery_record(classification, proposal, spec, limits)
    traceback = redact_text(signals.traceback, MAX_TRACEBACK_CHARS)
    logs_excerpt = redact_text(signals.logs or signals.error_message, MAX_LOGS_CHARS)
    title = " ".join((body.title or "").split()) or (
        f"{classification.failure_type.value} ({classification.subtype}): {classification.root_cause}"
    )
    evidence: list[dict[str, Any]] = [
        {
            "kind": "classification",
            "rule": classification.rule_id,
            "matched_rules": classification.matched_rules,
            "evidence": classification.evidence,
            "engine_version": classification.engine_version,
        }
    ]
    if idempotency_key is not None:
        evidence.append({"kind": "request", "idempotency_key": idempotency_key})
    failure = Failure(
        id=uuid.uuid4(),
        organization_id=actor.organization_id,
        workspace_id=project.workspace_id,
        project_id=project.id,
        mission_id=links.mission.id if links.mission else None,
        experiment_id=links.experiment.id if links.experiment else None,
        experiment_run_id=links.run.id if links.run else None,
        compute_job_id=links.job.id if links.job else None,
        agent_run_id=links.agent_run.id if links.agent_run else None,
        strategy_version_id=links.strategy_version_id,
        failure_type=classification.failure_type.value,
        signature=classification.signature,
        title=(redact_text(title) or classification.failure_type.value)[:300],
        detected_at=utcnow(),
        detected_by=detected_by or _detected_by(actor),
        evidence=evidence,
        traceback=traceback,
        logs_excerpt=logs_excerpt,
        classification={**classification.model_dump(mode="json"), "signals": _signals_summary(signals)},
        root_cause=classification.root_cause,
        root_cause_source="rule",
        confidence=classification.confidence,
        recovery_action=recovery_action,
        recovery_status=recovery_status,
        recurrence_count=recurrence,
        similar_failure_ids=similar_ids,
        status=FailureStatus.OPEN,
    )
    db.add(failure)
    db.flush()
    lesson, lesson_created = _link_lesson(db, actor, failure, classification, proposal)
    failure.lesson_id = lesson.id
    assert_transition("failure", failure.status, FailureStatus.DIAGNOSED)
    failure.status = FailureStatus.DIAGNOSED
    failure_evidence(
        db,
        failure,
        f"Failure {classification.failure_type.value}/{classification.subtype}",
        {
            "failure_type": classification.failure_type.value,
            "subtype": classification.subtype,
            "signature": classification.signature,
            "matched_rules": classification.matched_rules,
            "confidence": classification.confidence,
            "root_cause": classification.root_cause,
            "engine_version": classification.engine_version,
            "stage": signals.stage,
            "experiment_run_id": str(failure.experiment_run_id) if failure.experiment_run_id else None,
            "compute_job_id": str(failure.compute_job_id) if failure.compute_job_id else None,
            "recurrence_count": recurrence,
            "similar_failure_ids": similar_ids,
            "recovery": {
                "action": recovery_action.get("action"),
                "requires_approval": recovery_action.get("requires_approval"),
                "spec_patch": recovery_action.get("spec_patch"),
                "status": recovery_status,
            },
            "lesson_id": str(lesson.id),
            "traceback_sha256": _sha256(traceback),
            "logs_sha256": _sha256(logs_excerpt),
        },
        kind="failure",
    )
    audit(
        db,
        actor,
        FAILURE_RECORDED,
        "failure",
        failure.id,
        after={"failure_type": failure.failure_type, "signature": failure.signature, "stage": signals.stage},
    )
    failure_event(
        db,
        actor,
        failure,
        "recorded",
        subtype=classification.subtype,
        confidence=classification.confidence,
        recurrence_count=recurrence,
        similar_failure_ids=similar_ids,
        recovery_action=recovery_action.get("action"),
        lesson_id=str(lesson.id),
    )
    if lesson_created:
        lesson_event(db, actor, lesson, "proposed", failure_id=str(failure.id))
    db.flush()
    return failure


# -- from a run ------------------------------------------------------------------------------------
def _read_logs_tail(db: Session, actor: Actor, manifest: Mapping[str, Any], notes: list[str]) -> str | None:
    logs = manifest.get("logs") if isinstance(manifest.get("logs"), dict) else None
    version_id = (logs or {}).get("artifact_version_id")
    if not version_id:
        return None
    from aegis_api.lab.data.artifacts import open_artifact_stream

    limit = int(get_settings().execution_max_log_bytes) + 1024 * 1024
    tail = bytearray()
    streamed = 0
    try:
        for chunk in open_artifact_stream(db, actor, version_id):
            streamed += len(chunk)
            tail.extend(chunk)
            if len(tail) > LOG_TAIL_BYTES:
                del tail[: len(tail) - LOG_TAIL_BYTES]
            if streamed > limit:
                notes.append("log object larger than the execution log limit; only its tail was read")
                break
    except ServiceUnavailable:
        raise
    except AppError as exc:
        notes.append(f"logs unavailable: {exc.code}")
        return None
    return bytes(tail).decode("utf-8", "replace")


def _extract_traceback(text: str | None) -> str | None:
    if not text:
        return None
    index = text.rfind(_TRACEBACK_HEADER)
    return text[index:] if index >= 0 else None


def signals_from_run(
    db: Session, actor: Actor, run: ExperimentRun, job: ComputeJob | None
) -> tuple[dict[str, Any], list[str]]:
    """Platform observations of a finished run → ``(signals, notes)`` (logs tail read with a bounded stream)."""
    notes: list[str] = []
    reasons = {r for r in (run.status_reason, job.status_reason if job else None) if r}
    exit_code = run.exit_code if run.exit_code is not None else (job.exit_code if job else None)
    timed_out = (
        run.status == ExecutionStatus.TIMED_OUT
        or (job is not None and job.status == ExecutionStatus.TIMED_OUT)
        or bool(reasons & {"timeout", "deadline_exceeded"})
    )
    manifest: dict[str, Any] = dict(job.output_manifest or {}) if job is not None else {}
    logs = _read_logs_tail(db, actor, manifest, notes) if manifest else None
    signals: dict[str, Any] = {
        "exit_code": exit_code,
        "oom_killed": "oom" in reasons,
        "timed_out": timed_out,
        "error_message": run.error or (job.error if job else None),
        "logs": logs,
        "traceback": _extract_traceback(logs),
    }
    if job is not None and job.status in _JOB_FINISHED and manifest:
        doc = manifest.get("metrics")
        signals["metrics_file_present"] = doc is not None
        if isinstance(doc, dict):
            metrics: dict[str, Any] = {
                str(k): v for k, v in (doc.get("values") or {}).items() if isinstance(v, int | float)
            }
            for error in doc.get("errors") or []:
                match = _REJECTED_METRIC_RE.match(str(error))
                if match:
                    metrics[match.group(1)] = "nan"
                    notes.append(f"metric {match.group(1)!r} was rejected as non-finite or non-numeric")
            signals["metrics"] = metrics
    return {k: v for k, v in signals.items() if v is not None}, notes


def _has_failure_signal(run: ExperimentRun, signals: Mapping[str, Any]) -> bool:
    if run.status in (ExecutionStatus.FAILED, ExecutionStatus.TIMED_OUT):
        return True
    if signals.get("exit_code") not in (None, 0) or signals.get("oom_killed") or signals.get("timed_out"):
        return True
    if signals.get("metrics_file_present") is False:
        return True
    metrics = signals.get("metrics")
    return isinstance(metrics, dict) and (not metrics or bool(non_finite_metric_keys(metrics)))


def record_failure_from_run(db: Session, actor: Actor, experiment_run_id: uuid.UUID | str) -> Failure:
    """Record (once) the failure of a finished experiment run from platform observations; idempotent per run."""
    run = get_owned(db, ExperimentRun, experiment_run_id, actor, label="Experiment run")
    load_project(db, actor, run.project_id, "failure:write")
    advisory_xact_lock(db, f"failure-run:{run.id}")
    existing = db.scalars(
        select(Failure).where(Failure.experiment_run_id == run.id).order_by(Failure.created_at.asc()).limit(1)
    ).first()
    if existing is not None:
        return existing
    if run.status not in _RUN_TERMINAL:
        raise Conflict("The run has not finished yet", code="run_not_finished")
    if run.status == ExecutionStatus.CANCELLED:
        raise Conflict("Cancelled runs are not failures", code="run_cancelled")
    job = db.get(ComputeJob, run.compute_job_id) if run.compute_job_id else None
    if job is not None and job.organization_id != run.organization_id:
        job = None
    signals, notes = signals_from_run(db, actor, run, job)
    if not _has_failure_signal(run, signals):
        raise Conflict(
            "The run succeeded and produced valid metrics; there is no failure to record", code="run_not_failed"
        )
    failure = record_failure(
        db,
        actor,
        FailureInput(
            stage="execution",
            project_id=run.project_id,
            mission_id=run.mission_id,
            experiment_id=run.experiment_id,
            experiment_run_id=run.id,
            compute_job_id=job.id if job else None,
            signals=signals,
        ),
        detected_by="platform",
    )
    if notes:
        failure.evidence = append_bounded(
            failure.evidence, {"kind": "notes", "notes": notes[:20]}, MAX_EVIDENCE_ENTRIES
        )
        db.flush()
    return failure


# =============================================================================================
# Human diagnosis, transitions
# =============================================================================================
def classification_of(failure: Failure) -> Classification:
    """The classification governing the failure (rule classification, with a human re-label applied)."""
    data = {k: v for k, v in dict(failure.classification or {}).items() if k in Classification.model_fields}
    data["failure_type"] = failure.failure_type
    data.setdefault("subtype", "unknown")
    data.setdefault("confidence", float(failure.confidence or 0.0))
    data.setdefault("matched_rules", ["unknown"])
    data.setdefault("evidence", {})
    data["signature"] = failure.signature
    human = (failure.classification or {}).get("human")
    if isinstance(human, dict) and human.get("subtype"):
        data["subtype"] = human["subtype"]
    data["root_cause"] = failure.root_cause or data.get("root_cause") or "unknown"
    return Classification.model_validate(data)


def override_diagnosis(db: Session, actor: Actor, failure_id: uuid.UUID | str, data: DiagnosisOverride) -> Failure:
    """Record a human root cause (``root_cause_source="human"``); the rule classification is kept."""
    actor.require_human("overriding a failure diagnosis")
    failure = get_failure(db, actor, failure_id, permission="failure:write")
    retype = data.failure_type is not None and data.failure_type.value != failure.failure_type
    if retype and failure.status == FailureStatus.RECOVERING:
        raise Conflict(
            "A failure cannot be re-labelled while its recovery is being tested", code="recovery_in_progress"
        )
    previous = {
        "root_cause": failure.root_cause,
        "root_cause_source": failure.root_cause_source,
        "failure_type": failure.failure_type,
    }
    classification = dict(failure.classification or {})
    classification["human"] = {
        "root_cause": data.root_cause,
        "failure_type": data.failure_type.value if data.failure_type else None,
        "subtype": data.subtype,
        "reason": data.reason,
        "user_id": str(actor.user_id) if actor.user_id else None,
        "at": utcnow().isoformat(),
        "previous": previous,
    }
    failure.classification = classification
    failure.root_cause = data.root_cause
    failure.root_cause_source = "human"
    if retype and data.failure_type is not None:
        failure.failure_type = data.failure_type.value
        human_cls = classification_of(failure).model_copy(
            update={"subtype": data.subtype or "human_override", "matched_rules": ["human.override"]}
        )
        spec = _spec_for(db, _links_of(db, failure))
        limits = recovery_limits(db, failure.organization_id)
        context = build_recovery_context(db, failure.organization_id, spec, limits=limits)
        proposal = propose_recovery(human_cls, context)
        record, status = recovery_record(human_cls, proposal, spec, limits, source="human_diagnosis")
        record["superseded"] = {k: failure.recovery_action.get(k) for k in ("action", "spec_patch", "source")}
        failure.recovery_action = record
        failure.recovery_status = status
    if failure.status == FailureStatus.OPEN:
        assert_transition("failure", failure.status, FailureStatus.DIAGNOSED)
        failure.status = FailureStatus.DIAGNOSED
    failure_evidence(
        db,
        failure,
        "Human failure diagnosis",
        {
            "root_cause": data.root_cause,
            "failure_type": failure.failure_type,
            "reason": data.reason,
            "previous": previous,
        },
        kind="failure_diagnosis",
    )
    audit(
        db,
        actor,
        FAILURE_DIAGNOSIS_OVERRIDDEN,
        "failure",
        failure.id,
        before=previous,
        after={"root_cause_source": "human", "failure_type": failure.failure_type},
    )
    failure_event(db, actor, failure, "diagnosis_overridden", root_cause_source="human")
    db.flush()
    return failure


def _links_of(db: Session, failure: Failure) -> _Links:
    links = _Links()
    if failure.experiment_id is not None:
        links.experiment = db.get(Experiment, failure.experiment_id)
    if failure.experiment_run_id is not None:
        links.run = db.get(ExperimentRun, failure.experiment_run_id)
    return links


def transition_failure(db: Session, actor: Actor, failure_id: uuid.UUID | str, status: str, reason: str) -> Failure:
    """Manual lifecycle change (``failure:write``): WONT_FIX, RESOLVED, OPEN (reopen) or DIAGNOSED.

    ``RECOVERING`` is only entered by applying a recovery.
    """
    failure = get_failure(db, actor, failure_id, permission="failure:write")
    if status == FailureStatus.RECOVERING:
        raise ValidationFailed("Use POST /failures/{id}/recovery/apply to start a recovery")
    clean = " ".join((reason or "").split())
    if not 3 <= len(clean) <= 2000:
        raise ValidationFailed("reason must be 3-2000 characters")
    previous = failure.status
    assert_transition("failure", previous, status)
    failure.status = status
    failure.evidence = append_bounded(
        failure.evidence,
        {
            "kind": "transition",
            "from": previous,
            "to": status,
            "reason": clean,
            "by": actor.as_dict(),
            "at": utcnow().isoformat(),
        },
        MAX_EVIDENCE_ENTRIES,
    )
    audit(
        db,
        actor,
        FAILURE_STATUS_CHANGED,
        "failure",
        failure.id,
        before={"status": previous},
        after={"status": status, "reason": clean},
    )
    failure_event(db, actor, failure, "status_changed", previous_status=previous, reason=clean)
    db.flush()
    return failure


# =============================================================================================
# Lessons
# =============================================================================================
def _lesson_transition(lesson: Lesson, target: str) -> None:
    allowed = LESSON_TRANSITIONS.get(lesson.status, frozenset())
    if target not in allowed:
        raise InvalidTransition(
            f"Lesson cannot move from {lesson.status} to {target}",
            details={"machine": "lesson", "from": lesson.status, "to": target, "allowed": sorted(allowed)},
        )


def decide_lesson(db: Session, actor: Actor, lesson_id: uuid.UUID | str, *, approve: bool, reason: str) -> Lesson:
    """Human review: approve (PROPOSED → ACTIVE) or retire (→ RETIRED) a lesson (``memory:review``)."""
    actor.require_human("reviewing a lesson")
    reason = " ".join((reason or "").split())
    if not 3 <= len(reason) <= 2000:
        raise ValidationFailed("reason must be 3-2000 characters")
    lesson = get_lesson(db, actor, lesson_id, permission="memory:review")
    target = "ACTIVE" if approve else "RETIRED"
    _lesson_transition(lesson, target)
    previous = lesson.status
    lesson.status = target
    lesson.evidence = append_bounded(
        lesson.evidence,
        {
            "event": "approved" if approve else "retired",
            "reason": reason,
            "user_id": str(actor.user_id) if actor.user_id else None,
            "at": utcnow().isoformat(),
        },
        MAX_LESSON_EVIDENCE,
    )
    audit(
        db,
        actor,
        LESSON_APPROVED if approve else LESSON_RETIRED,
        "lesson",
        lesson.id,
        before={"status": previous},
        after={"status": target, "reason": reason},
    )
    lesson_event(db, actor, lesson, "approved" if approve else "retired", reason=reason)
    db.flush()
    return lesson


def list_lessons(
    db: Session,
    actor: Actor,
    params: PageParams,
    *,
    project_id: uuid.UUID | None = None,
    failure_type: str | None = None,
    status: str | None = None,
    signature: str | None = None,
    mapper: Any = None,
) -> Page[Any]:
    stmt = select(Lesson).where(Lesson.organization_id == actor.organization_id)
    if project_id is not None:
        project = load_project(db, actor, project_id, "failure:read")
        stmt = stmt.where(Lesson.project_id == project.id)
    else:
        actor.require("failure:read")
        visible = visible_project_ids(db, actor)
        if visible is not None:
            stmt = stmt.where(or_(Lesson.project_id.in_(visible), Lesson.project_id.is_(None)))
    for column, value in ((Lesson.failure_type, failure_type), (Lesson.status, status), (Lesson.signature, signature)):
        if value is not None:
            stmt = stmt.where(column == value)
    stmt = stmt.order_by(Lesson.created_at.desc(), Lesson.id.desc())
    return paginate(db, stmt, params, mapper or (lambda row: row))


# =============================================================================================
# Failure queries
# =============================================================================================
def list_failures(
    db: Session,
    actor: Actor,
    params: CursorParams,
    *,
    project_id: uuid.UUID | None = None,
    mission_id: uuid.UUID | None = None,
    experiment_id: uuid.UUID | None = None,
    failure_type: str | None = None,
    status: str | None = None,
    signature: str | None = None,
    mapper: Any = None,
) -> CursorPage[Any]:
    stmt = select(Failure).where(Failure.organization_id == actor.organization_id)
    if project_id is not None:
        project = load_project(db, actor, project_id, "failure:read")
        stmt = stmt.where(Failure.project_id == project.id)
    else:
        actor.require("failure:read")
        visible = visible_project_ids(db, actor)
        if visible is not None:
            stmt = stmt.where(or_(Failure.project_id.in_(visible), Failure.project_id.is_(None)))
    for column, value in (
        (Failure.mission_id, mission_id),
        (Failure.experiment_id, experiment_id),
        (Failure.failure_type, failure_type),
        (Failure.status, status),
        (Failure.signature, signature),
    ):
        if value is not None:
            stmt = stmt.where(column == value)
    return paginate_keyset(
        db, stmt, params, time_col=Failure.created_at, id_col=Failure.id, mapper=mapper or (lambda row: row)
    )


def similar_failures(db: Session, actor: Actor, failure: Failure) -> list[Failure]:
    """The recorded similar failures the actor can currently see, in stored (most similar first) order."""
    ids = [i for i in (_uuid(x) for x in failure.similar_failure_ids or []) if i is not None]
    if not ids:
        return []
    stmt = select(Failure).where(Failure.organization_id == actor.organization_id, Failure.id.in_(ids))
    visible = visible_project_ids(db, actor)
    if visible is not None:
        stmt = stmt.where(or_(Failure.project_id.in_(visible), Failure.project_id.is_(None)))
    rows = {row.id: row for row in db.scalars(stmt)}
    return [rows[i] for i in ids if i in rows]


def lesson_for(db: Session, actor: Actor, failure: Failure) -> Lesson | None:
    if failure.lesson_id is None:
        return None
    lesson = db.get(Lesson, failure.lesson_id)
    if lesson is None or lesson.organization_id != actor.organization_id:
        return None
    return lesson


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except ValueError:
        return None
