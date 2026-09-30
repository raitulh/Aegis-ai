"""Scientific claims: creation, deterministic extraction from comparisons, and hash-anchored evidence links.

* ``create_claim`` — a human/API claim (``UNVERIFIED``). Agents cannot create claims directly: model-proposed
  claims go through :func:`propose_model_claim`, which only accepts ``CANDIDATE`` claims whose every number
  matches the recorded statistics ("no LLM output is automatically scientific truth").
* ``extract_claims_from_comparison`` — the deterministic path: an ``improved`` comparison yields exactly one
  ``CANDIDATE`` claim whose statement is templated from the recorded numbers only, idempotent per comparison,
  auto-linked to all of its evidence (comparison, runs, versions, evaluations, datasets, code, environment,
  output artifacts).
* ``link_evidence`` — append-only links. Every link is anchored in the organization's hash-chained evidence
  store (``evidence_id`` + ``content_hash``) with a fingerprint of the referenced record (checksums, spec hash,
  metrics hash…) so verification can later prove nothing changed underneath the claim.
"""

from __future__ import annotations

import math
import re
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from aegis_api.errors import Forbidden, NotFound, ValidationFailed
from aegis_api.lab.core.access import get_owned, load_project, visible_project_ids
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.events import EventType, emit
from aegis_api.lab.core.features import feature_enabled
from aegis_api.lab.core.locks import advisory_xact_lock
from aegis_api.lab.core.pagination import CursorPage, CursorParams, paginate_keyset
from aegis_api.lab.models import (
    AgentRun,
    Artifact,
    ArtifactVersion,
    ClaimEvidence,
    CodeSnapshot,
    DatasetVersion,
    EvaluationRun,
    ExecutionEnvironment,
    Experiment,
    ExperimentComparison,
    ExperimentRun,
    ExperimentVersion,
    GraphNode,
    Memory,
    Mission,
    Project,
    Reproduction,
    ResearchSource,
    ScientificClaim,
    Verification,
)
from aegis_api.lab.verification.common import (
    anchor,
    canonical_hash,
    clean_text,
    get_many,
    jsonable,
    log,
    optional_module,
    parse_spec,
    statistic,
    uuid_set,
)
from aegis_api.lab.verification.schemas import ClaimCreate, ClaimEvidenceOut, ClaimOut
from engines.lab.review import scan_overclaiming
from engines.lab.states import ClaimStatus
from engines.lab.verification import CRITERIA_PROFILES

EVIDENCE_MODELS: dict[str, type[Any]] = {
    "experiment_run": ExperimentRun,
    "experiment_comparison": ExperimentComparison,
    "experiment": Experiment,
    "experiment_version": ExperimentVersion,
    "artifact_version": ArtifactVersion,
    "evaluation_run": EvaluationRun,
    "dataset_version": DatasetVersion,
    "code_snapshot": CodeSnapshot,
    "environment": ExecutionEnvironment,
    "research_source": ResearchSource,
    "memory": Memory,
    "reproduction": Reproduction,
    "verification": Verification,
    "agent_run": AgentRun,
}
RELATIONS: tuple[str, ...] = ("supports", "contradicts", "context")
#: Fingerprint fields that must not change after a record was cited as evidence.
INTEGRITY_FIELDS: dict[str, tuple[str, ...]] = {
    "experiment_run": ("succeeded", "metrics_hash", "seed", "experiment_version_id"),
    "experiment_comparison": ("verdict", "statistics_hash", "baseline_run_ids", "candidate_run_ids"),
    "experiment_version": ("spec_hash", "code_snapshot_id", "environment_id"),
    "artifact_version": ("sha256", "size_bytes"),
    "evaluation_run": ("status", "passed", "metrics_hash"),
    "dataset_version": ("sha256", "size_bytes"),
    "code_snapshot": ("content_hash",),
    "environment": ("content_hash", "image_digest"),
    "research_source": ("checksum",),
    "memory": ("content_hash",),
}
EVIDENCE_KIND = "claim_evidence"
CLAIM_SOURCE_COMPARISON = "experiment_comparison"
MAX_NOTE_CHARS = 4000
#: Relative tolerance used when checking numbers written by a model against recorded statistics.
MODEL_NUMBER_REL_TOL = 0.005


@dataclass
class ClaimExtraction:
    claims: list[ScientificClaim]
    created: bool
    skipped_reason: str | None = None


# ---------------------------------------------------------------------------------------------
# Mappers
# ---------------------------------------------------------------------------------------------
def claim_out(claim: ScientificClaim) -> ClaimOut:
    return ClaimOut.model_validate(claim)


def evidence_out(link: ClaimEvidence) -> ClaimEvidenceOut:
    return ClaimEvidenceOut.model_validate(link)


# ---------------------------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------------------------
def load_claim(db: Session, actor: Actor, claim_id: uuid.UUID | str, *permissions: str) -> ScientificClaim:
    """Load a claim the actor may see (404 across tenants / invisible projects) and check permissions."""
    claim = get_owned(db, ScientificClaim, claim_id, actor, label="Claim")
    try:
        load_project(db, actor, claim.project_id, *permissions)
    except NotFound as exc:
        raise NotFound("Claim not found") from exc
    return claim


