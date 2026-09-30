"""Claim lineage assembly: records → a machine-readable provenance graph with a completeness report.

The verification context loads the claim, its evidence links and every referenced record (experiments,
versions, runs, code snapshots, dataset versions, environments, agent-run model provenance, artifacts,
evaluations, verifications, reproductions) and passes them here as plain records. :func:`build_lineage`
links them into typed nodes and edges, reports which types of the required provenance chain are
reachable from the claim, and lists every missing link — references to records that were not supplied
and runs lacking a required dependency. Output ordering is deterministic and the graph carries a content
digest so it can be anchored in the evidence chain.
"""

from __future__ import annotations

import hashlib
import json
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

LINEAGE_SCHEMA_VERSION = "lineage-1.0"

DEFAULT_REQUIRED_CHAIN: tuple[str, ...] = (
    "Claim",
    "Experiment",
    "ExperimentRun",
    "CodeSnapshot",
    "DatasetVersion",
    "ModelVersion",
    "Environment",
    "RawArtifact",
    "Evaluation",
    "Verification",
)
NODE_TYPE_ORDER: tuple[str, ...] = (
    *DEFAULT_REQUIRED_CHAIN,
    "ExperimentVersion",
    "AgentRun",
    "PromptVersion",
    "Reproduction",
    "ExperimentComparison",
    "ResearchSource",
    "Memory",
)
#: Dependencies every experiment run should have (checked when the type is in the required chain).
RUN_DEPENDENCIES: tuple[str, ...] = ("CodeSnapshot", "DatasetVersion", "Environment", "RawArtifact", "Evaluation")

EVIDENCE_TYPE_NODES: dict[str, str] = {
    "experiment": "Experiment",
    "experiment_run": "ExperimentRun",
    "experiment_comparison": "ExperimentComparison",
    "artifact_version": "RawArtifact",
    "evaluation_run": "Evaluation",
    "dataset_version": "DatasetVersion",
    "research_source": "ResearchSource",
    "memory": "Memory",
    "reproduction": "Reproduction",
    "verification": "Verification",
    "code_snapshot": "CodeSnapshot",
    "agent_run": "AgentRun",
}
EVIDENCE_RELATIONS: dict[str, str] = {
    "supports": "supported_by",
    "contradicts": "contradicted_by",
    "context": "contextualized_by",
}


# =============================================================================================
# Input records
# =============================================================================================
def _to_str(value: Any) -> Any:
    return str(value) if isinstance(value, UUID | int) and not isinstance(value, bool) else value


#: Identifier field accepting ``str``, ``UUID`` or ``int`` values (ORM rows pass UUIDs).
Ref = Annotated[str, BeforeValidator(_to_str)]


