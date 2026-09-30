"""Independent verification of scientific claims.

A ``Verification`` runs a set of deterministic checks — each stored as one ``VerificationRun`` (unique per
verification + check type, so every check is idempotent) — and then asks the pure decision engine
(``engines.lab.verification.decide``) for the claim's status and confidence under the claim's criteria profile:

``evidence_validation``           every linked record still exists, its anchored fingerprint (checksums, spec
                                  hash, metrics hash…) is unchanged, objects in storage still match their size /
                                  SHA-256, cited runs completed, and the organization's evidence hash chain is valid
``provenance_completeness``       the claim lineage (``lineage.py``) is complete
``statistical_support``           the recorded comparison statistics (+ the platform's statistical evaluator)
``baseline_validation``           baseline runs succeeded and the metric evaluator passed on them
``independent_evaluation``        built-in evaluators (independent of the generating agent) on the candidate runs
``reproduction``                  independent re-execution with fresh seeds (``reproduction.py``)
``contradiction_search``          contradicting evidence links and opposite-verdict comparisons
``independent_model_assessment``  optional VerifierAgent opinion — recorded with its identity, ADVISORY ONLY: it
                                  can move confidence within the status band but never the status

Model output alone can never make a claim ``VERIFIED``: no criterion is ever satisfied by a model judgment.
"""

from __future__ import annotations

import time
import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.errors import AppError, Conflict, NotFound, ValidationFailed
from aegis_api.lab.core.access import get_owned, load_project, visible_project_ids
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.audit import AuditAction, audit
from aegis_api.lab.core.events import EventType, emit
from aegis_api.lab.core.evidence import verify_lab_chain
from aegis_api.lab.core.features import ensure_feature
from aegis_api.lab.core.locks import advisory_xact_lock
from aegis_api.lab.core.pagination import CursorPage, CursorParams, paginate_keyset
from aegis_api.lab.models import (
    AgentRun,
    Artifact,
    ArtifactVersion,
    ClaimEvidence,
    CodeSnapshot,
    DatasetVersion,
    Discovery,
    EvaluationRun,
    Experiment,
    ExperimentComparison,
    ExperimentRun,
    ExperimentVersion,
    Mission,
    Reproduction,
    ScientificClaim,
    Verification,
    VerificationRun,
)
from aegis_api.lab.observability.metrics import VERIFICATION_DURATION
from aegis_api.lab.storage import get_storage
from aegis_api.lab.storage.base import ObjectNotFound
from aegis_api.lab.verification import reproduction as reproduction_service
from aegis_api.lab.verification.claims import (
    CLAIM_SOURCE_COMPARISON,
    EVIDENCE_MODELS,
    INTEGRITY_FIELDS,
    claim_evidence,
    fingerprint,
    load_claim,
)
from aegis_api.lab.verification.common import (
    SUCCESS_RUN_STATUSES,
    anchor,
    get_many,
    jsonable,
    launch_flow,
    log,
    optional_module,
    uuid_set,
    walk_claim_status,
)
from aegis_api.lab.verification.lineage import build_claim_lineage
from aegis_api.lab.verification.schemas import VerificationOut, VerificationRunOut
from aegis_api.models import Evidence
from engines.evidence.hashing import content_hash
from engines.lab.states import ClaimStatus, ExperimentStatus, RunState, assert_transition, can_transition
from engines.lab.verification import (
    CRITERIA_PROFILES,
    CheckResults,
    EvaluatorOutcome,
    ModelJudgment,
    ProvenanceReport,
    ReproductionOutcome,
    StatisticalEvidence,
    VerdictResult,
    VerifierIdentity,
    decide,
    evaluate_criteria,
    get_profile,
)

CHECK_EVIDENCE = "evidence_validation"
CHECK_PROVENANCE = "provenance_completeness"
CHECK_STATISTICS = "statistical_support"
CHECK_BASELINE = "baseline_validation"
CHECK_INDEPENDENT = "independent_evaluation"
CHECK_REPRODUCTION = "reproduction"
CHECK_CONTRADICTION = "contradiction_search"
CHECK_MODEL = "independent_model_assessment"
DETERMINISTIC_CHECKS: tuple[str, ...] = (
    CHECK_EVIDENCE,
    CHECK_PROVENANCE,
    CHECK_STATISTICS,
    CHECK_BASELINE,
    CHECK_INDEPENDENT,
    CHECK_REPRODUCTION,
    CHECK_CONTRADICTION,
)
ACTIVE_STATUSES = frozenset({RunState.PENDING, RunState.RUNNING, RunState.WAITING})
#: Evaluators that measure the claimed outcome (resource usage, code style, statistics compliance and
#: reproduction are assessed by their own checks).
NON_OUTCOME_EVALUATORS = frozenset({"statistical", "reproduction", "resource", "code_quality"})
MAX_ISSUES = 200


# ---------------------------------------------------------------------------------------------
# Mappers / loading
# ---------------------------------------------------------------------------------------------
def verification_out(verification: Verification) -> VerificationOut:
    return VerificationOut.model_validate(verification)


def verification_run_out(run: VerificationRun) -> VerificationRunOut:
    return VerificationRunOut.model_validate(run)


def load_verification(
    db: Session, actor: Actor, verification_id: uuid.UUID | str, *permissions: str
) -> tuple[Verification, ScientificClaim]:
    verification = get_owned(db, Verification, verification_id, actor, label="Verification")
    try:
        load_project(db, actor, verification.project_id, *permissions)
    except NotFound as exc:
        raise NotFound("Verification not found") from exc
    claim = db.get(ScientificClaim, verification.claim_id)
    if claim is None:  # pragma: no cover - FK cascade
        raise NotFound("Verification not found")
    return verification, claim


def verification_runs(db: Session, verification_id: uuid.UUID) -> list[VerificationRun]:
    return list(
        db.scalars(
            select(VerificationRun)
            .where(VerificationRun.verification_id == verification_id)
            .order_by(VerificationRun.created_at, VerificationRun.id)
        ).all()
    )


def list_verifications(
    db: Session,
    actor: Actor,
    params: CursorParams,
    *,
    claim_id: uuid.UUID | str | None = None,
    status: str | None = None,
    verdict: str | None = None,
    project_id: uuid.UUID | str | None = None,
    mission_id: uuid.UUID | str | None = None,
) -> CursorPage[VerificationOut]:
    stmt = select(Verification).where(Verification.organization_id == actor.organization_id)
    if claim_id:
        stmt = stmt.where(Verification.claim_id == load_claim(db, actor, claim_id, "verification:read").id)
    if status:
        value = status.upper()
        if value not in RunState.__members__:
            raise ValidationFailed(f"status must be one of {', '.join(RunState.__members__)}")
        stmt = stmt.where(Verification.status == value)
    if verdict:
        stmt = stmt.where(Verification.verdict == verdict.upper())
    if project_id:
        stmt = stmt.where(Verification.project_id == load_project(db, actor, project_id, "verification:read").id)
    if mission_id:
        stmt = stmt.where(Verification.mission_id == get_owned(db, Mission, mission_id, actor, label="Mission").id)
    visible = visible_project_ids(db, actor)
    if visible is not None:
        stmt = stmt.where(Verification.project_id.in_(visible))
    return paginate_keyset(
        db, stmt, params, time_col=Verification.created_at, id_col=Verification.id, mapper=verification_out
    )