def claim_evidence(db: Session, claim_id: uuid.UUID) -> list[ClaimEvidence]:
    return list(
        db.scalars(
            select(ClaimEvidence)
            .where(ClaimEvidence.claim_id == claim_id)
            .order_by(ClaimEvidence.created_at, ClaimEvidence.id)
        ).all()
    )


def list_claims(
    db: Session,
    actor: Actor,
    params: CursorParams,
    *,
    status: str | None = None,
    mission_id: uuid.UUID | str | None = None,
    project_id: uuid.UUID | str | None = None,
) -> CursorPage[ClaimOut]:
    stmt = select(ScientificClaim).where(ScientificClaim.organization_id == actor.organization_id)
    if status:
        value = status.upper()
        if value not in ClaimStatus.__members__:
            raise ValidationFailed(f"status must be one of {', '.join(ClaimStatus.__members__)}")
        stmt = stmt.where(ScientificClaim.status == value)
    if project_id:
        stmt = stmt.where(ScientificClaim.project_id == load_project(db, actor, project_id, "claim:read").id)
    if mission_id:
        mission = get_owned(db, Mission, mission_id, actor, label="Mission")
        stmt = stmt.where(ScientificClaim.mission_id == mission.id)
    visible = visible_project_ids(db, actor)
    if visible is not None:
        stmt = stmt.where(ScientificClaim.project_id.in_(visible))
    return paginate_keyset(
        db, stmt, params, time_col=ScientificClaim.created_at, id_col=ScientificClaim.id, mapper=claim_out
    )


# ---------------------------------------------------------------------------------------------
# Evidence fingerprints
# ---------------------------------------------------------------------------------------------
def _ids(values: Iterable[Any]) -> list[str]:
    return [str(v) for v in values]


def fingerprint(evidence_type: str, row: Any) -> dict[str, Any]:
    """Deterministic description of the cited record, anchored with the link (JSON-safe)."""
    record: dict[str, Any]
    if evidence_type == "experiment_run":
        record = {
            "run_id": row.id,
            "experiment_id": row.experiment_id,
            "experiment_version_id": row.experiment_version_id,
            "role": row.role,
            "seed": row.seed,
            "status": row.status,
            "succeeded": row.status in ("SUCCEEDED", "VERIFICATION_PENDING", "VERIFIED"),
            "exit_code": row.exit_code,
            "metrics_hash": canonical_hash(row.metrics or {}),
            "image_digest": (row.environment_manifest or {}).get("image_digest"),
            "completed_at": row.completed_at,
        }
    elif evidence_type == "experiment_comparison":
        stats = row.statistics or {}
        record = {
            "comparison_id": row.id,
            "metric": row.metric,
            "direction": row.direction,
            "verdict": row.verdict,
            "statistics_hash": canonical_hash(stats),
            "delta": statistic(stats, "delta"),
            "ci_low": statistic(stats, "ci_low"),
            "ci_high": statistic(stats, "ci_high"),
            "p_value": statistic(stats, "p_value"),
            "p_value_adjusted": statistic(stats, "p_value_adjusted"),
            "baseline_run_ids": sorted(_ids(row.baseline_run_ids or [])),
            "candidate_run_ids": sorted(_ids(row.candidate_run_ids or [])),
            "config_diff_hash": canonical_hash(row.config_diff or {}),
            "evaluator_version": row.evaluator_version,
        }
    elif evidence_type == "experiment":
        record = {
            "experiment_id": row.id,
            "title": clean_text(row.title, 300),
            "kind": row.kind,
            "current_version_id": row.current_version_id,
            "hypothesis_id": row.hypothesis_id,
        }
    elif evidence_type == "experiment_version":
        record = {
            "experiment_version_id": row.id,
            "experiment_id": row.experiment_id,
            "version": row.version,
            "spec_hash": row.spec_hash,
            "code_snapshot_id": row.code_snapshot_id,
            "environment_id": row.environment_id,
            "dataset_version_ids": sorted(_ids(row.dataset_version_ids or [])),
        }
    elif evidence_type == "artifact_version":
        record = {
            "artifact_version_id": row.id,
            "artifact_id": row.artifact_id,
            "version": row.version,
            "sha256": row.checksum,
            "size_bytes": row.size_bytes,
            "mime_type": row.mime_type,
        }
    elif evidence_type == "evaluation_run":
        record = {
            "evaluation_run_id": row.id,
            "experiment_run_id": row.experiment_run_id,
            "evaluator_key": row.evaluator_key,
            "evaluator_version": row.evaluator_version,
            "status": row.status,
            "passed": row.passed,
            "independent": row.independent,
            "metrics_hash": canonical_hash(row.metrics or {}),
        }
    elif evidence_type == "dataset_version":
        record = {
            "dataset_version_id": row.id,
            "dataset_id": row.dataset_id,
            "version": row.version,
            "sha256": row.checksum,
            "size_bytes": row.size_bytes,
            "format": row.format,
        }
    elif evidence_type == "code_snapshot":
        record = {
            "code_snapshot_id": row.id,
            "content_hash": row.content_hash,
            "source": row.source,
            "git_commit": row.git_commit,
            "entrypoint": row.entrypoint,
        }
    elif evidence_type == "environment":
        record = {
            "environment_id": row.id,
            "image": row.image,
            "image_digest": row.image_digest,
            "lockfile_hash": row.lockfile_hash,
            "content_hash": row.content_hash,
        }
    elif evidence_type == "research_source":
        record = {
            "source_id": row.id,
            "title": clean_text(row.title, 300),
            "url": row.canonical_url or row.url,
            "doi": row.doi,
            "checksum": row.checksum,
        }
    elif evidence_type == "memory":
        record = {"memory_id": row.id, "category": row.category, "content_hash": row.content_hash}
    elif evidence_type == "reproduction":
        record = {
            "reproduction_id": row.id,
            "experiment_id": row.experiment_id,
            "experiment_version_id": row.experiment_version_id,
            "status": row.status,
            "verdict": row.verdict,
        }
    elif evidence_type == "verification":
        record = {
            "verification_id": row.id,
            "claim_id": row.claim_id,
            "status": row.status,
            "verdict": row.verdict,
            "confidence": row.confidence,
        }
    elif evidence_type == "agent_run":
        record = {
            "agent_run_id": row.id,
            "role": row.role,
            "agent_version_id": row.agent_version_id,
            "provider": row.provider,
            "model": row.model,
            "model_version": row.model_version,
            "prompt_key": row.prompt_key,
            "prompt_version": row.prompt_version,
            "prompt_hash": row.prompt_hash,
            "status": row.status,
        }
    else:  # pragma: no cover - guarded by EVIDENCE_MODELS
        raise ValidationFailed(f"Unsupported evidence type '{evidence_type}'")
    return jsonable(record)