class _Record(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ClaimRecord(_Record):
    id: Ref
    statement: str = ""
    status: str | None = None
    metric: str | None = None
    mission_id: Ref | None = None
    extractor_agent_run_id: Ref | None = None


class ClaimEvidenceRecord(_Record):
    id: Ref | None = None
    claim_id: Ref
    evidence_type: str
    ref_id: Ref
    relation: Literal["supports", "contradicts", "context"] = "supports"
    weight: float = 1.0
    evidence_id: Ref | None = None


class ExperimentRecord(_Record):
    id: Ref
    title: str = ""
    kind: str | None = None
    hypothesis_id: Ref | None = None
    created_by_agent_run_id: Ref | None = None


class ExperimentVersionRecord(_Record):
    id: Ref
    experiment_id: Ref
    version: int = 1
    spec_hash: str | None = None
    code_snapshot_id: Ref | None = None
    environment_id: Ref | None = None
    dataset_version_ids: list[Ref] = Field(default_factory=list)
    created_by_agent_run_id: Ref | None = None


class ExperimentRunRecord(_Record):
    id: Ref
    experiment_version_id: Ref | None = None
    experiment_id: Ref | None = None
    role: str | None = None
    seed: int | None = None
    status: str | None = None
    code_snapshot_id: Ref | None = None
    environment_id: Ref | None = None
    dataset_version_ids: list[Ref] = Field(default_factory=list)
    artifact_version_ids: list[Ref] = Field(default_factory=list)
    agent_run_id: Ref | None = None
    reproduction_of_run_id: Ref | None = None


class CodeSnapshotRecord(_Record):
    id: Ref
    content_hash: str | None = None
    source: str | None = None
    git_commit: str | None = None
    entrypoint: str | None = None
    created_by_agent_run_id: Ref | None = None


class DatasetVersionRecord(_Record):
    id: Ref
    dataset_id: Ref | None = None
    version: int | None = None
    content_hash: str | None = None
    parent_version_id: Ref | None = None


class EnvironmentRecord(_Record):
    id: Ref
    image: str | None = None
    image_digest: str | None = None
    lockfile_hash: str | None = None


class ModelProvenanceRecord(_Record):
    agent_run_id: Ref
    role: str | None = None
    agent_version_id: Ref | None = None
    provider: str
    model: str
    model_version: str | None = None
    prompt_key: str | None = None
    prompt_version: Ref | None = None
    prompt_hash: str | None = None


class ArtifactRecord(_Record):
    id: Ref
    name: str | None = None
    kind: str | None = None
    sha256: str | None = None
    experiment_run_id: Ref | None = None


class EvaluationRecord(_Record):
    id: Ref
    experiment_run_id: Ref | None = None
    evaluator_key: str | None = None
    evaluator_version: str | None = None
    status: str | None = None
    passed: bool | None = None


class VerificationRecord(_Record):
    id: Ref
    claim_id: Ref
    status: str | None = None
    verdict: str | None = None
    confidence: float | None = None


class ReproductionRecord(_Record):
    id: Ref
    experiment_id: Ref | None = None
    original_run_ids: list[Ref] = Field(default_factory=list)
    reproduction_run_ids: list[Ref] = Field(default_factory=list)
    verdict: str | None = None
    status: str | None = None


# =============================================================================================
# Output
# =============================================================================================
class LineageNode(BaseModel):
    id: str
    type: str
    label: str
    attrs: dict[str, Any] = Field(default_factory=dict)


class LineageEdge(BaseModel):
    source: str
    target: str
    relation: str


class MissingLink(BaseModel):
    node: str
    relation: str
    target_type: str
    target_id: str | None = None
    reason: Literal["not_provided", "absent"]


class LineageCompleteness(BaseModel):
    required_chain: list[str]
    present: list[str]
    missing: list[str]
    missing_links: list[MissingLink]
    complete: bool


class LineageGraph(BaseModel):
    schema_version: str = LINEAGE_SCHEMA_VERSION
    claim_id: str
    nodes: list[LineageNode]
    edges: list[LineageEdge]
    completeness: LineageCompleteness
    digest: str


# =============================================================================================
# Builder
# =============================================================================================
def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal | UUID):
        return str(value)
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_json_safe(v) for v in value]
    return value


def _attrs(record: BaseModel, *exclude: str) -> dict[str, Any]:
    data = record.model_dump(exclude={"id", *exclude})
    return {key: _json_safe(value) for key, value in data.items() if value not in (None, [], {}, "")}


def _short(text: str | None, limit: int = 120) -> str:
    if not text:
        return ""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def node_id(node_type: str, key: str) -> str:
    return f"{node_type}:{key}"