# ---------------------------------------------------------------------------------------------
# Claim context and generator identity
# ---------------------------------------------------------------------------------------------
@dataclass
class ClaimContext:
    claim: ScientificClaim
    comparison: ExperimentComparison | None = None
    candidate: Experiment | None = None
    baseline: Experiment | None = None
    candidate_runs: list[ExperimentRun] = field(default_factory=list)
    baseline_runs: list[ExperimentRun] = field(default_factory=list)


def claim_context(db: Session, claim: ScientificClaim) -> ClaimContext:
    """The comparison behind a claim and its candidate/baseline experiments and runs (batched)."""
    org = claim.organization_id
    comparison: ExperimentComparison | None = None
    if claim.source_type == CLAIM_SOURCE_COMPARISON and claim.source_id is not None:
        comparison = db.get(ExperimentComparison, claim.source_id)
    links = claim_evidence(db, claim.id)
    if comparison is None:
        linked = [
            link.ref_id
            for link in links
            if link.evidence_type == "experiment_comparison" and link.relation == "supports"
        ]
        comparison = db.get(ExperimentComparison, linked[0]) if linked else None
    ctx = ClaimContext(claim=claim, comparison=comparison)
    if comparison is not None and comparison.organization_id == org:
        experiments = get_many(
            db, Experiment, {comparison.candidate_experiment_id, comparison.baseline_experiment_id}, org
        )
        ctx.candidate = experiments.get(comparison.candidate_experiment_id)
        ctx.baseline = experiments.get(comparison.baseline_experiment_id)
        candidate_ids = uuid_set(comparison.candidate_run_ids or [])
        baseline_ids = uuid_set(comparison.baseline_run_ids or [])
        runs = get_many(db, ExperimentRun, candidate_ids | baseline_ids, org)
        ctx.candidate_runs = sorted((runs[i] for i in candidate_ids if i in runs), key=lambda r: r.run_number)
        ctx.baseline_runs = sorted((runs[i] for i in baseline_ids if i in runs), key=lambda r: r.run_number)
    else:
        ctx.comparison = None
        run_ids = {link.ref_id for link in links if link.evidence_type == "experiment_run" and link.relation == "supports"}
        runs = get_many(db, ExperimentRun, run_ids, org)
        ctx.candidate_runs = sorted(runs.values(), key=lambda r: r.run_number)
        experiment_ids = {r.experiment_id for r in ctx.candidate_runs}
        if len(experiment_ids) == 1:
            ctx.candidate = db.get(Experiment, next(iter(experiment_ids)))
    return ctx


@dataclass
class GeneratorIdentity:
    agent_version_ids: list[str]
    models: list[str]
    actor_ids: list[str]

    def as_dict(self) -> dict[str, list[str]]:
        return {"agent_version_ids": self.agent_version_ids, "models": self.models, "actor_ids": self.actor_ids}


def generator_identity(db: Session, ctx: ClaimContext) -> GeneratorIdentity:
    """Who produced the claim: agent versions and models that designed/coded/extracted it, and the actors."""
    org = ctx.claim.organization_id
    experiments = [e for e in (ctx.candidate, ctx.baseline) if e is not None]
    version_ids: set[Any] = set()
    if ctx.comparison is not None:
        version_ids |= {ctx.comparison.candidate_version_id, ctx.comparison.baseline_version_id}
    version_ids |= {e.current_version_id for e in experiments if e.current_version_id}
    versions = get_many(db, ExperimentVersion, version_ids, org)
    snapshots = get_many(db, CodeSnapshot, {v.code_snapshot_id for v in versions.values() if v.code_snapshot_id}, org)
    agent_run_ids: set[Any] = {ctx.claim.extractor_agent_run_id}
    agent_run_ids |= {e.created_by_agent_run_id for e in experiments}
    agent_run_ids |= {v.created_by_agent_run_id for v in versions.values()}
    agent_run_ids |= {s.created_by_agent_run_id for s in snapshots.values()}
    agent_runs = get_many(db, AgentRun, agent_run_ids, org)
    actors: set[str] = set()
    for user_id in [ctx.claim.created_by_id, *(e.created_by_id for e in experiments)]:
        if user_id is not None:
            actors.add(f"user:{user_id}")
    actors |= {f"agent_run:{run_id}" for run_id in agent_runs}
    return GeneratorIdentity(
        agent_version_ids=sorted({str(r.agent_version_id) for r in agent_runs.values()}),
        models=sorted({f"{r.provider}/{r.model}" for r in agent_runs.values() if r.provider and r.model}),
        actor_ids=sorted(actors),
    )


# ---------------------------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------------------------
def _profile(value: str | None, claim: ScientificClaim) -> str:
    profile = value or claim.criteria_profile or "default"
    if profile not in CRITERIA_PROFILES:
        raise ValidationFailed(f"profile must be one of {', '.join(sorted(CRITERIA_PROFILES))}")
    return profile


def start_verification(
    db: Session,
    actor: Actor,
    claim_id: uuid.UUID | str,
    profile: str | None = None,
    *,
    launch: bool = True,
) -> Verification:
    """Start (or return the active) verification of a claim; launches ``VerificationWorkflow`` after commit."""
    claim = load_claim(db, actor, claim_id, "verification:run")
    ensure_feature(db, actor.organization_id, "verification")
    chosen = _profile(profile, claim)
    advisory_xact_lock(db, f"verification:{claim.id}")
    active = db.scalar(
        select(Verification)
        .where(Verification.claim_id == claim.id, Verification.status.in_(sorted(ACTIVE_STATUSES)))
        .order_by(Verification.created_at.desc())
        .limit(1)
    )
    if active is not None:
        return active
    ctx = claim_context(db, claim)
    identity = generator_identity(db, ctx)
    discovery_id = db.scalar(
        select(Discovery.id)
        .where(Discovery.claim_id == claim.id, Discovery.status != "REJECTED")
        .order_by(Discovery.created_at.desc())
        .limit(1)
    )
    criteria = get_profile(chosen).model_dump(mode="json")
    criteria["generator_identity"] = identity.as_dict()
    verification = Verification(
        id=uuid.uuid4(),
        organization_id=actor.organization_id,
        workspace_id=claim.workspace_id,
        project_id=claim.project_id,
        mission_id=claim.mission_id,
        claim_id=claim.id,
        discovery_id=discovery_id,
        criteria_profile=chosen,
        criteria=criteria,
        status=RunState.PENDING,
        checks={},
        generating_agent_version_ids=identity.agent_version_ids,
        requested_by_id=actor.user_id,
    )
    db.add(verification)
    db.flush()
    emit(
        db,
        organization_id=claim.organization_id,
        type=EventType.VERIFICATION_STARTED,
        payload={"verification_id": str(verification.id), "claim_id": str(claim.id), "profile": chosen},
        mission_id=claim.mission_id,
        project_id=claim.project_id,
        workspace_id=claim.workspace_id,
        subject_type="verification",
        subject_id=verification.id,
        actor=actor,
    )
    audit(
        db,
        actor,
        AuditAction.VERIFICATION_STARTED,
        "verification",
        verification.id,
        after={"claim_id": str(claim.id), "profile": chosen, "claim_status": claim.status},
    )
    if launch:
        verification.workflow_run_id = launch_flow(
            db,
            actor,
            "VerificationWorkflow",
            subject_type="verification",
            subject_id=verification.id,
            flow_input={"claim_id": str(claim.id), "verification_id": str(verification.id), "profile": chosen},
            project_id=claim.project_id,
            mission_id=claim.mission_id,
        )
        db.flush()
    return verification