def row_project_id(db: Session, evidence_type: str, row: Any) -> uuid.UUID | None:
    """Project that owns a cited record (``None`` for organization-level records such as environments)."""
    project_id = getattr(row, "project_id", None)
    if project_id is not None:
        return project_id  # type: ignore[no-any-return]
    experiment_id: uuid.UUID | None = None
    if evidence_type == "experiment_version":
        experiment_id = row.experiment_id
    elif evidence_type == "experiment_comparison":
        experiment_id = row.candidate_experiment_id
    elif evidence_type == "evaluation_run":
        experiment_id = row.experiment_id
    if experiment_id is not None:
        experiment = db.get(Experiment, experiment_id)
        return experiment.project_id if experiment is not None else None
    return None


def resolve_reference(db: Session, actor: Actor, evidence_type: str, ref_id: uuid.UUID | str) -> Any:
    """Load a cited record through the tenant session; 404 when foreign or in a project the actor cannot see."""
    model = EVIDENCE_MODELS.get(evidence_type)
    if model is None:
        raise ValidationFailed(f"evidence_type must be one of {', '.join(sorted(EVIDENCE_MODELS))}")
    label = evidence_type.replace("_", " ").capitalize()
    row = get_owned(db, model, ref_id, actor, label=label)
    project_id = row_project_id(db, evidence_type, row)
    if project_id is not None:
        try:
            load_project(db, actor, project_id)
        except NotFound as exc:
            raise NotFound(f"{label} not found") from exc
    if evidence_type == "artifact_version":
        artifact = db.get(Artifact, row.artifact_id)
        if artifact is None or artifact.deleted_at is not None:
            raise NotFound(f"{label} not found")
    return row


# ---------------------------------------------------------------------------------------------
# Linking (append-only, hash-anchored)
# ---------------------------------------------------------------------------------------------
def _existing_links(db: Session, claim_id: uuid.UUID) -> dict[tuple[str, uuid.UUID, str], ClaimEvidence]:
    return {(link.evidence_type, link.ref_id, link.relation): link for link in claim_evidence(db, claim_id)}


def _append_link(
    db: Session,
    claim: ScientificClaim,
    evidence_type: str,
    row: Any,
    relation: str,
    *,
    note: str | None = None,
    weight: float = 1.0,
) -> ClaimEvidence:
    record = fingerprint(evidence_type, row)
    content = {
        "claim_id": str(claim.id),
        "evidence_type": evidence_type,
        "ref_id": str(row.id),
        "relation": relation,
        "record": record,
    }
    evidence = anchor(
        db,
        claim.organization_id,
        EVIDENCE_KIND,
        f"Claim {claim.id} {relation} {evidence_type} {row.id}",
        content,
    )
    link = ClaimEvidence(
        id=uuid.uuid4(),
        organization_id=claim.organization_id,
        claim_id=claim.id,
        evidence_type=evidence_type,
        ref_id=row.id,
        relation=relation,
        weight=weight,
        note=note,
        evidence_id=evidence.id,
        content_hash=evidence.content_hash,
    )
    db.add(link)
    return link


def _refresh_evidence_counts(db: Session, claim: ScientificClaim) -> None:
    counts = dict(
        db.execute(
            select(ClaimEvidence.relation, func.count(ClaimEvidence.id))
            .where(ClaimEvidence.claim_id == claim.id)
            .group_by(ClaimEvidence.relation)
        ).all()
    )
    uncertainty = dict(claim.uncertainty or {})
    uncertainty["supporting_evidence"] = int(counts.get("supports", 0))
    uncertainty["conflicting_evidence"] = int(counts.get("contradicts", 0))
    uncertainty["context_evidence"] = int(counts.get("context", 0))
    claim.uncertainty = uncertainty