class _Graph:
    def __init__(self) -> None:
        self.nodes: dict[str, LineageNode] = {}
        self.edges: set[tuple[str, str, str]] = set()
        self.missing: dict[tuple[str, str, str, str | None, str], MissingLink] = {}

    def add_node(self, node_type: str, key: str, label: str, attrs: dict[str, Any] | None = None) -> str:
        nid = node_id(node_type, key)
        existing = self.nodes.get(nid)
        if existing is None:
            self.nodes[nid] = LineageNode(id=nid, type=node_type, label=label or nid, attrs=attrs or {})
        elif attrs:
            merged = {**attrs, **existing.attrs}
            self.nodes[nid] = LineageNode(id=nid, type=node_type, label=existing.label, attrs=merged)
        return nid

    def add_edge(self, source: str, relation: str, target: str) -> None:
        if source != target:
            self.edges.add((source, relation, target))

    def add_missing(
        self,
        node: str,
        relation: str,
        target_type: str,
        target_id: str | None,
        reason: Literal["not_provided", "absent"],
    ) -> None:
        key = (node, relation, target_type, target_id, reason)
        self.missing.setdefault(
            key,
            MissingLink(node=node, relation=relation, target_type=target_type, target_id=target_id, reason=reason),
        )

    def neighbors(self) -> dict[str, set[str]]:
        adjacency: dict[str, set[str]] = {nid: set() for nid in self.nodes}
        for source, _, target in self.edges:
            adjacency.setdefault(source, set()).add(target)
            adjacency.setdefault(target, set()).add(source)
        return adjacency


def _index[T: BaseModel](records: Iterable[T | Mapping[str, Any]], model: type[T], key: str = "id") -> dict[str, T]:
    out: dict[str, T] = {}
    for record in records:
        parsed = record if isinstance(record, model) else model.model_validate(record)
        out[str(getattr(parsed, key))] = parsed
    return out


def _type_rank(node_type: str) -> int:
    return NODE_TYPE_ORDER.index(node_type) if node_type in NODE_TYPE_ORDER else len(NODE_TYPE_ORDER)