# ---------------------------------------------------------------------------------------------
# Check bookkeeping
# ---------------------------------------------------------------------------------------------
def _begin(db: Session, verification: Verification) -> None:
    if verification.status == RunState.PENDING:
        assert_transition("run", verification.status, RunState.RUNNING)
        verification.status = RunState.RUNNING
        verification.started_at = verification.started_at or utcnow()
        db.flush()
    elif verification.status not in ACTIVE_STATUSES:
        raise Conflict(
            f"The verification is {verification.status.lower()}; start a new verification to re-check the claim",
            code="verification_not_active",
        )


def get_check(db: Session, verification_id: uuid.UUID, check_type: str) -> VerificationRun | None:
    return db.scalar(
        select(VerificationRun).where(
            VerificationRun.verification_id == verification_id, VerificationRun.check_type == check_type
        )
    )


def _completed(db: Session, verification: Verification, check_type: str) -> VerificationRun | None:
    row = get_check(db, verification.id, check_type)
    return row if row is not None and row.status in (RunState.COMPLETED, RunState.FAILED) else None


def save_check(
    db: Session,
    verification: Verification,
    check_type: str,
    *,
    passed: bool | None,
    result: dict[str, Any],
    status: str = RunState.COMPLETED,
    error: str | None = None,
    evaluator_key: str | None = None,
    evaluator_version: str | None = None,
    agent_run_id: uuid.UUID | None = None,
    reproduction_id: uuid.UUID | None = None,
    evaluation_run_id: uuid.UUID | None = None,
) -> VerificationRun:
    """Create/advance the check row (idempotent: a finished check is returned unchanged)."""
    row = get_check(db, verification.id, check_type)
    now = utcnow()
    if row is None:
        row = VerificationRun(
            id=uuid.uuid4(),
            organization_id=verification.organization_id,
            verification_id=verification.id,
            check_type=check_type,
            status=RunState.PENDING,
            result={},
            started_at=now,
        )
        db.add(row)
        db.flush()
    if row.status in (RunState.COMPLETED, RunState.FAILED):
        return row
    if row.status == RunState.PENDING:
        assert_transition("run", row.status, RunState.RUNNING)
        row.status = RunState.RUNNING
    if status != RunState.RUNNING:
        assert_transition("run", row.status, status)
        row.status = status
        row.completed_at = now
    row.passed = passed
    row.result = jsonable(result)
    row.error = error
    row.evaluator_key = evaluator_key or row.evaluator_key
    row.evaluator_version = evaluator_version or row.evaluator_version
    row.agent_run_id = agent_run_id or row.agent_run_id
    row.reproduction_id = reproduction_id or row.reproduction_id
    row.evaluation_run_id = evaluation_run_id or row.evaluation_run_id
    db.flush()
    return row


def _active(db: Session, actor: Actor, verification_id: uuid.UUID | str) -> tuple[Verification, ScientificClaim]:
    verification, claim = load_verification(db, actor, verification_id, "verification:run")
    advisory_xact_lock(db, f"verification-check:{verification.id}")
    db.refresh(verification)
    return verification, claim


# ---------------------------------------------------------------------------------------------
# evidence_validation (three phases so storage IO never runs inside a DB transaction in workers)
# ---------------------------------------------------------------------------------------------
@dataclass
class ObjectCheck:
    evidence_type: str
    ref_id: str
    key: str
    size_bytes: int
    sha256: str


@dataclass
class EvidencePlan:
    verification_id: uuid.UUID
    issues: list[dict[str, Any]]
    objects: list[ObjectCheck]
    checked: dict[str, int]
    chain: dict[str, Any]
    links: int


def _issue(link: ClaimEvidence, problem: str, **extra: Any) -> dict[str, Any]:
    return {
        "link_id": str(link.id),
        "evidence_type": link.evidence_type,
        "ref_id": str(link.ref_id),
        "problem": problem,
        **jsonable(extra),
    }


def plan_evidence_validation(db: Session, actor: Actor, verification_id: uuid.UUID | str) -> EvidencePlan | None:
    """Phase 1 (DB): verify links, anchors and fingerprints; list storage objects to stat. ``None`` if done."""
    verification, claim = _active(db, actor, verification_id)
    if _completed(db, verification, CHECK_EVIDENCE) is not None:
        return None
    _begin(db, verification)
    org = claim.organization_id
    links = claim_evidence(db, claim.id)
    grouped: dict[str, set[uuid.UUID]] = defaultdict(set)
    for link in links:
        grouped[link.evidence_type].add(link.ref_id)
    rows = {t: get_many(db, EVIDENCE_MODELS[t], ids, org) for t, ids in grouped.items() if t in EVIDENCE_MODELS}
    anchors = get_many(db, Evidence, [link.evidence_id for link in links if link.evidence_id], org)
    artifact_ids = {row.artifact_id for row in rows.get("artifact_version", {}).values()}
    artifacts = get_many(db, Artifact, artifact_ids, org)
    issues: list[dict[str, Any]] = []
    objects: list[ObjectCheck] = []
    checked: dict[str, int] = defaultdict(int)
    for link in links:
        ref = rows.get(link.evidence_type, {}).get(link.ref_id)
        if link.evidence_type not in EVIDENCE_MODELS or ref is None:
            issues.append(_issue(link, "referenced record not found"))
            continue
        checked[link.evidence_type] += 1
        evidence = anchors.get(link.evidence_id) if link.evidence_id else None
        anchored: dict[str, Any] = {}
        if evidence is None:
            issues.append(_issue(link, "link is not anchored in the evidence chain"))
        else:
            recomputed = content_hash(
                {"kind": evidence.kind, "title": evidence.title, "content": evidence.content, "source_uri": evidence.source_uri}
            )
            if recomputed != evidence.content_hash:
                issues.append(_issue(link, "anchored evidence content does not match its content hash"))
            if link.content_hash and link.content_hash != evidence.content_hash:
                issues.append(_issue(link, "link content hash does not match its evidence record"))
            content = evidence.content or {}
            if content.get("ref_id") != str(link.ref_id) or content.get("claim_id") != str(claim.id):
                issues.append(_issue(link, "anchored evidence describes a different record"))
            anchored = content.get("record") or {}
        current = fingerprint(link.evidence_type, ref)
        for name in INTEGRITY_FIELDS.get(link.evidence_type, ()):
            if name in anchored and anchored[name] != current.get(name):
                issues.append(
                    _issue(link, f"{name} changed since the evidence was anchored", anchored=anchored[name], current=current.get(name))
                )
        if link.evidence_type == "experiment_run" and ref.status not in SUCCESS_RUN_STATUSES:
            issues.append(_issue(link, "run did not complete successfully", status=ref.status))
        elif link.evidence_type == "evaluation_run" and ref.status != RunState.COMPLETED:
            issues.append(_issue(link, "evaluation did not complete", status=ref.status))
        elif link.evidence_type == "artifact_version":
            artifact = artifacts.get(ref.artifact_id)
            if artifact is None or artifact.deleted_at is not None:
                issues.append(_issue(link, "artifact was deleted"))
            if ref.scan_status == "infected":
                issues.append(_issue(link, "artifact was flagged by the malware scanner"))
            objects.append(ObjectCheck("artifact_version", str(ref.id), ref.storage_key, ref.size_bytes, ref.checksum))
        elif link.evidence_type == "dataset_version":
            objects.append(ObjectCheck("dataset_version", str(ref.id), ref.storage_key, ref.size_bytes, ref.checksum))
    chain = verify_lab_chain(db, org)
    return EvidencePlan(
        verification_id=verification.id,
        issues=issues,
        objects=objects,
        checked=dict(checked),
        chain=chain,
        links=len(links),
    )