def link_rows(
    db: Session, claim: ScientificClaim, links: Sequence[tuple[str, Any, str]], *, note: str | None = None
) -> list[ClaimEvidence]:
    """Link already-authorized records (``(evidence_type, row, relation)``) to ``claim`` (idempotent)."""
    advisory_xact_lock(db, f"claim-evidence:{claim.id}")
    existing = _existing_links(db, claim.id)
    created: list[ClaimEvidence] = []
    for evidence_type, row, relation in links:
        key = (evidence_type, row.id, relation)
        if key in existing:
            continue
        link = _append_link(db, claim, evidence_type, row, relation, note=note)
        existing[key] = link
        created.append(link)
    if created:
        db.flush()
        _refresh_evidence_counts(db, claim)
        db.flush()
    return created


def link_evidence(
    db: Session,
    actor: Actor,
    claim_id: uuid.UUID | str,
    evidence_type: str,
    ref_id: uuid.UUID | str,
    relation: str = "supports",
    note: str | None = None,
    *,
    weight: float = 1.0,
) -> ClaimEvidence:
    """Attach a record as evidence (append-only; idempotent for the same type + record + relation)."""
    claim = load_claim(db, actor, claim_id, "claim:create")
    if relation not in RELATIONS:
        raise ValidationFailed(f"relation must be one of {', '.join(RELATIONS)}")
    if note is not None and len(note) > MAX_NOTE_CHARS:
        raise ValidationFailed(f"note must be at most {MAX_NOTE_CHARS} characters")
    if isinstance(weight, bool) or not 0.0 <= float(weight) <= 1.0:
        raise ValidationFailed("weight must be between 0 and 1")
    row = resolve_reference(db, actor, evidence_type, ref_id)
    advisory_xact_lock(db, f"claim-evidence:{claim.id}")
    existing = db.scalar(
        select(ClaimEvidence).where(
            ClaimEvidence.claim_id == claim.id,
            ClaimEvidence.evidence_type == evidence_type,
            ClaimEvidence.ref_id == row.id,
            ClaimEvidence.relation == relation,
        )
    )
    if existing is not None:
        return existing
    link = _append_link(db, claim, evidence_type, row, relation, note=note, weight=float(weight))
    db.flush()
    _refresh_evidence_counts(db, claim)
    db.flush()
    return link


# ---------------------------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------------------------
def _profile(value: str | None) -> str:
    profile = value or "default"
    if profile not in CRITERIA_PROFILES:
        raise ValidationFailed(f"criteria_profile must be one of {', '.join(sorted(CRITERIA_PROFILES))}")
    return profile


def _mission_in_project(db: Session, actor: Actor, mission_id: uuid.UUID | str | None, project: Project) -> Mission | None:
    if mission_id in (None, ""):
        return None
    mission = get_owned(db, Mission, mission_id, actor, label="Mission")
    if mission.project_id != project.id:
        raise ValidationFailed("mission_id does not belong to the claim's project")
    return mission


def _language_flags(statement: str) -> list[dict[str, Any]]:
    return [{"label": hit["label"], "match": hit["match"]} for hit in scan_overclaiming(statement)][:20]


def _announce(db: Session, actor: Actor, claim: ScientificClaim) -> None:
    emit(
        db,
        organization_id=claim.organization_id,
        type=EventType.CLAIM_CREATED,
        payload={
            "claim_id": str(claim.id),
            "status": claim.status,
            "statement": clean_text(claim.statement, 500),
            "metric": claim.metric,
            "source_type": claim.source_type,
            "source_id": str(claim.source_id) if claim.source_id else None,
            "extracted_by": claim.extracted_by,
        },
        mission_id=claim.mission_id,
        project_id=claim.project_id,
        workspace_id=claim.workspace_id,
        subject_type="claim",
        subject_id=claim.id,
        actor=actor,
    )
    _graph_node(db, actor, claim)


def _graph_node(db: Session, actor: Actor, claim: ScientificClaim) -> None:
    """Project the claim into the knowledge graph (best-effort; the claim row is the source of truth)."""
    key = f"claim:{claim.id}"
    label = clean_text(claim.statement, 1000)
    properties = {"status": claim.status, "metric": claim.metric, "source_type": claim.source_type}
    graph_module = optional_module("aegis_api.lab.knowledge.graph")
    try:
        with db.begin_nested():
            if graph_module is not None and hasattr(graph_module, "get_graph"):
                graph = graph_module.get_graph(db, actor)
                graph.upsert_node(
                    "Claim",
                    key,
                    label,
                    ref_type="scientific_claim",
                    ref_id=claim.id,
                    project_id=claim.project_id,
                    properties=properties,
                )
            elif feature_enabled(db, claim.organization_id, "graph_memory"):
                db.execute(
                    insert(GraphNode)
                    .values(
                        id=uuid.uuid4(),
                        organization_id=claim.organization_id,
                        project_id=claim.project_id,
                        node_type="Claim",
                        key=key,
                        label=label,
                        ref_type="scientific_claim",
                        ref_id=claim.id,
                        properties=properties,
                    )
                    .on_conflict_do_nothing(constraint="uq_graph_nodes_type_key")
                )
    except Exception:  # the graph is a derived projection; never fail claim creation because of it
        log.warning("claim_graph_projection_failed", claim_id=str(claim.id), exc_info=True)