def build_lineage(
    claim: ClaimRecord | Mapping[str, Any],
    *,
    evidence: Iterable[ClaimEvidenceRecord | Mapping[str, Any]] = (),
    experiments: Iterable[ExperimentRecord | Mapping[str, Any]] = (),
    experiment_versions: Iterable[ExperimentVersionRecord | Mapping[str, Any]] = (),
    runs: Iterable[ExperimentRunRecord | Mapping[str, Any]] = (),
    code_snapshots: Iterable[CodeSnapshotRecord | Mapping[str, Any]] = (),
    dataset_versions: Iterable[DatasetVersionRecord | Mapping[str, Any]] = (),
    environments: Iterable[EnvironmentRecord | Mapping[str, Any]] = (),
    model_provenance: Iterable[ModelProvenanceRecord | Mapping[str, Any]] = (),
    artifacts: Iterable[ArtifactRecord | Mapping[str, Any]] = (),
    evaluations: Iterable[EvaluationRecord | Mapping[str, Any]] = (),
    verifications: Iterable[VerificationRecord | Mapping[str, Any]] = (),
    reproductions: Iterable[ReproductionRecord | Mapping[str, Any]] = (),
    required_chain: Sequence[str] = DEFAULT_REQUIRED_CHAIN,
) -> LineageGraph:
    """Assemble the provenance graph of ``claim`` and report its completeness against ``required_chain``."""
    claim_rec = claim if isinstance(claim, ClaimRecord) else ClaimRecord.model_validate(claim)
    exp_by_id = _index(experiments, ExperimentRecord)
    ver_by_id = _index(experiment_versions, ExperimentVersionRecord)
    run_by_id = _index(runs, ExperimentRunRecord)
    code_by_id = _index(code_snapshots, CodeSnapshotRecord)
    data_by_id = _index(dataset_versions, DatasetVersionRecord)
    env_by_id = _index(environments, EnvironmentRecord)
    prov_by_run = _index(model_provenance, ModelProvenanceRecord, key="agent_run_id")
    art_by_id = _index(artifacts, ArtifactRecord)
    eval_by_id = _index(evaluations, EvaluationRecord)
    verif_by_id = _index(verifications, VerificationRecord)
    repro_by_id = _index(reproductions, ReproductionRecord)
    registries: dict[str, Mapping[str, Any]] = {
        "Experiment": exp_by_id,
        "ExperimentVersion": ver_by_id,
        "ExperimentRun": run_by_id,
        "CodeSnapshot": code_by_id,
        "DatasetVersion": data_by_id,
        "Environment": env_by_id,
        "RawArtifact": art_by_id,
        "Evaluation": eval_by_id,
        "Verification": verif_by_id,
        "Reproduction": repro_by_id,
    }

    g = _Graph()
    claim_node = g.add_node("Claim", claim_rec.id, _short(claim_rec.statement) or "claim", _attrs(claim_rec))

    # ---- nodes for every supplied record ------------------------------------------------------
    for exp in exp_by_id.values():
        g.add_node("Experiment", exp.id, _short(exp.title) or f"experiment {exp.id[:8]}", _attrs(exp))
    for ver in ver_by_id.values():
        g.add_node("ExperimentVersion", ver.id, f"version {ver.version}", _attrs(ver))
    for run in run_by_id.values():
        seed = f" (seed {run.seed})" if run.seed is not None else ""
        g.add_node("ExperimentRun", run.id, f"run {run.id[:8]}{seed}", _attrs(run))
    for code in code_by_id.values():
        label = f"code {code.content_hash[:12]}" if code.content_hash else f"code {code.id[:8]}"
        g.add_node("CodeSnapshot", code.id, label, _attrs(code))
    for data in data_by_id.values():
        label = f"dataset version {data.version}" if data.version is not None else f"dataset {data.id[:8]}"
        g.add_node("DatasetVersion", data.id, label, _attrs(data))
    for env in env_by_id.values():
        label = env.image or f"environment {env.id[:8]}"
        if env.image_digest:
            label += f"@{env.image_digest[:19]}"
        g.add_node("Environment", env.id, label, _attrs(env))
    for art in art_by_id.values():
        g.add_node("RawArtifact", art.id, art.name or art.kind or f"artifact {art.id[:8]}", _attrs(art))
    for ev in eval_by_id.values():
        label = (
            f"{ev.evaluator_key or 'evaluation'}@{ev.evaluator_version}"
            if ev.evaluator_version
            else (ev.evaluator_key or f"evaluation {ev.id[:8]}")
        )
        g.add_node("Evaluation", ev.id, label, _attrs(ev))
    for repro in repro_by_id.values():
        g.add_node(
            "Reproduction", repro.id, f"reproduction ({repro.verdict or repro.status or 'pending'})", _attrs(repro)
        )
    for verif in verif_by_id.values():
        g.add_node(
            "Verification", verif.id, f"verification ({verif.verdict or verif.status or 'pending'})", _attrs(verif)
        )

    def agent_run_node(agent_run_id: str) -> str:
        prov = prov_by_run.get(agent_run_id)
        if prov is None:
            nid = g.add_node("AgentRun", agent_run_id, f"agent run {agent_run_id[:8]}", {"model_provenance": "missing"})
            g.add_missing(nid, "used_model", "ModelVersion", None, "not_provided")
            return nid
        nid = g.add_node(
            "AgentRun",
            agent_run_id,
            f"{prov.role or 'agent'} run {agent_run_id[:8]}",
            _attrs(
                prov,
                "agent_run_id",
                "provider",
                "model",
                "model_version",
                "prompt_key",
                "prompt_version",
                "prompt_hash",
            ),
        )
        version = prov.model_version or "unversioned"
        model_node = g.add_node(
            "ModelVersion",
            f"{prov.provider}/{prov.model}@{version}",
            f"{prov.provider}/{prov.model}@{version}",
            {"provider": prov.provider, "model": prov.model, "model_version": prov.model_version},
        )
        g.add_edge(nid, "used_model", model_node)
        if prov.prompt_key:
            prompt_node = g.add_node(
                "PromptVersion",
                f"{prov.prompt_key}@{prov.prompt_version or 'latest'}",
                f"{prov.prompt_key}@v{prov.prompt_version or '?'}",
                {"prompt_key": prov.prompt_key, "prompt_version": prov.prompt_version, "prompt_hash": prov.prompt_hash},
            )
            g.add_edge(nid, "used_prompt", prompt_node)
        return nid

    for agent_run_id in sorted(prov_by_run):
        agent_run_node(agent_run_id)

    def link(source: str, relation: str, target_type: str, target_id: str | None) -> None:
        if not target_id:
            return
        if target_type == "AgentRun":
            g.add_edge(source, relation, agent_run_node(target_id))
            return
        if target_id in registries[target_type]:
            g.add_edge(source, relation, node_id(target_type, target_id))
        else:
            g.add_missing(source, relation, target_type, target_id, "not_provided")

    # ---- claim ---------------------------------------------------------------------------------
    link(claim_node, "extracted_by", "AgentRun", claim_rec.extractor_agent_run_id)
    for row in sorted(
        (r if isinstance(r, ClaimEvidenceRecord) else ClaimEvidenceRecord.model_validate(r) for r in evidence),
        key=lambda r: (r.evidence_type, r.ref_id, r.relation),
    ):
        if row.claim_id != claim_rec.id:
            continue
        relation = EVIDENCE_RELATIONS[row.relation]
        target_type = EVIDENCE_TYPE_NODES.get(row.evidence_type)
        if target_type is None:
            g.add_missing(claim_node, relation, row.evidence_type, row.ref_id, "not_provided")
        elif target_type in registries or target_type == "AgentRun":
            link(claim_node, relation, target_type, row.ref_id)
        else:
            attrs = {"evidence_type": row.evidence_type, "weight": row.weight}
            if row.evidence_id:
                attrs["evidence_id"] = row.evidence_id
            target = g.add_node(target_type, row.ref_id, f"{row.evidence_type} {row.ref_id[:8]}", attrs)
            g.add_edge(claim_node, relation, target)
    for verif in verif_by_id.values():
        if verif.claim_id == claim_rec.id:
            g.add_edge(claim_node, "verified_by", node_id("Verification", verif.id))

    # ---- experiments / versions -----------------------------------------------------------------
    for exp in exp_by_id.values():
        link(node_id("Experiment", exp.id), "designed_by", "AgentRun", exp.created_by_agent_run_id)
    for ver in ver_by_id.values():
        vnode = node_id("ExperimentVersion", ver.id)
        if ver.experiment_id in exp_by_id:
            g.add_edge(node_id("Experiment", ver.experiment_id), "has_version", vnode)
        else:
            g.add_missing(vnode, "version_of", "Experiment", ver.experiment_id, "not_provided")
        link(vnode, "uses_code", "CodeSnapshot", ver.code_snapshot_id)
        link(vnode, "runs_in", "Environment", ver.environment_id)
        for dataset_id in ver.dataset_version_ids:
            link(vnode, "uses_dataset", "DatasetVersion", dataset_id)
        link(vnode, "designed_by", "AgentRun", ver.created_by_agent_run_id)

    # ---- runs -------------------------------------------------------------------------------------
    for run in run_by_id.values():
        rnode = node_id("ExperimentRun", run.id)
        version = ver_by_id.get(run.experiment_version_id or "")
        if version is not None:
            g.add_edge(node_id("ExperimentVersion", version.id), "has_run", rnode)
        elif run.experiment_version_id:
            g.add_missing(rnode, "run_of", "ExperimentVersion", run.experiment_version_id, "not_provided")
        if version is None and run.experiment_id:
            link(rnode, "run_of", "Experiment", run.experiment_id)
        link(
            rnode,
            "executed_code",
            "CodeSnapshot",
            run.code_snapshot_id or (version.code_snapshot_id if version else None),
        )
        link(rnode, "ran_in", "Environment", run.environment_id or (version.environment_id if version else None))
        for dataset_id in run.dataset_version_ids or (version.dataset_version_ids if version else []):
            link(rnode, "used_dataset", "DatasetVersion", dataset_id)
        for artifact_id in run.artifact_version_ids:
            link(rnode, "produced", "RawArtifact", artifact_id)
        link(rnode, "launched_by", "AgentRun", run.agent_run_id)
        link(rnode, "reproduction_of", "ExperimentRun", run.reproduction_of_run_id)

    for art in art_by_id.values():
        if art.experiment_run_id:
            if art.experiment_run_id in run_by_id:
                g.add_edge(node_id("ExperimentRun", art.experiment_run_id), "produced", node_id("RawArtifact", art.id))
            else:
                g.add_missing(
                    node_id("RawArtifact", art.id),
                    "produced_by",
                    "ExperimentRun",
                    art.experiment_run_id,
                    "not_provided",
                )
    for ev in eval_by_id.values():
        if ev.experiment_run_id:
            if ev.experiment_run_id in run_by_id:
                g.add_edge(node_id("ExperimentRun", ev.experiment_run_id), "evaluated_by", node_id("Evaluation", ev.id))
            else:
                g.add_missing(
                    node_id("Evaluation", ev.id), "evaluates", "ExperimentRun", ev.experiment_run_id, "not_provided"
                )
    for code in code_by_id.values():
        link(node_id("CodeSnapshot", code.id), "authored_by", "AgentRun", code.created_by_agent_run_id)
    for data in data_by_id.values():
        if data.parent_version_id and data.parent_version_id in data_by_id:
            g.add_edge(
                node_id("DatasetVersion", data.id), "derived_from", node_id("DatasetVersion", data.parent_version_id)
            )
    for repro in repro_by_id.values():
        pnode = node_id("Reproduction", repro.id)
        link(pnode, "reproduction_of_experiment", "Experiment", repro.experiment_id)
        for run_id in repro.original_run_ids:
            link(pnode, "reproduces", "ExperimentRun", run_id)
        for run_id in repro.reproduction_run_ids:
            link(pnode, "reproduction_run", "ExperimentRun", run_id)

    # ---- completeness -----------------------------------------------------------------------------
    adjacency = g.neighbors()
    reachable: set[str] = set()
    queue: deque[str] = deque([claim_node])
    while queue:
        current = queue.popleft()
        if current in reachable:
            continue
        reachable.add(current)
        queue.extend(sorted(adjacency.get(current, set()) - reachable))
    reachable_types = {g.nodes[nid].type for nid in reachable}
    chain = list(dict.fromkeys(required_chain))
    required = set(chain)
    for nid in sorted(reachable):
        node = g.nodes[nid]
        if node.type != "ExperimentRun":
            continue
        neighbor_types = {g.nodes[other].type for other in adjacency.get(nid, set())}
        for dep in RUN_DEPENDENCIES:
            if dep in required and dep not in neighbor_types:
                g.add_missing(nid, "requires", dep, None, "absent")
    present = [t for t in chain if t in reachable_types]
    missing = [t for t in chain if t not in reachable_types]
    missing_links = sorted(
        g.missing.values(), key=lambda m: (m.node, m.relation, m.target_type, m.target_id or "", m.reason)
    )
    if "ModelVersion" not in required:
        missing_links = [m for m in missing_links if m.target_type != "ModelVersion"]

    nodes = sorted(g.nodes.values(), key=lambda n: (_type_rank(n.type), n.id))
    edges = [LineageEdge(source=s, relation=r, target=t) for s, r, t in sorted(g.edges)]
    completeness = LineageCompleteness(
        required_chain=chain,
        present=present,
        missing=missing,
        missing_links=missing_links,
        complete=not missing and not missing_links,
    )
    body = {
        "schema_version": LINEAGE_SCHEMA_VERSION,
        "claim_id": claim_rec.id,
        "nodes": [n.model_dump() for n in nodes],
        "edges": [e.model_dump() for e in edges],
        "completeness": completeness.model_dump(),
    }
    digest = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()
    ).hexdigest()
    return LineageGraph(claim_id=claim_rec.id, nodes=nodes, edges=edges, completeness=completeness, digest=digest)