def stat_objects(objects: Sequence[ObjectCheck]) -> list[dict[str, Any]]:
    """Phase 2 (storage IO only): compare stored objects with their recorded size / SHA-256."""
    storage = get_storage()
    issues: list[dict[str, Any]] = []
    for obj in objects:
        base = {"evidence_type": obj.evidence_type, "ref_id": obj.ref_id}
        try:
            stat = storage.stat(obj.key)
        except ObjectNotFound:
            issues.append({**base, "problem": "object is missing from storage"})
            continue
        if stat.size != obj.size_bytes:
            issues.append({**base, "problem": "stored object size differs", "recorded": obj.size_bytes, "stored": stat.size})
        if stat.sha256 and stat.sha256.lower() != obj.sha256.lower():
            issues.append({**base, "problem": "stored object checksum differs", "recorded": obj.sha256, "stored": stat.sha256})
    return issues


def record_evidence_validation(
    db: Session, actor: Actor, verification_id: uuid.UUID | str, plan: EvidencePlan, storage_issues: list[dict[str, Any]]
) -> VerificationRun:
    """Phase 3 (DB): store the check result."""
    verification, _ = _active(db, actor, verification_id)
    done = _completed(db, verification, CHECK_EVIDENCE)
    if done is not None:
        return done
    issues = plan.issues + storage_issues
    chain_valid = bool(plan.chain.get("valid"))
    passed = not issues and chain_valid
    return save_check(
        db,
        verification,
        CHECK_EVIDENCE,
        passed=passed,
        result={
            "links": plan.links,
            "checked": plan.checked,
            "objects_checked": len(plan.objects),
            "issues": issues[:MAX_ISSUES],
            "issue_count": len(issues),
            "evidence_chain": {
                "valid": chain_valid,
                "records": plan.chain.get("records"),
                "broken_indices": list(plan.chain.get("broken_indices") or [])[:50],
                "head": plan.chain.get("head"),
            },
        },
    )


def validate_evidence(db: Session, actor: Actor, verification_id: uuid.UUID | str) -> VerificationRun:
    """All three phases in one session (API/manual path)."""
    plan = plan_evidence_validation(db, actor, verification_id)
    if plan is None:
        verification, _ = load_verification(db, actor, verification_id, "verification:run")
        row = get_check(db, verification.id, CHECK_EVIDENCE)
        assert row is not None
        return row
    return record_evidence_validation(db, actor, verification_id, plan, stat_objects(plan.objects))


# ---------------------------------------------------------------------------------------------
# provenance_completeness
# ---------------------------------------------------------------------------------------------
def check_provenance(db: Session, actor: Actor, verification_id: uuid.UUID | str) -> VerificationRun:
    verification, claim = _active(db, actor, verification_id)
    done = _completed(db, verification, CHECK_PROVENANCE)
    if done is not None:
        return done
    _begin(db, verification)
    lineage = build_claim_lineage(db, claim)
    summary = lineage.summary()
    evidence = anchor(
        db,
        claim.organization_id,
        "lineage",
        f"Lineage of claim {claim.id} ({'complete' if lineage.complete else 'incomplete'})",
        {
            "claim_id": claim.id,
            "verification_id": verification.id,
            "digest": lineage.graph.digest,
            "schema_version": lineage.graph.schema_version,
            "complete": lineage.complete,
            "missing": summary["missing"],
            "not_applicable": summary["not_applicable"],
        },
    )
    summary["evidence_id"] = str(evidence.id)
    return save_check(db, verification, CHECK_PROVENANCE, passed=lineage.complete, result=summary)


# ---------------------------------------------------------------------------------------------
# Evaluations (through the evaluation context when deployed)
# ---------------------------------------------------------------------------------------------
def _run_evaluation(db: Session, actor: Actor, **kwargs: Any) -> tuple[EvaluationRun | None, str | None]:
    module = optional_module("aegis_api.lab.evaluation.service")
    run_evaluation = getattr(module, "run_evaluation", None) if module is not None else None
    if run_evaluation is None:
        return None, "evaluation service unavailable"
    try:
        with db.begin_nested():
            result = run_evaluation(db, actor, **kwargs)
    except (AppError, ValueError) as exc:
        return None, f"{type(exc).__name__}: {getattr(exc, 'message', str(exc))}"[:500]
    return (result if isinstance(result, EvaluationRun) else None), None


def _evaluations_for(db: Session, run_ids: Iterable[uuid.UUID], key: str | None = None) -> list[EvaluationRun]:
    ids = sorted(set(run_ids))
    if not ids:
        return []
    stmt = select(EvaluationRun).where(EvaluationRun.experiment_run_id.in_(ids))
    if key is not None:
        stmt = stmt.where(EvaluationRun.evaluator_key == key)
    return list(db.scalars(stmt.order_by(EvaluationRun.created_at, EvaluationRun.id)).all())


def _aggregate(evaluations: Sequence[EvaluationRun]) -> list[dict[str, Any]]:
    """Per evaluator key+version: passed (False if any failed), independence, lowest confidence, ids."""
    groups: dict[tuple[str, str], list[EvaluationRun]] = defaultdict(list)
    for evaluation in evaluations:
        if evaluation.status == RunState.COMPLETED:
            groups[(evaluation.evaluator_key, evaluation.evaluator_version)].append(evaluation)
    out = []
    for (key, version), items in sorted(groups.items()):
        decided = [e.passed for e in items if e.passed is not None]
        confidences = [e.confidence for e in items if e.confidence is not None]
        out.append(
            {
                "key": key,
                "version": version,
                "passed": None if not decided else all(decided),
                "independent": all(e.independent for e in items),
                "confidence": min(confidences) if confidences else None,
                "evaluation_run_ids": [str(e.id) for e in items],
                "runs_evaluated": len({e.experiment_run_id for e in items}),
            }
        )
    return out