def create_claim(
    db: Session,
    actor: Actor,
    data: ClaimCreate,
) -> ScientificClaim:
    """Create a human-authored claim (``UNVERIFIED``). Agents must use :func:`propose_model_claim`."""
    if actor.kind == "agent":
        raise Forbidden(
            "Agents cannot assert claims directly; model-proposed claims are validated against recorded metrics",
            code="agent_claim_requires_validation",
        )
    project = load_project(db, actor, data.project_id, "claim:create")
    mission = _mission_in_project(db, actor, data.mission_id, project)
    profile = _profile(data.criteria_profile)
    claim = ScientificClaim(
        id=uuid.uuid4(),
        organization_id=actor.organization_id,
        workspace_id=project.workspace_id,
        project_id=project.id,
        mission_id=mission.id if mission else None,
        statement=data.statement,
        claim_type=data.claim_type,
        domain=data.domain,
        metric=data.metric,
        direction=data.direction,
        value=data.value,
        ci_low=data.ci_low,
        ci_high=data.ci_high,
        units=data.units,
        conditions=jsonable(data.conditions),
        source_type="human",
        source_id=None,
        extracted_by="human",
        status=ClaimStatus.UNVERIFIED,
        confidence=0.0,
        uncertainty={
            "evidence_quality": "none",
            "missing_evidence": [],
            "failed_reproductions": 0,
            "supporting_evidence": 0,
            "conflicting_evidence": 0,
            "language_flags": _language_flags(data.statement),
            "notes": [],
        },
        criteria_profile=profile,
        created_by_id=actor.user_id,
    )
    db.add(claim)
    db.flush()
    _announce(db, actor, claim)
    return claim


# ---------------------------------------------------------------------------------------------
# Deterministic extraction from comparisons
# ---------------------------------------------------------------------------------------------
def _title(text: str | None) -> str:
    return clean_text((text or "untitled").replace("'", "’"), 120)


def _fmt(value: float | None, spec: str) -> str:
    return "n/a" if value is None else format(value, spec)


def comparison_statement(candidate_title: str, baseline_title: str, metric: str, stats: Mapping[str, Any]) -> str:
    """The claim sentence — built from the recorded numbers only (never from model text)."""
    delta = statistic(stats, "delta")
    relative = statistic(stats, "relative_delta")
    ci_low, ci_high = statistic(stats, "ci_low"), statistic(stats, "ci_high")
    ci_level = statistic(stats, "ci_level") or 0.95
    p_adjusted = statistic(stats, "p_value_adjusted")
    family = int(statistic(stats, "family_size") or 1)
    p = p_adjusted if p_adjusted is not None and family > 1 else statistic(stats, "p_value")
    test = clean_text(str(stats.get("test") or "test"), 40)
    n_b = int(statistic(stats, "n_baseline") or 0)
    n_c = int(statistic(stats, "n_candidate") or 0)
    relative_text = "n/a" if relative is None else f"{relative * 100:.3g}%"
    ci_text = (
        f"{ci_level * 100:.0f}% CI [{ci_low:.4g}, {ci_high:.4g}]"
        if ci_low is not None and ci_high is not None
        else f"{ci_level * 100:.0f}% CI n/a"
    )
    return (
        f"Candidate '{_title(candidate_title)}' improves {clean_text(metric, 120)} over baseline "
        f"'{_title(baseline_title)}' by Δ={_fmt(delta, '.4g')} (relative {relative_text}, {ci_text}, "
        f"{test} p={_fmt(p, '.3g')}, n={n_b}/{n_c} seeds)."
    )


@dataclass
class _ComparisonContext:
    comparison: ExperimentComparison
    candidate: Experiment
    baseline: Experiment
    project: Project


def _comparison_context(
    db: Session, actor: Actor, comparison_id: uuid.UUID | str, *permissions: str
) -> _ComparisonContext:
    comparison = get_owned(db, ExperimentComparison, comparison_id, actor, label="Comparison")
    candidate = db.get(Experiment, comparison.candidate_experiment_id)
    baseline = db.get(Experiment, comparison.baseline_experiment_id)
    if candidate is None or baseline is None:
        raise NotFound("Comparison not found")
    project_id = comparison.project_id or candidate.project_id
    try:
        project = load_project(db, actor, project_id, *permissions)
    except NotFound as exc:
        raise NotFound("Comparison not found") from exc
    return _ComparisonContext(comparison=comparison, candidate=candidate, baseline=baseline, project=project)


