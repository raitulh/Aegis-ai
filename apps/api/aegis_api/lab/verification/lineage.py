"""Machine-readable claim lineage: stored records → ``engines.lab.lineage.build_lineage``.

The chain Claim → ClaimEvidence → comparison → experiments → experiment versions → runs → code snapshots →
dataset versions → model provenance (agent runs: provider, model, model version, prompt key/version/hash) →
environments (image digest) → raw artifacts (checksums) → evaluation runs (evaluator key/version) →
verifications / reproductions is loaded with a bounded number of batched queries (one per record type, never
one per row) and handed to the pure lineage engine, which builds the DAG and the completeness report.

Two chain types are *not applicable* in well-defined cases and are then removed from the required chain (and
reported as such, never silently):

* ``ModelVersion`` — when no agent run is referenced anywhere in the lineage (a purely human/rule-derived
  result has no model provenance to record);
* ``DatasetVersion`` — when every experiment version in the lineage declares no dataset (e.g. simulations).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from aegis_api.lab.core.actor import Actor
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
    Reproduction,
    ScientificClaim,
    Verification,
)
from aegis_api.lab.verification.claims import CLAIM_SOURCE_COMPARISON, load_claim, run_artifact_versions
from aegis_api.lab.verification.common import SUCCESS_RUN_STATUSES, get_many, parse_spec, uuid_set
from aegis_api.lab.verification.schemas import LineageOut
from engines.lab.lineage import (
    DEFAULT_REQUIRED_CHAIN,
    EVIDENCE_TYPE_NODES,
    LINEAGE_SCHEMA_VERSION,
    ArtifactRecord,
    ClaimEvidenceRecord,
    ClaimRecord,
    CodeSnapshotRecord,
    DatasetVersionRecord,
    EnvironmentRecord,
    EvaluationRecord,
    ExperimentRecord,
    ExperimentRunRecord,
    ExperimentVersionRecord,
    LineageGraph,
    ModelProvenanceRecord,
    ReproductionRecord,
    VerificationRecord,
    build_lineage,
)


@dataclass
class ClaimLineage:
    graph: LineageGraph
    not_applicable: list[str] = field(default_factory=list)
    agent_run_ids: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return self.graph.completeness.complete

    def to_out(self) -> LineageOut:
        data = self.graph.model_dump()
        data["not_applicable"] = list(self.not_applicable)
        return LineageOut.model_validate(data)

    def summary(self, max_links: int = 100) -> dict[str, Any]:
        completeness = self.graph.completeness
        return {
            "schema_version": self.graph.schema_version,
            "digest": self.graph.digest,
            "complete": completeness.complete,
            "required_chain": completeness.required_chain,
            "present": completeness.present,
            "missing": completeness.missing,
            "missing_links": [m.model_dump() for m in completeness.missing_links[:max_links]],
            "missing_links_total": len(completeness.missing_links),
            "not_applicable": list(self.not_applicable),
            "nodes": len(self.graph.nodes),
            "edges": len(self.graph.edges),
        }


def _opt(value: uuid.UUID | None) -> str | None:
    return str(value) if value is not None else None


def _declares_datasets(version: ExperimentVersion) -> bool:
    if version.dataset_version_ids:
        return True
    spec = parse_spec(version.spec)
    return bool(spec is not None and spec.datasets)


def build_claim_lineage(db: Session, claim: ScientificClaim) -> ClaimLineage:
    """Assemble the lineage of an already-authorized claim (batched queries; no per-row lookups)."""
    org = claim.organization_id
    links = list(
        db.scalars(
            select(ClaimEvidence).where(ClaimEvidence.claim_id == claim.id).order_by(ClaimEvidence.created_at)
        ).all()
    )
    by_type: dict[str, set[uuid.UUID]] = defaultdict(set)
    for link in links:
        by_type[link.evidence_type].add(link.ref_id)

    comparison_ids = set(by_type["experiment_comparison"])
    if claim.source_type == CLAIM_SOURCE_COMPARISON and claim.source_id is not None:
        comparison_ids.add(claim.source_id)
    comparisons = get_many(db, ExperimentComparison, comparison_ids, org)

    experiment_ids = set(by_type["experiment"])
    run_ids = set(by_type["experiment_run"])
    version_ids = set(by_type["experiment_version"])
    for comparison in comparisons.values():
        experiment_ids |= {comparison.baseline_experiment_id, comparison.candidate_experiment_id}
        version_ids |= {comparison.baseline_version_id, comparison.candidate_version_id}
        run_ids |= uuid_set(comparison.baseline_run_ids or []) | uuid_set(comparison.candidate_run_ids or [])

    # Reproductions of the same immutable versions (explicitly linked ones always count).
    repro_filters = [Reproduction.id.in_(sorted(by_type["reproduction"]))] if by_type["reproduction"] else []
    if experiment_ids and version_ids:
        repro_filters.append(
            Reproduction.experiment_id.in_(sorted(experiment_ids))
            & Reproduction.experiment_version_id.in_(sorted(version_ids))
        )
    reproductions: dict[uuid.UUID, Reproduction] = {}
    if repro_filters:
        for repro in db.scalars(select(Reproduction).where(or_(*repro_filters))).all():
            reproductions[repro.id] = repro
    repro_run_ids: set[uuid.UUID] = set()
    for repro in reproductions.values():
        run_ids |= uuid_set(repro.original_run_ids or [])
        repro_run_ids |= uuid_set(repro.reproduction_run_ids or [])

    runs = get_many(db, ExperimentRun, run_ids | repro_run_ids, org)
    # Failed reproduction runs carry no result; the reproduction's verdict already reflects them.
    runs = {
        rid: run
        for rid, run in runs.items()
        if rid in run_ids or rid not in repro_run_ids or run.status in SUCCESS_RUN_STATUSES
    }
    for run in runs.values():
        version_ids.add(run.experiment_version_id)
        experiment_ids.add(run.experiment_id)
    versions = get_many(db, ExperimentVersion, version_ids, org)
    experiment_ids |= {v.experiment_id for v in versions.values()}
    experiments = get_many(db, Experiment, experiment_ids, org)

    code_ids = set(by_type["code_snapshot"]) | {v.code_snapshot_id for v in versions.values() if v.code_snapshot_id}
    env_ids = set(by_type["environment"]) | {v.environment_id for v in versions.values() if v.environment_id}
    dataset_ids = set(by_type["dataset_version"]) | uuid_set(
        d for v in versions.values() for d in (v.dataset_version_ids or [])
    )
    code_snapshots = get_many(db, CodeSnapshot, code_ids, org)
    environments = get_many(db, ExecutionEnvironment, env_ids, org)
    dataset_versions = get_many(db, DatasetVersion, dataset_ids, org)

    artifacts_by_run = run_artifact_versions(db, runs.values())
    artifact_rows: dict[uuid.UUID, tuple[ArtifactVersion, uuid.UUID | None]] = {}
    for run_id, items in artifacts_by_run.items():
        for version in items:
            artifact_rows[version.id] = (version, run_id)
    for version_id, version in get_many(db, ArtifactVersion, by_type["artifact_version"], org).items():
        artifact_rows.setdefault(version_id, (version, None))
    artifact_names: dict[uuid.UUID, tuple[str, str]] = {}
    if artifact_rows:
        artifact_ids = {version.artifact_id for version, _ in artifact_rows.values()}
        for artifact in db.scalars(select(Artifact).where(Artifact.id.in_(sorted(artifact_ids)))).all():
            artifact_names[artifact.id] = (artifact.name, artifact.kind)

    eval_filters = []
    if runs:
        eval_filters.append(EvaluationRun.experiment_run_id.in_(sorted(runs)))
    if by_type["evaluation_run"]:
        eval_filters.append(EvaluationRun.id.in_(sorted(by_type["evaluation_run"])))
    evaluations = list(db.scalars(select(EvaluationRun).where(or_(*eval_filters))).all()) if eval_filters else []

    verification_filters = [Verification.claim_id == claim.id]
    if by_type["verification"]:
        verification_filters.append(Verification.id.in_(sorted(by_type["verification"])))
    verifications = list(db.scalars(select(Verification).where(or_(*verification_filters))).all())

    agent_run_ids = set(by_type["agent_run"])
    if claim.extractor_agent_run_id:
        agent_run_ids.add(claim.extractor_agent_run_id)
    agent_run_ids |= {e.created_by_agent_run_id for e in experiments.values() if e.created_by_agent_run_id}
    agent_run_ids |= {v.created_by_agent_run_id for v in versions.values() if v.created_by_agent_run_id}
    agent_run_ids |= {c.created_by_agent_run_id for c in code_snapshots.values() if c.created_by_agent_run_id}
    agent_runs = get_many(db, AgentRun, agent_run_ids, org)

    # ---- records ---------------------------------------------------------------------------------
    run_artifacts: dict[uuid.UUID, list[str]] = defaultdict(list)
    for version_id, (_, run_id) in artifact_rows.items():
        if run_id is not None:
            run_artifacts[run_id].append(str(version_id))
    provenance = [
        ModelProvenanceRecord(
            agent_run_id=str(run.id),
            role=run.role,
            agent_version_id=_opt(run.agent_version_id),
            provider=run.provider,
            model=run.model,
            model_version=run.model_version,
            prompt_key=run.prompt_key,
            prompt_version=str(run.prompt_version) if run.prompt_version is not None else None,
            prompt_hash=run.prompt_hash,
        )
        for run in agent_runs.values()
        if run.provider and run.model
    ]
    kept_runs = {str(rid) for rid in runs}
    required = list(DEFAULT_REQUIRED_CHAIN)
    not_applicable: list[str] = []
    if not agent_run_ids:
        not_applicable.append("ModelVersion")
    if versions and not any(_declares_datasets(v) for v in versions.values()):
        not_applicable.append("DatasetVersion")
    required = [t for t in required if t not in not_applicable]

    graph = build_lineage(
        ClaimRecord(
            id=str(claim.id),
            statement=claim.statement,
            status=claim.status,
            metric=claim.metric,
            mission_id=_opt(claim.mission_id),
            extractor_agent_run_id=_opt(claim.extractor_agent_run_id),
        ),
        evidence=[
            ClaimEvidenceRecord(
                id=str(link.id),
                claim_id=str(link.claim_id),
                evidence_type=link.evidence_type,
                ref_id=str(link.ref_id),
                relation=link.relation,  # type: ignore[arg-type]
                weight=link.weight,
                evidence_id=_opt(link.evidence_id),
            )
            for link in links
            # experiment versions and environments are linked through runs/versions in the graph itself
            if link.evidence_type in EVIDENCE_TYPE_NODES
        ],
        experiments=[
            ExperimentRecord(
                id=str(e.id),
                title=e.title,
                kind=e.kind,
                hypothesis_id=_opt(e.hypothesis_id),
                created_by_agent_run_id=_opt(e.created_by_agent_run_id),
            )
            for e in experiments.values()
        ],
        experiment_versions=[
            ExperimentVersionRecord(
                id=str(v.id),
                experiment_id=str(v.experiment_id),
                version=v.version,
                spec_hash=v.spec_hash,
                code_snapshot_id=_opt(v.code_snapshot_id),
                environment_id=_opt(v.environment_id),
                dataset_version_ids=[str(d) for d in (v.dataset_version_ids or [])],
                created_by_agent_run_id=_opt(v.created_by_agent_run_id),
            )
            for v in versions.values()
        ],
        runs=[
            ExperimentRunRecord(
                id=str(r.id),
                experiment_version_id=str(r.experiment_version_id),
                experiment_id=str(r.experiment_id),
                role=r.role,
                seed=r.seed,
                status=r.status,
                artifact_version_ids=sorted(run_artifacts.get(r.id, [])),
                reproduction_of_run_id=_opt(r.reproduction_of_run_id),
            )
            for r in runs.values()
        ],
        code_snapshots=[
            CodeSnapshotRecord(
                id=str(c.id),
                content_hash=c.content_hash,
                source=c.source,
                git_commit=c.git_commit,
                entrypoint=c.entrypoint,
                created_by_agent_run_id=_opt(c.created_by_agent_run_id),
            )
            for c in code_snapshots.values()
        ],
        dataset_versions=[
            DatasetVersionRecord(
                id=str(d.id),
                dataset_id=str(d.dataset_id),
                version=d.version,
                content_hash=d.checksum,
                parent_version_id=_opt(d.parent_version_id),
            )
            for d in dataset_versions.values()
        ],
        environments=[
            EnvironmentRecord(id=str(e.id), image=e.image, image_digest=e.image_digest, lockfile_hash=e.lockfile_hash)
            for e in environments.values()
        ],
        model_provenance=provenance,
        artifacts=[
            ArtifactRecord(
                id=str(version.id),
                name=artifact_names.get(version.artifact_id, (None, None))[0],
                kind=artifact_names.get(version.artifact_id, (None, None))[1],
                sha256=version.checksum,
                experiment_run_id=_opt(run_id),
            )
            for version, run_id in artifact_rows.values()
        ],
        evaluations=[
            EvaluationRecord(
                id=str(ev.id),
                experiment_run_id=_opt(ev.experiment_run_id),
                evaluator_key=ev.evaluator_key,
                evaluator_version=ev.evaluator_version,
                status=ev.status,
                passed=ev.passed,
            )
            for ev in evaluations
        ],
        verifications=[
            VerificationRecord(
                id=str(v.id), claim_id=str(v.claim_id), status=v.status, verdict=v.verdict, confidence=v.confidence
            )
            for v in verifications
        ],
        reproductions=[
            ReproductionRecord(
                id=str(r.id),
                experiment_id=str(r.experiment_id),
                original_run_ids=[str(x) for x in (r.original_run_ids or [])],
                reproduction_run_ids=[str(x) for x in (r.reproduction_run_ids or []) if str(x) in kept_runs],
                verdict=r.verdict,
                status=r.status,
            )
            for r in reproductions.values()
        ],
        required_chain=required,
    )
    return ClaimLineage(
        graph=graph, not_applicable=not_applicable, agent_run_ids=sorted(str(a) for a in agent_run_ids)
    )


def claim_lineage(db: Session, actor: Actor, claim_id: uuid.UUID | str) -> ClaimLineage:
    """Machine-readable provenance of a claim the actor may read (``claim:read``)."""
    claim = load_claim(db, actor, claim_id, "claim:read")
    return build_claim_lineage(db, claim)


__all__ = ["LINEAGE_SCHEMA_VERSION", "ClaimLineage", "build_claim_lineage", "claim_lineage"]