# ---------------------------------------------------------------------------------------------
# statistical_support
# ---------------------------------------------------------------------------------------------
def _statistical_evidence(ctx: ClaimContext) -> StatisticalEvidence | None:
    if ctx.comparison is None:
        return None
    stats = ctx.comparison.statistics or {}
    direction = ctx.claim.direction or ("increase" if ctx.comparison.direction == "maximize" else "decrease")
    data = {
        "p_value": stats.get("p_value"),
        "p_value_adjusted": stats.get("p_value_adjusted"),
        "alpha": stats.get("alpha"),
        "ci_low": stats.get("ci_low"),
        "ci_high": stats.get("ci_high"),
        "effect_size": stats.get("effect_size"),
        "claimed_direction": direction,
    }
    try:
        return StatisticalEvidence.model_validate(data)
    except ValueError:
        return None


def check_statistical_support(db: Session, actor: Actor, verification_id: uuid.UUID | str) -> VerificationRun:
    verification, claim = _active(db, actor, verification_id)
    done = _completed(db, verification, CHECK_STATISTICS)
    if done is not None:
        return done
    _begin(db, verification)
    ctx = claim_context(db, claim)
    evidence = _statistical_evidence(ctx)
    if evidence is None:
        return save_check(
            db,
            verification,
            CHECK_STATISTICS,
            passed=None,
            result={"state": "missing", "reason": "no statistical comparison is linked to the claim"},
        )
    assert ctx.comparison is not None
    comparison = ctx.comparison
    statistical = list(
        db.scalars(
            select(EvaluationRun)
            .where(
                EvaluationRun.evaluator_key == "statistical",
                or_(
                    EvaluationRun.comparison_id == comparison.id,
                    EvaluationRun.experiment_id == comparison.candidate_experiment_id,
                ),
            )
            .order_by(EvaluationRun.created_at.desc())
        ).all()
    )
    error = None
    if not any(e.status == RunState.COMPLETED for e in statistical):
        created, error = _run_evaluation(db, actor, evaluator_key="statistical", comparison_id=comparison.id)
        if created is not None:
            statistical.insert(0, created)
    latest = next((e for e in statistical if e.status == RunState.COMPLETED), None)
    criterion = evaluate_criteria(verification.criteria_profile, CheckResults(statistics=evidence))[
        "statistically_supported"
    ]
    evaluator_passed = latest.passed if latest is not None else None
    passed: bool | None
    if criterion.state == "missing":
        passed = None
    else:
        passed = criterion.state == "satisfied" and evaluator_passed is not False
    return save_check(
        db,
        verification,
        CHECK_STATISTICS,
        passed=passed,
        result={
            "comparison_id": comparison.id,
            "statistics": evidence.model_dump(),
            "criterion": {"state": criterion.state, "detail": criterion.detail},
            "statistical_evaluator": (
                {
                    "evaluation_run_id": latest.id,
                    "version": latest.evaluator_version,
                    "passed": latest.passed,
                    "warnings": list(latest.warnings or [])[:20],
                }
                if latest is not None
                else None
            ),
            "evaluator_error": error,
        },
        evaluator_key="statistical" if latest is not None else None,
        evaluator_version=latest.evaluator_version if latest is not None else None,
        evaluation_run_id=latest.id if latest is not None else None,
    )


# ---------------------------------------------------------------------------------------------
# baseline_validation
# ---------------------------------------------------------------------------------------------
def check_baseline(db: Session, actor: Actor, verification_id: uuid.UUID | str) -> VerificationRun:
    verification, claim = _active(db, actor, verification_id)
    done = _completed(db, verification, CHECK_BASELINE)
    if done is not None:
        return done
    _begin(db, verification)
    ctx = claim_context(db, claim)
    if ctx.comparison is None or not ctx.baseline_runs:
        return save_check(
            db, verification, CHECK_BASELINE, passed=None, result={"state": "missing", "reason": "no baseline runs"}
        )
    runs = ctx.baseline_runs
    completed = all(r.status in SUCCESS_RUN_STATUSES for r in runs)
    evaluations = _evaluations_for(db, [r.id for r in runs], "metric")
    errors: list[str] = []
    evaluated = {e.experiment_run_id for e in evaluations if e.status == RunState.COMPLETED}
    for run in runs:
        if run.id in evaluated or run.status not in SUCCESS_RUN_STATUSES:
            continue
        created, error = _run_evaluation(db, actor, evaluator_key="metric", experiment_run_id=run.id)
        if created is not None:
            evaluations.append(created)
        if error:
            errors.append(error)
            break  # the service is unavailable or refuses; do not retry per run
    aggregated = [a for a in _aggregate(evaluations) if a["key"] == "metric"]
    evaluator_passed = aggregated[0]["passed"] if aggregated else None
    passed = False if not completed else (evaluator_passed if evaluator_passed is not None else None)
    return save_check(
        db,
        verification,
        CHECK_BASELINE,
        passed=passed,
        result={
            "baseline_experiment_id": ctx.comparison.baseline_experiment_id,
            "baseline_run_ids": [str(r.id) for r in runs],
            "run_statuses": {str(r.id): r.status for r in runs},
            "runs_completed": completed,
            "evaluator_passed": evaluator_passed,
            "evaluators": aggregated,
            "evaluator_errors": errors,
        },
        evaluator_key="metric" if aggregated else None,
        evaluator_version=aggregated[0]["version"] if aggregated else None,
    )


# ---------------------------------------------------------------------------------------------
# independent_evaluation
# ---------------------------------------------------------------------------------------------
def independent_evaluation(db: Session, actor: Actor, verification_id: uuid.UUID | str) -> VerificationRun:
    """Built-in evaluators (independent of the generating agent) on the candidate runs."""
    verification, claim = _active(db, actor, verification_id)
    done = _completed(db, verification, CHECK_INDEPENDENT)
    if done is not None:
        return done
    _begin(db, verification)
    ctx = claim_context(db, claim)
    runs = [r for r in ctx.candidate_runs if r.status in SUCCESS_RUN_STATUSES]
    if not runs:
        return save_check(
            db,
            verification,
            CHECK_INDEPENDENT,
            passed=None,
            result={"state": "missing", "reason": "no successful candidate runs to evaluate", "evaluators": []},
        )
    evaluations = _evaluations_for(db, [r.id for r in runs])
    errors: list[str] = []
    has_metric = {e.experiment_run_id for e in evaluations if e.evaluator_key == "metric" and e.status == RunState.COMPLETED}
    for run in runs:
        if run.id in has_metric:
            continue
        created, error = _run_evaluation(db, actor, evaluator_key="metric", experiment_run_id=run.id)
        if created is not None:
            evaluations.append(created)
        if error:
            errors.append(error)
            break
    outcomes = [a for a in _aggregate(evaluations) if a["key"] not in NON_OUTCOME_EVALUATORS]
    independent = [o for o in outcomes if o["independent"]]
    if any(o["passed"] is False for o in independent):
        passed: bool | None = False
    elif any(o["passed"] is True for o in independent):
        passed = True
    else:
        passed = None
    first_id = next((uuid.UUID(o["evaluation_run_ids"][0]) for o in outcomes if o["evaluation_run_ids"]), None)
    return save_check(
        db,
        verification,
        CHECK_INDEPENDENT,
        passed=passed,
        result={
            "candidate_run_ids": [str(r.id) for r in runs],
            "evaluators": outcomes,
            "evaluator_errors": errors,
            "independent_of_generator": True,
        },
        evaluation_run_id=first_id,
    )