def comparison_evidence_rows(db: Session, comparison: ExperimentComparison) -> list[tuple[str, Any, str]]:
    """Every record behind a comparison, with its relation to a claim about the candidate (batched loads)."""
    org = comparison.organization_id
    candidate_ids = uuid_set(comparison.candidate_run_ids or [])
    baseline_ids = uuid_set(comparison.baseline_run_ids or [])
    runs = get_many(db, ExperimentRun, candidate_ids | baseline_ids, org)
    experiments = get_many(db, Experiment, {comparison.candidate_experiment_id, comparison.baseline_experiment_id}, org)
    version_ids = {comparison.candidate_version_id, comparison.baseline_version_id} | {
        r.experiment_version_id for r in runs.values()
    }
    versions = get_many(db, ExperimentVersion, version_ids, org)
    evaluations = (
        db.scalars(
            select(EvaluationRun)
            .where(EvaluationRun.experiment_run_id.in_(sorted(runs)))
            .order_by(EvaluationRun.created_at, EvaluationRun.id)
        ).all()
        if runs
        else []
    )
    dataset_ids = uuid_set(d for v in versions.values() for d in (v.dataset_version_ids or []))
    datasets = get_many(db, DatasetVersion, dataset_ids, org)
    snapshots = get_many(db, CodeSnapshot, {v.code_snapshot_id for v in versions.values()}, org)
    environments = get_many(db, ExecutionEnvironment, {v.environment_id for v in versions.values()}, org)
    artifacts = run_artifact_versions(db, runs.values())

    rows: list[tuple[str, Any, str]] = [("experiment_comparison", comparison, "supports")]
    for exp_id in (comparison.candidate_experiment_id, comparison.baseline_experiment_id):
        if exp_id in experiments:
            rows.append(("experiment", experiments[exp_id], "context"))
    for version_id in sorted(versions, key=str):
        rows.append(("experiment_version", versions[version_id], "context"))
    for run_id in sorted(runs, key=str):
        rows.append(("experiment_run", runs[run_id], "supports" if run_id in candidate_ids else "context"))
    for evaluation in evaluations:
        relation = "supports" if evaluation.experiment_run_id in candidate_ids else "context"
        rows.append(("evaluation_run", evaluation, relation))
    for run_id, versions_of_run in sorted(artifacts.items(), key=lambda item: str(item[0])):
        relation = "supports" if run_id in candidate_ids else "context"
        for version in versions_of_run:
            rows.append(("artifact_version", version, relation))
    for dataset_id in sorted(datasets, key=str):
        rows.append(("dataset_version", datasets[dataset_id], "context"))
    for snapshot_id in sorted(snapshots, key=str):
        rows.append(("code_snapshot", snapshots[snapshot_id], "context"))
    for env_id in sorted(environments, key=str):
        rows.append(("environment", environments[env_id], "context"))
    return rows


def run_artifact_versions(db: Session, runs: Iterable[ExperimentRun]) -> dict[uuid.UUID, list[ArtifactVersion]]:
    """Output artifact versions of runs (linked by run id or by the run's compute job), in one query."""
    run_list = list(runs)
    if not run_list:
        return {}
    run_ids = [r.id for r in run_list]
    job_to_run = {r.compute_job_id: r.id for r in run_list if r.compute_job_id is not None}
    conditions = [Artifact.experiment_run_id.in_(run_ids)]
    if job_to_run:
        conditions.append(Artifact.compute_job_id.in_(list(job_to_run)))
    rows = db.execute(
        select(ArtifactVersion, Artifact.experiment_run_id, Artifact.compute_job_id)
        .join(Artifact, Artifact.id == ArtifactVersion.artifact_id)
        .where(or_(*conditions), Artifact.deleted_at.is_(None))
        .order_by(ArtifactVersion.created_at, ArtifactVersion.id)
    ).all()
    out: dict[uuid.UUID, list[ArtifactVersion]] = {}
    seen: set[uuid.UUID] = set()
    known = set(run_ids)
    for version, run_id, job_id in rows:
        owner = run_id if run_id in known else job_to_run.get(job_id)
        if owner is None or version.id in seen:
            continue
        seen.add(version.id)
        out.setdefault(owner, []).append(version)
    return out


def _metric_unit(version: ExperimentVersion | None, metric: str) -> str | None:
    spec = parse_spec(version.spec) if version is not None else None
    item = spec.metric(metric) if spec is not None else None
    return item.unit if item is not None else None