# ---------------------------------------------------------------------------------------------
# reproduction
# ---------------------------------------------------------------------------------------------
def request_reproduction_for_verification(
    db: Session,
    actor: Actor,
    verification_id: uuid.UUID | str,
    *,
    seeds: Sequence[int] | None = None,
    tolerance: dict[str, Any] | None = None,
    launch: bool = False,
) -> Reproduction:
    """Request (once per verification) an independent reproduction of the claim's candidate experiment."""
    verification, claim = _active(db, actor, verification_id)
    existing = get_check(db, verification.id, CHECK_REPRODUCTION)
    if existing is not None and existing.reproduction_id is not None:
        reproduction = db.get(Reproduction, existing.reproduction_id)
        if reproduction is not None:
            return reproduction
    _begin(db, verification)
    ctx = claim_context(db, claim)
    if ctx.candidate is None:
        raise Conflict("The claim is not linked to a candidate experiment that could be reproduced", code="nothing_to_reproduce")
    if ctx.comparison is not None and ctx.candidate.current_version_id != ctx.comparison.candidate_version_id:
        raise Conflict(
            "The candidate experiment has a newer version than the compared one; the compared version cannot be "
            "re-executed as the current version",
            code="reproduction_version_mismatch",
        )
    reproduction = reproduction_service.request_reproduction(
        db, actor, ctx.candidate.id, seeds=seeds, tolerance=tolerance, launch=launch
    )
    save_check(
        db,
        verification,
        CHECK_REPRODUCTION,
        passed=None,
        result={
            "reproduction_id": reproduction.id,
            "status": reproduction.status,
            "seeds": (reproduction.tolerance or {}).get("protocol", {}).get("seeds", []),
        },
        status=RunState.RUNNING,
        reproduction_id=reproduction.id,
    )
    return reproduction


def _reproduction_outcomes(
    db: Session, verification: Verification, ctx: ClaimContext
) -> tuple[list[ReproductionOutcome], list[str]]:
    linked = {row.reproduction_id for row in verification_runs(db, verification.id) if row.reproduction_id}
    conditions = [Reproduction.id.in_(sorted(linked))] if linked else []
    if ctx.candidate is not None and ctx.comparison is not None:
        conditions.append(
            (Reproduction.experiment_id == ctx.candidate.id)
            & (Reproduction.experiment_version_id == ctx.comparison.candidate_version_id)
        )
    if not conditions:
        return [], []
    rows = db.scalars(select(Reproduction).where(or_(*conditions)).order_by(Reproduction.created_at)).all()
    outcomes: list[ReproductionOutcome] = []
    pending: list[str] = []
    originals = get_many(
        db, ExperimentRun, {rid for r in rows for rid in uuid_set(r.original_run_ids or [])}, verification.organization_id
    )
    for repro in rows:
        if repro.status != RunState.COMPLETED or not repro.verdict:
            if repro.status in ACTIVE_STATUSES:
                pending.append(str(repro.id))
            continue
        original_seeds = {originals[i].seed for i in uuid_set(repro.original_run_ids or []) if i in originals}
        repro_seeds = set((repro.tolerance or {}).get("protocol", {}).get("seeds") or [])
        # Re-running the very same seeds confirms determinism, not the (statistical) result.
        independent = not repro_seeds or not repro_seeds.issubset(original_seeds)
        outcomes.append(
            ReproductionOutcome(id=str(repro.id), verdict=repro.verdict, independent=independent)  # type: ignore[arg-type]
        )
    return outcomes, pending


# ---------------------------------------------------------------------------------------------
# contradiction search
# ---------------------------------------------------------------------------------------------
def contradiction_search(db: Session, verification: Verification, ctx: ClaimContext) -> VerificationRun:
    done = _completed(db, verification, CHECK_CONTRADICTION)
    if done is not None:
        return done
    contradicting = [
        str(link.id)
        for link in claim_evidence(db, ctx.claim.id)
        if link.relation == "contradicts"
    ]
    opposite: list[str] = []
    if ctx.comparison is not None:
        opposite = [
            str(row_id)
            for row_id in db.scalars(
                select(ExperimentComparison.id).where(
                    ExperimentComparison.candidate_experiment_id == ctx.comparison.candidate_experiment_id,
                    ExperimentComparison.baseline_experiment_id == ctx.comparison.baseline_experiment_id,
                    ExperimentComparison.metric == ctx.comparison.metric,
                    ExperimentComparison.id != ctx.comparison.id,
                    ExperimentComparison.verdict == "regressed",
                )
            ).all()
        ]
    count = len(contradicting) + len(opposite)
    return save_check(
        db,
        verification,
        CHECK_CONTRADICTION,
        passed=count == 0,
        result={
            "contradicting_evidence_count": count,
            "contradicting_links": contradicting,
            "opposite_comparisons": opposite,
        },
    )


# ---------------------------------------------------------------------------------------------
# Model assessment (advisory)
# ---------------------------------------------------------------------------------------------
def _assessor_independence(verification: Verification, agent_run: AgentRun) -> tuple[bool, list[str]]:
    generator = (verification.criteria or {}).get("generator_identity") or {}
    reasons = []
    if str(agent_run.agent_version_id) in set(verification.generating_agent_version_ids or []):
        reasons.append("the verifier agent version generated the claim")
    model = f"{agent_run.provider}/{agent_run.model}" if agent_run.provider and agent_run.model else None
    if model is not None and model in set(generator.get("models") or []):
        reasons.append("the verifier used the same model as the generator")
    if f"agent_run:{agent_run.id}" in set(generator.get("actor_ids") or []):
        reasons.append("the verifier run generated the claim")
    return not reasons, reasons


def record_model_assessment(
    db: Session,
    actor: Actor,
    verification_id: uuid.UUID | str,
    *,
    agent_run: AgentRun,
    supports: bool,
    confidence: float,
    concerns: Sequence[str] = (),
    summary: str | None = None,
    verdict_label: str | None = None,
) -> VerificationRun:
    """Store a VerifierAgent opinion with its identity. Never changes the status; can only move confidence."""
    verification, claim = load_verification(db, actor, verification_id, "verification:run")
    advisory_xact_lock(db, f"verification-check:{verification.id}")
    if agent_run.organization_id != verification.organization_id:
        raise NotFound("Agent run not found")
    if not 0.0 <= float(confidence) <= 1.0:
        raise ValidationFailed("confidence must be between 0 and 1")
    independent, reasons = _assessor_independence(verification, agent_run)
    identity = {
        "agent_run_id": str(agent_run.id),
        "agent_version_id": str(agent_run.agent_version_id),
        "role": agent_run.role,
        "provider": agent_run.provider,
        "model": agent_run.model,
        "model_version": agent_run.model_version,
        "prompt_key": agent_run.prompt_key,
        "prompt_version": agent_run.prompt_version,
        "prompt_hash": agent_run.prompt_hash,
    }
    existing = get_check(db, verification.id, CHECK_MODEL)
    if existing is not None and existing.status == RunState.COMPLETED:
        return existing
    row = save_check(
        db,
        verification,
        CHECK_MODEL,
        passed=None,  # advisory: a model opinion never passes (or fails) a verification criterion
        result={
            "advisory": True,
            "supports_claim": bool(supports),
            "confidence": float(confidence),
            "verdict_label": verdict_label,
            "concerns": [str(c)[:1000] for c in list(concerns)[:20]],
            "summary": (summary or "")[:4000] or None,
            "independent": independent,
            "not_independent_reasons": reasons,
            "identity": identity,
        },
        agent_run_id=agent_run.id,
    )
    if verification.status == RunState.COMPLETED and independent:
        _reapply_confidence(db, verification, claim)
    return row


def _model_judgment(row: VerificationRun | None) -> ModelJudgment | None:
    if row is None or row.status != RunState.COMPLETED:
        return None
    result = row.result or {}
    if not result.get("independent"):
        return None
    identity = result.get("identity") or {}
    model = f"{identity.get('provider')}/{identity.get('model')}" if identity.get("model") else None
    return ModelJudgment(
        supports=bool(result.get("supports_claim")),
        confidence=float(result.get("confidence") or 0.0),
        rationale=(result.get("summary") or None),
        model=model,
    )


def _reapply_confidence(db: Session, verification: Verification, claim: ScientificClaim) -> None:
    inputs = (verification.checks or {}).get("inputs")
    if not inputs:
        return
    checks = CheckResults.model_validate(inputs)
    checks.model_judgment = _model_judgment(get_check(db, verification.id, CHECK_MODEL))
    result = decide(verification.criteria_profile, checks)
    if result.status != verification.verdict:  # pragma: no cover - the engine guarantees status independence
        log.error("model_judgment_changed_status", verification_id=str(verification.id))
        return
    verification.confidence = result.confidence
    verification.checks = {**(verification.checks or {}), "confidence_components": result.confidence_components}
    claim.confidence = result.confidence
    db.flush()


# ---------------------------------------------------------------------------------------------
# Finalize
# ---------------------------------------------------------------------------------------------
def _verifier_identity(
    generator: dict[str, Any], evaluators: Sequence[dict[str, Any]], model_row: VerificationRun | None
) -> VerifierIdentity:
    actor_ids = sorted(
        {f"evaluator:{e['key']}@{e['version']}" for e in evaluators if e.get("independent") and e.get("passed") is not None}
    )
    agent_versions: list[str] = []
    models: list[str] = []
    if model_row is not None and (model_row.result or {}).get("identity"):
        identity = model_row.result["identity"]
        agent_versions = [identity["agent_version_id"]] if identity.get("agent_version_id") else []
        if identity.get("provider") and identity.get("model"):
            models = [f"{identity['provider']}/{identity['model']}"]
        if identity.get("agent_run_id"):
            actor_ids.append(f"agent_run:{identity['agent_run_id']}")
    return VerifierIdentity(
        generator_agent_version_ids=list(generator.get("agent_version_ids") or []),
        generator_models=list(generator.get("models") or []),
        generator_actor_ids=list(generator.get("actor_ids") or []),
        verifier_agent_version_ids=agent_versions,
        verifier_models=models,
        verifier_actor_ids=actor_ids,
        verifier_is_human=False,
    )


def build_check_results(
    db: Session, verification: Verification, ctx: ClaimContext
) -> tuple[CheckResults, dict[str, Any]]:
    """Turn the stored check rows into the engine's ``CheckResults`` (+ notes for the record)."""
    rows = {row.check_type: row for row in verification_runs(db, verification.id)}
    notes: dict[str, Any] = {}

    def done(check_type: str) -> VerificationRun | None:
        row = rows.get(check_type)
        return row if row is not None and row.status == RunState.COMPLETED else None

    statistics = None
    if (row := done(CHECK_STATISTICS)) is not None and row.passed is not None and row.result.get("statistics"):
        statistics = StatisticalEvidence.model_validate(row.result["statistics"])
        if row.result.get("statistical_evaluator", {}) and row.result["statistical_evaluator"].get("passed") is False:
            # the platform's statistical evaluator (recomputed from raw values) disagrees with the stored numbers
            statistics = StatisticalEvidence.model_validate({**row.result["statistics"], "p_value": 1.0, "p_value_adjusted": 1.0})
            notes["statistics"] = "the statistical evaluator did not confirm the pre-registered plan"

    baseline_completed: bool | None = None
    baseline_passed: bool | None = None
    if (row := done(CHECK_BASELINE)) is not None and row.result.get("runs_completed") is not None:
        baseline_completed = bool(row.result["runs_completed"])
        baseline_passed = row.result.get("evaluator_passed")

    evaluators: list[EvaluatorOutcome] = []
    evaluator_items: list[dict[str, Any]] = []
    if (row := done(CHECK_INDEPENDENT)) is not None:
        evaluator_items = list(row.result.get("evaluators") or [])
        evaluators = [
            EvaluatorOutcome(
                key=f"{item['key']}@{item['version']}",
                passed=item.get("passed"),
                independent=bool(item.get("independent")),
                confidence=item.get("confidence"),
            )
            for item in evaluator_items
        ]

    provenance: ProvenanceReport | None = None
    evidence_row = done(CHECK_EVIDENCE)
    provenance_row = done(CHECK_PROVENANCE)
    if evidence_row is not None and evidence_row.passed is False:
        provenance = ProvenanceReport(
            complete=False,
            missing=[f"evidence_integrity ({evidence_row.result.get('issue_count', 0)} issue(s))"],
        )
    elif provenance_row is not None and evidence_row is not None:
        missing = list(provenance_row.result.get("missing") or [])
        links = int(provenance_row.result.get("missing_links_total") or 0)
        if links:
            missing.append(f"{links} missing link(s)")
        provenance = ProvenanceReport(complete=bool(provenance_row.passed), missing=missing)
    elif provenance_row is not None:
        notes["provenance"] = "provenance is not counted until the evidence validation check has run"

    reproductions, pending = _reproduction_outcomes(db, verification, ctx)
    if pending:
        notes["pending_reproductions"] = pending

    contradiction = done(CHECK_CONTRADICTION)
    contradicting = int(contradiction.result.get("contradicting_evidence_count", 0)) if contradiction else None
    supporting = int(
        db.scalar(
            select(func.count(ClaimEvidence.id)).where(
                ClaimEvidence.claim_id == ctx.claim.id, ClaimEvidence.relation == "supports"
            )
        )
        or 0
    )
    model_row = rows.get(CHECK_MODEL)
    generator = (verification.criteria or {}).get("generator_identity") or {}
    verifier = _verifier_identity(generator, evaluator_items, model_row)
    has_identity = bool(verifier.verifier_actor_ids or verifier.verifier_agent_version_ids or verifier.verifier_models)
    checks = CheckResults(
        reproductions=reproductions,
        statistics=statistics,
        baseline_runs_completed=baseline_completed,
        baseline_evaluator_passed=baseline_passed,
        evaluators=evaluators,
        provenance=provenance,
        verifier=verifier if has_identity else None,
        supporting_evidence_count=supporting,
        contradicting_evidence_count=contradicting,
        model_judgment=_model_judgment(model_row),
    )
    return checks, notes