def extract_claims_from_comparison(
    db: Session, actor: Actor, comparison_id: uuid.UUID | str, *, criteria_profile: str | None = None
) -> ClaimExtraction:
    """Deterministic claim from an ``improved`` comparison (idempotent per comparison)."""
    ctx = _comparison_context(db, actor, comparison_id, "claim:create")
    comparison = ctx.comparison
    advisory_xact_lock(db, f"claim-extract:{comparison.id}")
    existing = db.scalars(
        select(ScientificClaim)
        .where(
            ScientificClaim.source_type == CLAIM_SOURCE_COMPARISON,
            ScientificClaim.source_id == comparison.id,
            ScientificClaim.extracted_by == "rule",
        )
        .order_by(ScientificClaim.created_at)
    ).all()
    if existing:
        return ClaimExtraction(claims=list(existing), created=False)
    if comparison.verdict != "improved":
        return ClaimExtraction(
            claims=[],
            created=False,
            skipped_reason=f"comparison verdict is '{comparison.verdict}'; only 'improved' comparisons yield claims",
        )
    stats = comparison.statistics or {}
    if statistic(stats, "delta") is None:
        return ClaimExtraction(claims=[], created=False, skipped_reason="the comparison has no effect estimate")
    profile = _profile(criteria_profile)
    mission_id = comparison.mission_id or ctx.candidate.mission_id
    mission = db.get(Mission, mission_id) if mission_id else None
    candidate_version = db.get(ExperimentVersion, comparison.candidate_version_id)
    statement = comparison_statement(ctx.candidate.title, ctx.baseline.title, comparison.metric, stats)
    p_adjusted = statistic(stats, "p_value_adjusted")
    claim = ScientificClaim(
        id=uuid.uuid4(),
        organization_id=actor.organization_id,
        workspace_id=ctx.project.workspace_id,
        project_id=ctx.project.id,
        mission_id=mission.id if mission is not None and mission.project_id == ctx.project.id else None,
        statement=statement,
        claim_type="comparative",
        domain=mission.domain if mission is not None else "general",
        metric=comparison.metric,
        direction="increase" if comparison.direction == "maximize" else "decrease",
        value=statistic(stats, "delta"),
        ci_low=statistic(stats, "ci_low"),
        ci_high=statistic(stats, "ci_high"),
        units=_metric_unit(candidate_version, comparison.metric),
        conditions=jsonable(
            {
                "comparison_id": comparison.id,
                "comparison_type": comparison.comparison_type,
                "baseline_experiment_id": comparison.baseline_experiment_id,
                "candidate_experiment_id": comparison.candidate_experiment_id,
                "baseline_version_id": comparison.baseline_version_id,
                "candidate_version_id": comparison.candidate_version_id,
                "metric_direction": comparison.direction,
                "test": stats.get("test"),
                "alpha": statistic(stats, "alpha"),
                "correction": stats.get("correction"),
                "p_value": statistic(stats, "p_value"),
                "p_value_adjusted": p_adjusted,
                "effect_size": statistic(stats, "effect_size"),
                "effect_size_kind": stats.get("effect_size_kind"),
                "relative_delta": statistic(stats, "relative_delta"),
                "n_baseline": statistic(stats, "n_baseline"),
                "n_candidate": statistic(stats, "n_candidate"),
                "metric_source": stats.get("metric_source"),
                "config_diff_keys": sorted(
                    key for part in (comparison.config_diff or {}).values() if isinstance(part, dict) for key in part
                )[:100],
                "comparison_engine": comparison.evaluator_version,
            }
        ),
        source_type=CLAIM_SOURCE_COMPARISON,
        source_id=comparison.id,
        extracted_by="rule",
        extractor_agent_run_id=None,
        status=ClaimStatus.CANDIDATE,
        confidence=0.0,
        uncertainty={
            "evidence_quality": "self_reported" if stats.get("metric_source") == "self_reported" else "measured",
            "missing_evidence": [],
            "failed_reproductions": 0,
            "supporting_evidence": 0,
            "conflicting_evidence": 0,
            "language_flags": [],
            "notes": ["extracted deterministically from the recorded comparison statistics"],
        },
        criteria_profile=profile,
        created_by_id=actor.user_id,
    )
    db.add(claim)
    db.flush()
    link_rows(db, claim, comparison_evidence_rows(db, comparison), note="auto-linked from comparison")
    _announce(db, actor, claim)
    return ClaimExtraction(claims=[claim], created=True)


# ---------------------------------------------------------------------------------------------
# Model-proposed claims (validated against recorded numbers)
# ---------------------------------------------------------------------------------------------
_NUMBER = re.compile(r"(?<![A-Za-z0-9_.])[-+−]?(?:\d+(?:[.,]\d+)*(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?")


def numbers_in_text(text: str) -> list[tuple[float, int]]:
    """Numbers written in ``text`` as ``(value, decimals)`` (thousands separators and unicode minus handled)."""
    found: list[tuple[float, int]] = []
    for match in _NUMBER.finditer(text):
        raw = match.group(0).replace("−", "-").replace(",", "")
        mantissa = re.split(r"[eE]", raw)[0]
        decimals = len(mantissa.split(".", 1)[1]) if "." in mantissa else 0
        try:
            value = float(raw)
        except ValueError:
            continue
        if math.isfinite(value):
            found.append((value, decimals))
    return found


def _recorded_numbers(stats: Mapping[str, Any]) -> list[float]:
    values: list[float] = []
    for name in (
        "delta",
        "ci_low",
        "ci_high",
        "p_value",
        "p_value_adjusted",
        "effect_size",
        "mean_baseline",
        "mean_candidate",
        "median_baseline",
        "median_candidate",
        "sd_baseline",
        "sd_candidate",
        "improvement",
        "n_baseline",
        "n_candidate",
        "alpha",
        "statistic",
        "df",
    ):
        value = statistic(stats, name)
        if value is not None:
            values.append(value)
    for name in ("relative_delta", "ci_level", "alpha"):
        value = statistic(stats, name)
        if value is not None:
            values += [value * 100.0, value]
    return values


def _matches(value: float, decimals: int, recorded: Sequence[float]) -> bool:
    """``value`` (written with ``decimals`` decimals) equals a recorded number up to rounding.

    Magnitudes are compared: a model may write "reduces loss by 0.05" for a recorded delta of -0.05; the
    stored claim direction always comes from the record.
    """
    rounding = 0.5 * 10 ** (-decimals)
    for reference in recorded:
        tolerance = max(rounding, MODEL_NUMBER_REL_TOL * abs(reference), 1e-12)
        if abs(abs(value) - abs(reference)) <= tolerance:
            return True
    return False


def check_model_numbers(
    statement: str,
    stats: Mapping[str, Any],
    *,
    ignore_texts: Iterable[str] = (),
    value: float | None = None,
    ci_low: float | None = None,
    ci_high: float | None = None,
) -> list[str]:
    """Deterministic check that every number a model wrote matches a recorded statistic; returns mismatches."""
    text = statement
    for fragment in ignore_texts:
        if fragment:
            text = text.replace(fragment, " ")
    recorded = _recorded_numbers(stats)
    problems = [
        f"number {number:g} in the statement does not match any recorded statistic"
        for number, decimals in numbers_in_text(text)
        if not _matches(number, decimals, recorded)
    ]
    for name, given in (("value", value), ("ci_low", ci_low), ("ci_high", ci_high)):
        if given is None:
            continue
        reference = statistic(stats, "delta" if name == "value" else name)
        if reference is None:
            problems.append(f"{name}={given:g} but no recorded {name} exists")
        elif abs(given - reference) > max(MODEL_NUMBER_REL_TOL * abs(reference), 1e-9):
            problems.append(f"{name}={given:g} does not match the recorded value {reference:g}")
    return problems


def propose_model_claim(
    db: Session,
    actor: Actor,
    *,
    comparison_id: uuid.UUID | str,
    statement: str,
    value: float | None = None,
    ci_low: float | None = None,
    ci_high: float | None = None,
    source_type: str = "agent_run",
    source_id: uuid.UUID | str | None = None,
    agent_run_id: uuid.UUID | None = None,
    criteria_profile: str | None = None,
) -> ScientificClaim:
    """A model-proposed claim about a recorded comparison: CANDIDATE only, numbers checked deterministically.

    The stored numbers (value, CI, metric, direction) always come from the recorded comparison, never from
    the model; a statement whose numbers do not match the record, or that uses certainty language, is rejected.
    """
    ctx = _comparison_context(db, actor, comparison_id, "claim:create")
    comparison = ctx.comparison
    text = " ".join((statement or "").split())
    if not 10 <= len(text) <= 1000:
        raise ValidationFailed("statement must be 10-1000 characters")
    if source_type not in ("agent_run", "report"):
        raise ValidationFailed("source_type must be agent_run or report")
    if comparison.verdict != "improved":
        raise ValidationFailed(
            f"the comparison verdict is '{comparison.verdict}'; claims can only describe improved comparisons"
        )
    stats = comparison.statistics or {}
    problems = check_model_numbers(
        text,
        stats,
        ignore_texts=(ctx.candidate.title, ctx.baseline.title, comparison.metric),
        value=value,
        ci_low=ci_low,
        ci_high=ci_high,
    )
    language = scan_overclaiming(text)
    if language:
        problems += [f"overclaiming language: {hit['label']} ({hit['match']!r})" for hit in language]
    if problems:
        raise ValidationFailed(
            "The proposed claim does not match the recorded metrics", details={"problems": problems[:50]}
        )
    run_id = agent_run_id or actor.agent_run_id
    source = uuid.UUID(str(source_id)) if source_id else run_id
    duplicate = db.scalar(
        select(ScientificClaim).where(
            ScientificClaim.source_type == source_type,
            ScientificClaim.source_id == source,
            ScientificClaim.statement == text,
            ScientificClaim.project_id == ctx.project.id,
        )
    )
    if duplicate is not None:
        return duplicate
    claim = ScientificClaim(
        id=uuid.uuid4(),
        organization_id=actor.organization_id,
        workspace_id=ctx.project.workspace_id,
        project_id=ctx.project.id,
        mission_id=comparison.mission_id or ctx.candidate.mission_id,
        statement=text,
        claim_type="comparative",
        domain="general",
        metric=comparison.metric,
        direction="increase" if comparison.direction == "maximize" else "decrease",
        value=statistic(stats, "delta"),
        ci_low=statistic(stats, "ci_low"),
        ci_high=statistic(stats, "ci_high"),
        units=_metric_unit(db.get(ExperimentVersion, comparison.candidate_version_id), comparison.metric),
        conditions=jsonable({"comparison_id": comparison.id, "numbers_checked": True}),
        source_type=source_type,
        source_id=source,
        extracted_by="agent",
        extractor_agent_run_id=run_id,
        status=ClaimStatus.CANDIDATE,
        confidence=0.0,
        uncertainty={
            "evidence_quality": "model_proposed",
            "missing_evidence": [],
            "failed_reproductions": 0,
            "supporting_evidence": 0,
            "conflicting_evidence": 0,
            "language_flags": [],
            "notes": ["model-proposed; numbers matched against the recorded comparison"],
        },
        criteria_profile=_profile(criteria_profile),
        created_by_id=actor.user_id,
    )
    db.add(claim)
    db.flush()
    link_rows(db, claim, comparison_evidence_rows(db, comparison), note="auto-linked from comparison")
    if run_id is not None:
        agent_run = db.get(AgentRun, run_id)
        if agent_run is not None and agent_run.organization_id == actor.organization_id:
            link_rows(db, claim, [("agent_run", agent_run, "context")], note="proposing agent run")
    _announce(db, actor, claim)
    return claim