def _update_experiment(db: Session, ctx: ClaimContext, status: str, reproduced: bool) -> str | None:
    """Candidate experiment → VERIFIED/REJECTED when the state machine allows it; returns the new status."""
    experiment = ctx.candidate
    if experiment is None:
        return None
    target = {ClaimStatus.VERIFIED: ExperimentStatus.VERIFIED, ClaimStatus.REJECTED: ExperimentStatus.REJECTED}.get(
        status  # type: ignore[call-overload]
    )
    if target is None or experiment.status == target:
        return None
    path: list[str] = []
    if can_transition("experiment", experiment.status, target):
        path = [target]
    elif (
        reproduced
        and experiment.status == ExperimentStatus.COMPLETED
        and can_transition("experiment", ExperimentStatus.REPRODUCING, target)
    ):
        path = [ExperimentStatus.REPRODUCING, target]
    if not path:
        return None
    for nxt in path:
        assert_transition("experiment", experiment.status, nxt)
        experiment.status = nxt
    experiment.status_reason = f"claim {ctx.claim.id} verification verdict {status}"
    db.flush()
    return target


def finalize_verification(db: Session, actor: Actor, verification_id: uuid.UUID | str) -> Verification:
    """Decide the claim's status from the recorded checks (idempotent for completed verifications)."""
    started = time.monotonic()
    verification, claim = _active(db, actor, verification_id)
    if verification.status == RunState.COMPLETED:
        return verification
    _begin(db, verification)
    ctx = claim_context(db, claim)
    contradiction_search(db, verification, ctx)
    checks, notes = build_check_results(db, verification, ctx)
    result: VerdictResult = decide(verification.criteria_profile, checks)

    previous = claim.status
    path = walk_claim_status(claim.status, result.status)
    if path:
        claim.status = result.status
    claim.confidence = result.confidence
    uncertainty = dict(claim.uncertainty or {})
    uncertainty.update(
        {
            "evidence_quality": "verified" if result.status == ClaimStatus.VERIFIED else "partial",
            "missing_evidence": result.missing,
            "failed_criteria": result.failed,
            "failed_reproductions": sum(1 for r in checks.reproductions if r.verdict == "not_reproduced"),
            "conflicting_evidence": checks.contradicting_evidence_count or 0,
            "last_verification_id": str(verification.id),
            "notes": [result.rationale[:2000]],
        }
    )
    claim.uncertainty = uncertainty
    reproduced = any(r.verdict == "reproduced" for r in checks.reproductions)
    experiment_status = _update_experiment(db, ctx, result.status, reproduced)

    now = utcnow()
    assert_transition("run", verification.status, RunState.COMPLETED)
    verification.status = RunState.COMPLETED
    verification.verdict = result.status
    verification.confidence = result.confidence
    verification.completed_at = now
    verification.checks = jsonable(
        {
            "engine_version": result.engine_version,
            "profile": result.profile,
            "satisfied": result.satisfied,
            "missing": result.missing,
            "failed": result.failed,
            "rationale": result.rationale,
            "criteria": {k: v.model_dump(mode="json") for k, v in result.criteria.items()},
            "confidence_components": result.confidence_components,
            "inputs": checks.model_dump(mode="json"),
            "notes": notes,
            "claim_status": {"from": previous, "to": claim.status, "path": path},
            "experiment_status": experiment_status,
        }
    )
    db.flush()
    evidence = anchor(
        db,
        claim.organization_id,
        "verification",
        f"Verification {verification.id} of claim {claim.id}: {result.status}",
        {
            "verification_id": verification.id,
            "claim_id": claim.id,
            "profile": result.profile,
            "verdict": result.status,
            "confidence": result.confidence,
            "satisfied": result.satisfied,
            "missing": result.missing,
            "failed": result.failed,
            "engine_version": result.engine_version,
            "check_run_ids": [str(r.id) for r in verification_runs(db, verification.id)],
        },
    )
    verification.checks = {**verification.checks, "evidence_id": str(evidence.id)}
    db.flush()
    emit(
        db,
        organization_id=claim.organization_id,
        type=EventType.VERIFICATION_COMPLETED,
        payload={
            "verification_id": str(verification.id),
            "claim_id": str(claim.id),
            "verdict": result.status,
            "confidence": result.confidence,
            "claim_status": claim.status,
            "previous_claim_status": previous,
            "satisfied": result.satisfied,
            "missing": result.missing,
            "failed": result.failed,
        },
        mission_id=claim.mission_id,
        project_id=claim.project_id,
        workspace_id=claim.workspace_id,
        subject_type="verification",
        subject_id=verification.id,
        actor=actor,
    )
    since = verification.started_at or verification.created_at
    duration = max((now - since).total_seconds(), time.monotonic() - started) if since else time.monotonic() - started
    VERIFICATION_DURATION.labels(result.status).observe(duration)
    return verification


# ---------------------------------------------------------------------------------------------
# Manual path: run every deterministic check inline (no reproduction scheduling)
# ---------------------------------------------------------------------------------------------
def run_deterministic_checks(db: Session, actor: Actor, verification_id: uuid.UUID | str) -> list[VerificationRun]:
    """Evidence validation, provenance, statistics, baseline and independent evaluation (idempotent)."""
    return [
        validate_evidence(db, actor, verification_id),
        check_provenance(db, actor, verification_id),
        check_statistical_support(db, actor, verification_id),
        check_baseline(db, actor, verification_id),
        independent_evaluation(db, actor, verification_id),
    ]


def dataset_and_artifact_counts(db: Session, claim: ScientificClaim) -> dict[str, int]:
    """Small helper for summaries: how many dataset/artifact versions a claim cites."""
    counts = dict(
        db.execute(
            select(ClaimEvidence.evidence_type, func.count(ClaimEvidence.id))
            .where(
                ClaimEvidence.claim_id == claim.id,
                ClaimEvidence.evidence_type.in_(["dataset_version", "artifact_version"]),
            )
            .group_by(ClaimEvidence.evidence_type)
        ).all()
    )
    return {"dataset_versions": int(counts.get("dataset_version", 0)), "artifact_versions": int(counts.get("artifact_version", 0))}


__all__ = [
    "CHECK_BASELINE",
    "CHECK_CONTRADICTION",
    "CHECK_EVIDENCE",
    "CHECK_INDEPENDENT",
    "CHECK_MODEL",
    "CHECK_PROVENANCE",
    "CHECK_REPRODUCTION",
    "CHECK_STATISTICS",
    "DETERMINISTIC_CHECKS",
    "ArtifactVersion",
    "DatasetVersion",
    "check_baseline",
    "check_provenance",
    "check_statistical_support",
    "finalize_verification",
    "independent_evaluation",
    "record_model_assessment",
    "request_reproduction_for_verification",
    "start_verification",
    "validate_evidence",
]
