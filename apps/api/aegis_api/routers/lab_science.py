"""Research, knowledge, memory, graph, hypotheses, experiments, runs, datasets, artifacts and environments."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.deps import get_db, rate_limited, require
from aegis_api.errors import NotFound, PayloadTooLarge, ValidationFailed
from aegis_api.idempotency import Idempotency, idempotency
from aegis_api.models.lab import (
    Dataset,
    Document,
    Experiment,
    ExperimentRun,
    ExperimentVersion,
    GraphNode,
    Hypothesis,
    Memory,
    Mission,
    ResearchTask,
)
from aegis_api.routers._helpers import paginate
from aegis_api.schemas.common import Page, PageParams
from aegis_api.schemas.lab import (
    Accepted,
    ArtifactOut,
    ArtifactVersionOut,
    CodeBundleIn,
    DatasetIn,
    DatasetOut,
    DatasetVersionOut,
    DocumentOut,
    EnvironmentIn,
    EvaluationRunOut,
    ExperimentIn,
    ExperimentOut,
    ExperimentRunOut,
    ExperimentVersionIn,
    ExperimentVersionOut,
    HypothesisEvidenceIn,
    HypothesisIn,
    HypothesisOut,
    MemoryIn,
    MemoryOut,
    MemoryReviewIn,
    MemorySupersedeIn,
    ResearchEventOut,
    ResearchIn,
    ResearchTaskOut,
    RunComparisonOut,
    SearchIn,
    SourceOut,
    TransitionIn,
    UrlIngestIn,
)
from aegis_api.security.context import Principal
from aegis_api.services.lab import (
    artifacts,
    datasets,
    environments,
    evaluation,
    graph,
    hypotheses,
    knowledge,
    reproducibility,
    research,
)
from aegis_api.services.lab import experiments as experiment_service
from aegis_api.services.lab import memory as memory_service
from aegis_api.services.lab.access import accessible_project_ids, get_project, get_scoped, scoped_ref
from aegis_api.services.lab.common import Actor, parse_uuid
from aegis_api.services.lab.reproducibility import manifest_report
from aegis_api.workflows import client as workflow_client

router = APIRouter(prefix="/api/v1", tags=["Science"])


def _accept(idem: Idempotency, db: Session, body: Accepted) -> dict[str, Any]:
    payload = body.model_dump()
    idem.complete(db, 202, payload)
    return payload


# --- research ---------------------------------------------------------------------------------------------------


@router.post("/research", response_model=Accepted, status_code=202)
def create_research(
    body: ResearchIn,
    idem: Idempotency = Depends(idempotency),
    principal: Principal = Depends(require("research:run")),
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limited("research")),
) -> Any:
    if idem.replay_response is not None:
        return idem.replay_response
    task = research.create(
        db,
        principal,
        project_id=body.project_id,
        title=body.title,
        question=body.question,
        mode=body.mode,
        mission_id=parse_uuid(body.mission_id, "Mission") if body.mission_id else None,
        require_plan_approval=body.require_plan_approval,
    )
    return _accept(idem, db, Accepted(id=str(task.id), status=task.status, workflow_run_id=str(task.workflow_run_id)))


@router.get("/research", response_model=Page[ResearchTaskOut])
def list_research(
    params: PageParams = Depends(),
    project_id: uuid.UUID | None = Query(default=None),
    mission_id: uuid.UUID | None = Query(default=None),
    principal: Principal = Depends(require("research:read")),
    db: Session = Depends(get_db),
) -> Page[ResearchTaskOut]:
    stmt = research.list_tasks(db, principal, project_id=project_id, mission_id=mission_id)
    return paginate(db, stmt, params, ResearchTaskOut.model_validate)


@router.get("/research/{task_id}", response_model=ResearchTaskOut)
def get_research(
    task_id: uuid.UUID, principal: Principal = Depends(require("research:read")), db: Session = Depends(get_db)
) -> ResearchTaskOut:
    return ResearchTaskOut.model_validate(get_scoped(db, principal, ResearchTask, task_id, label="Research task"))


@router.get("/research/{task_id}/events", response_model=list[ResearchEventOut])
def research_events(
    task_id: uuid.UUID,
    after: int = Query(default=0, ge=0),
    principal: Principal = Depends(require("research:read")),
    db: Session = Depends(get_db),
) -> list[ResearchEventOut]:
    task = get_scoped(db, principal, ResearchTask, task_id, label="Research task")
    return [ResearchEventOut.model_validate(e) for e in research.task_events(db, task, after)]


@router.get("/research/{task_id}/report")
def research_report(
    task_id: uuid.UUID, principal: Principal = Depends(require("research:read")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    task = get_scoped(db, principal, ResearchTask, task_id, label="Research task")
    return {
        "id": str(task.id),
        "status": task.status,
        "report": task.report,
        "report_artifact_id": str(task.report_artifact_id) if task.report_artifact_id else None,
        "citations": research.source_count(db, task),
        "note": "Research output is untrusted third-party content; verify claims against the cited sources.",
    }


@router.post("/research/{task_id}/cancel", response_model=Accepted)
def cancel_research(
    task_id: uuid.UUID, principal: Principal = Depends(require("research:run")), db: Session = Depends(get_db)
) -> Accepted:
    task = get_scoped(db, principal, ResearchTask, task_id, label="Research task")
    if task.workflow_run_id:
        workflow_client.cancel(db, task.workflow_run_id)
    return Accepted(
        id=str(task.id),
        status="cancelling",
        workflow_run_id=str(task.workflow_run_id) if task.workflow_run_id else None,
    )


@router.get("/papers", response_model=Page[SourceOut])
def list_sources(
    params: PageParams = Depends(),
    q: str | None = Query(default=None, max_length=200),
    project_id: uuid.UUID | None = Query(default=None),
    principal: Principal = Depends(require("knowledge:read")),
    db: Session = Depends(get_db),
) -> Page[SourceOut]:
    stmt = knowledge.search_sources(
        db, principal.organization_id, q, project_ids=accessible_project_ids(db, principal), project_id=project_id
    )
    return paginate(db, stmt, params, SourceOut.model_validate)


# --- documents / knowledge ---------------------------------------------------------------------------------------


@router.post("/knowledge/documents", response_model=Accepted, status_code=202)
async def upload_document(
    project_id: uuid.UUID = Form(...),
    title: str | None = Form(default=None),
    file: UploadFile = File(...),
    principal: Principal = Depends(require("knowledge:ingest")),
    db: Session = Depends(get_db),
) -> Accepted:
    limit = get_settings().max_upload_bytes
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise PayloadTooLarge(f"Document exceeds {limit} bytes")
    project = get_project(db, principal, project_id)
    db.commit()  # no transaction held across the object-storage write
    version = knowledge.store_document(
        db,
        principal,
        project,
        filename=file.filename or "upload",
        data=data,
        declared_type=file.content_type,
        title=title,
    )
    run = workflow_client.start(
        db,
        organization_id=principal.organization_id,
        workflow="artifact_processing",
        business_key=f"document-version:{version.id}",
        payload={"document_version_id": str(version.id)},
        principal=principal,
    )
    return Accepted(id=str(version.document_id), status="processing", workflow_run_id=str(run.id))


@router.post("/knowledge/urls", response_model=Accepted, status_code=202)
def ingest_url(
    body: UrlIngestIn, principal: Principal = Depends(require("knowledge:ingest")), db: Session = Depends(get_db)
) -> Accepted:
    project = get_project(db, principal, body.project_id)
    doc = knowledge.register_url(db, principal, project, url=body.url, title=body.title)
    run = workflow_client.start(
        db,
        organization_id=principal.organization_id,
        workflow="artifact_processing",
        business_key=f"document:{doc.id}",
        payload={"document_id": str(doc.id)},
        principal=principal,
    )
    return Accepted(id=str(doc.id), status=doc.status, workflow_run_id=str(run.id))


@router.get("/knowledge/documents", response_model=Page[DocumentOut])
def list_documents(
    params: PageParams = Depends(),
    project_id: uuid.UUID | None = Query(default=None),
    principal: Principal = Depends(require("knowledge:read")),
    db: Session = Depends(get_db),
) -> Page[DocumentOut]:
    stmt = select(Document).where(Document.organization_id == principal.organization_id)
    visible = accessible_project_ids(db, principal)
    if visible is not None:
        stmt = stmt.where(Document.project_id.in_(visible))
    if project_id:
        stmt = stmt.where(Document.project_id == project_id)
    return paginate(db, stmt.order_by(Document.created_at.desc()), params, DocumentOut.model_validate)


@router.post("/knowledge/search")
def search_knowledge(
    body: SearchIn, principal: Principal = Depends(require("knowledge:read")), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    vectors, model = knowledge.embed_texts(principal.organization_id, [body.query])
    return knowledge.search_chunks(
        db,
        principal.organization_id,
        body.query,
        project_ids=accessible_project_ids(db, principal),
        project_id=parse_uuid(body.project_id, "Project") if body.project_id else None,
        query_vector=vectors[0] if vectors else None,
        embedding_model=model,
        limit=body.limit,
    )


# --- memory -----------------------------------------------------------------------------------------------------


@router.get("/memory", response_model=Page[MemoryOut])
def list_memory(
    params: PageParams = Depends(),
    status: str | None = Query(default=None, max_length=16),
    scope: str | None = Query(default=None, max_length=16),
    project_id: uuid.UUID | None = Query(default=None),
    principal: Principal = Depends(require("memory:read")),
    db: Session = Depends(get_db),
) -> Page[MemoryOut]:
    stmt = memory_service.list_memories(
        db,
        principal.organization_id,
        project_ids=accessible_project_ids(db, principal),
        status=status,
        scope=scope,
        project_id=project_id,
    )
    return paginate(db, stmt, params, MemoryOut.model_validate)


@router.post("/memory/search")
def search_memory(
    body: SearchIn, principal: Principal = Depends(require("memory:read")), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    vectors, model = knowledge.embed_texts(principal.organization_id, [body.query])
    return memory_service.search(
        db,
        principal.organization_id,
        body.query,
        query_vector=vectors[0] if vectors else None,
        embedding_model=model,
        project_ids=accessible_project_ids(db, principal),
        project_id=parse_uuid(body.project_id, "Project") if body.project_id else None,
        mission_id=parse_uuid(body.mission_id, "Mission") if body.mission_id else None,
        scopes=body.scopes,
        categories=body.categories,
        limit=body.limit,
    )


@router.post("/memory", response_model=MemoryOut, status_code=201)
def propose_memory(
    body: MemoryIn, principal: Principal = Depends(require("memory:write")), db: Session = Depends(get_db)
) -> MemoryOut:
    project_id = parse_uuid(body.project_id, "Project") if body.project_id else None
    if project_id:
        get_project(db, principal, project_id)
    mission = scoped_ref(db, principal, Mission, body.mission_id, project_id=project_id, label="Mission")
    if mission is not None:
        project_id = mission.project_id
    vectors, model = knowledge.embed_texts(principal.organization_id, [body.content])
    mem = memory_service.propose(
        db,
        organization_id=principal.organization_id,
        actor=Actor.of(principal),
        scope=body.scope,
        category=body.category,
        content=body.content,
        source="user",
        source_ref=body.source_ref,
        title=body.title,
        project_id=project_id,
        mission_id=mission.id if mission else None,
        confidence=body.confidence,
        sensitivity=body.sensitivity,
        embedding=vectors[0] if vectors else None,
        embedding_model=model or None,
    )
    return MemoryOut.model_validate(mem)


@router.post("/memory/{memory_id}/review", response_model=MemoryOut)
def review_memory(
    memory_id: uuid.UUID,
    body: MemoryReviewIn,
    principal: Principal = Depends(require("memory:review")),
    db: Session = Depends(get_db),
) -> MemoryOut:
    return MemoryOut.model_validate(
        memory_service.review(db, principal, memory_id, approve=body.approve, reason=body.reason)
    )


@router.post("/memory/{memory_id}/supersede", response_model=MemoryOut)
def supersede_memory(
    memory_id: uuid.UUID,
    body: MemorySupersedeIn,
    principal: Principal = Depends(require("memory:write")),
    db: Session = Depends(get_db),
) -> MemoryOut:
    vectors, model = knowledge.embed_texts(principal.organization_id, [body.content])
    return MemoryOut.model_validate(
        memory_service.supersede(
            db,
            principal,
            memory_id,
            content=body.content,
            embedding=vectors[0] if vectors else None,
            embedding_model=model or None,
        )
    )


@router.get("/memory/{memory_id}", response_model=MemoryOut)
def get_memory(
    memory_id: uuid.UUID, principal: Principal = Depends(require("memory:read")), db: Session = Depends(get_db)
) -> MemoryOut:
    return MemoryOut.model_validate(get_scoped(db, principal, Memory, memory_id, label="Memory"))


# --- knowledge graph --------------------------------------------------------------------------------------------


@router.get("/graph/nodes")
def graph_nodes(
    q: str | None = Query(default=None, max_length=100),
    node_type: str | None = Query(default=None, max_length=24),
    ref_type: str | None = Query(default=None, max_length=24),
    ref_id: str | None = Query(default=None, max_length=64),
    principal: Principal = Depends(require("knowledge:read")),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    rows = graph.find_nodes(
        db,
        principal.organization_id,
        query=q,
        node_type=node_type,
        ref=(ref_type, ref_id) if ref_type and ref_id else None,
        project_ids=accessible_project_ids(db, principal),
    )
    return [graph.node_dict(n) for n in rows]


@router.get("/graph/nodes/{node_id}/neighborhood")
def graph_neighborhood(
    node_id: uuid.UUID,
    depth: int = Query(default=1, ge=1, le=3),
    relation: list[str] | None = Query(default=None),
    principal: Principal = Depends(require("knowledge:read")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    node = db.get(GraphNode, node_id)
    if node is None or node.organization_id != principal.organization_id:
        raise NotFound("Graph node not found")
    visible = accessible_project_ids(db, principal)
    if visible is not None and node.project_id is not None and node.project_id not in visible:
        raise NotFound("Graph node not found")
    return graph.neighborhood(
        db, principal.organization_id, node.id, depth=depth, relations=relation, project_ids=visible
    )


# --- hypotheses -------------------------------------------------------------------------------------------------


@router.get("/hypotheses", response_model=Page[HypothesisOut])
def list_hypotheses(
    params: PageParams = Depends(),
    project_id: uuid.UUID | None = Query(default=None),
    mission_id: uuid.UUID | None = Query(default=None),
    status: str | None = Query(default=None, max_length=24),
    principal: Principal = Depends(require("hypothesis:read")),
    db: Session = Depends(get_db),
) -> Page[HypothesisOut]:
    stmt = hypotheses.list_hypotheses(db, principal, project_id=project_id, mission_id=mission_id, status=status)
    return paginate(db, stmt, params, HypothesisOut.model_validate)


@router.post("/hypotheses", response_model=HypothesisOut, status_code=201)
def create_hypothesis(
    body: HypothesisIn, principal: Principal = Depends(require("hypothesis:write")), db: Session = Depends(get_db)
) -> HypothesisOut:
    data = body.model_dump()
    for key in ("mission_id", "parent_hypothesis_id"):
        data[key] = parse_uuid(data[key], key) if data.get(key) else None
    return HypothesisOut.model_validate(hypotheses.create(db, principal, data))


@router.get("/hypotheses/{hypothesis_id}")
def get_hypothesis(
    hypothesis_id: uuid.UUID, principal: Principal = Depends(require("hypothesis:read")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    h = get_scoped(db, principal, Hypothesis, hypothesis_id, label="Hypothesis")
    return {
        **HypothesisOut.model_validate(h).model_dump(),
        "evidence": [
            {"source_type": e.source_type, "source_id": e.source_id, "relation": e.relation, "note": e.note}
            for e in hypotheses.evidence_for(db, h)
        ],
    }


@router.post("/hypotheses/{hypothesis_id}/transition", response_model=HypothesisOut)
def transition_hypothesis(
    hypothesis_id: uuid.UUID,
    body: TransitionIn,
    principal: Principal = Depends(require("hypothesis:write")),
    db: Session = Depends(get_db),
) -> HypothesisOut:
    return HypothesisOut.model_validate(hypotheses.transition(db, principal, hypothesis_id, body.status, body.reason))


@router.post("/hypotheses/{hypothesis_id}/evidence", status_code=201)
def add_hypothesis_evidence(
    hypothesis_id: uuid.UUID,
    body: HypothesisEvidenceIn,
    principal: Principal = Depends(require("hypothesis:write")),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    row = hypotheses.add_evidence(
        db,
        principal,
        hypothesis_id,
        source_type=body.source_type,
        source_id=body.source_id,
        relation=body.relation,
        note=body.note,
    )
    db.flush()
    return {"id": str(row.id)}


# --- experiments ------------------------------------------------------------------------------------------------


@router.get("/experiments", response_model=Page[ExperimentOut])
def list_experiments(
    params: PageParams = Depends(),
    project_id: uuid.UUID | None = Query(default=None),
    mission_id: uuid.UUID | None = Query(default=None),
    status: str | None = Query(default=None, max_length=16),
    principal: Principal = Depends(require("experiment:read")),
    db: Session = Depends(get_db),
) -> Page[ExperimentOut]:
    stmt = experiment_service.list_experiments(
        db, principal, project_id=project_id, mission_id=mission_id, status=status
    )
    return paginate(db, stmt, params, ExperimentOut.model_validate)


@router.post("/experiments", response_model=ExperimentOut, status_code=201)
def create_experiment(
    body: ExperimentIn, principal: Principal = Depends(require("experiment:create")), db: Session = Depends(get_db)
) -> ExperimentOut:
    experiment, _ = experiment_service.create(db, principal, body.model_dump())
    return ExperimentOut.model_validate(experiment)


@router.post("/experiments/validate")
def validate_experiment_spec(
    spec: dict[str, Any], principal: Principal = Depends(require("experiment:read")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    _, report = experiment_service.validate_spec(db, principal.organization_id, spec)
    return report


@router.get("/experiments/{experiment_id}", response_model=ExperimentOut)
def get_experiment(
    experiment_id: uuid.UUID, principal: Principal = Depends(require("experiment:read")), db: Session = Depends(get_db)
) -> ExperimentOut:
    return ExperimentOut.model_validate(get_scoped(db, principal, Experiment, experiment_id, label="Experiment"))


@router.get("/experiments/{experiment_id}/versions", response_model=list[ExperimentVersionOut])
def experiment_versions(
    experiment_id: uuid.UUID, principal: Principal = Depends(require("experiment:read")), db: Session = Depends(get_db)
) -> list[ExperimentVersionOut]:
    experiment = get_scoped(db, principal, Experiment, experiment_id, label="Experiment")
    return [ExperimentVersionOut.model_validate(v) for v in experiment_service.versions(db, experiment)]


@router.post("/experiments/{experiment_id}/versions", response_model=ExperimentVersionOut, status_code=201)
def create_experiment_version(
    experiment_id: uuid.UUID,
    body: ExperimentVersionIn,
    principal: Principal = Depends(require("experiment:create")),
    db: Session = Depends(get_db),
) -> ExperimentVersionOut:
    return ExperimentVersionOut.model_validate(
        experiment_service.new_version(db, principal, experiment_id, body.spec, body.change_note)
    )


@router.post("/experiments/{experiment_id}/code", response_model=ExperimentVersionOut, status_code=201)
def attach_code(
    experiment_id: uuid.UUID,
    body: CodeBundleIn,
    principal: Principal = Depends(require("experiment:create")),
    db: Session = Depends(get_db),
) -> ExperimentVersionOut:
    get_scoped(db, principal, Experiment, experiment_id, label="Experiment")
    db.commit()
    result = experiment_service.attach_code(
        principal.organization_id,
        experiment_id=experiment_id,
        bundle=body.model_dump(),
        agent_run_id=None,
        actor=Actor.of(principal),
    )
    version = db.get(ExperimentVersion, uuid.UUID(result["version_id"]))
    assert version is not None
    return ExperimentVersionOut.model_validate(version)


@router.post("/experiments/{experiment_id}/execute", response_model=Accepted, status_code=202)
def execute_experiment(
    experiment_id: uuid.UUID,
    idem: Idempotency = Depends(idempotency),
    principal: Principal = Depends(require("experiment:execute")),
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limited("execution")),
) -> Any:
    """Run every planned run of the current version in the sandbox, then evaluate (ExperimentBatchWorkflow)."""
    if idem.replay_response is not None:
        return idem.replay_response
    experiment = get_scoped(db, principal, Experiment, experiment_id, label="Experiment")
    version = db.get(ExperimentVersion, experiment.current_version_id) if experiment.current_version_id else None
    if version is None or not (version.validation or {}).get("valid"):
        raise ValidationFailed("The current experiment version has not passed validation")
    if version.code_artifact_id is None:
        raise ValidationFailed("Attach code before executing")
    run = workflow_client.start(
        db,
        organization_id=principal.organization_id,
        workflow="experiment_batch",
        business_key=f"experiment-batch:{version.id}",
        payload={"experiment_id": str(experiment.id)},
        principal=principal,
    )
    return _accept(idem, db, Accepted(id=str(experiment.id), status="queued", workflow_run_id=str(run.id)))


@router.get("/experiments/{experiment_id}/runs", response_model=list[ExperimentRunOut])
def experiment_runs(
    experiment_id: uuid.UUID, principal: Principal = Depends(require("experiment:read")), db: Session = Depends(get_db)
) -> list[ExperimentRunOut]:
    experiment = get_scoped(db, principal, Experiment, experiment_id, label="Experiment")
    return [ExperimentRunOut.model_validate(r) for r in experiment_service.runs(db, experiment.id)]


@router.get("/experiments/{experiment_id}/evaluations", response_model=list[EvaluationRunOut])
def experiment_evaluations(
    experiment_id: uuid.UUID, principal: Principal = Depends(require("evaluation:read")), db: Session = Depends(get_db)
) -> list[EvaluationRunOut]:
    experiment = get_scoped(db, principal, Experiment, experiment_id, label="Experiment")
    return [EvaluationRunOut.model_validate(e) for e in evaluation.list_evaluations(db, experiment.id)]


@router.get("/experiments/{experiment_id}/comparisons", response_model=list[RunComparisonOut])
def experiment_comparisons(
    experiment_id: uuid.UUID, principal: Principal = Depends(require("evaluation:read")), db: Session = Depends(get_db)
) -> list[RunComparisonOut]:
    experiment = get_scoped(db, principal, Experiment, experiment_id, label="Experiment")
    return [RunComparisonOut.model_validate(c) for c in evaluation.comparisons_for(db, experiment.id)]


@router.get("/experiments/{experiment_id}/reproducibility-package")
def reproducibility_package(
    experiment_id: uuid.UUID,
    principal: Principal = Depends(require("artifact:download")),
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limited("download", per="user")),
) -> Response:
    filename, data = reproducibility.package(db, principal, experiment_id)
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "X-Content-Type-Options": "nosniff"},
    )


@router.get("/experiment-runs/{run_id}", response_model=ExperimentRunOut)
def get_run(
    run_id: uuid.UUID, principal: Principal = Depends(require("experiment:read")), db: Session = Depends(get_db)
) -> ExperimentRunOut:
    return ExperimentRunOut.model_validate(get_scoped(db, principal, ExperimentRun, run_id, label="Experiment run"))


@router.get("/experiment-runs/{run_id}/manifest")
def run_manifest(
    run_id: uuid.UUID, principal: Principal = Depends(require("experiment:read")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    return manifest_report(get_scoped(db, principal, ExperimentRun, run_id, label="Experiment run"))


@router.post("/experiment-runs/{run_id}/replay", response_model=Accepted, status_code=202)
def replay_run(
    run_id: uuid.UUID,
    idem: Idempotency = Depends(idempotency),
    principal: Principal = Depends(require("experiment:execute")),
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limited("execution")),
) -> Any:
    if idem.replay_response is not None:
        return idem.replay_response
    run, wf = reproducibility.replay(db, principal, run_id)
    return _accept(idem, db, Accepted(id=str(run.id), status=run.status, workflow_run_id=str(wf.id)))


# --- datasets -----------------------------------------------------------------------------------------------------


@router.get("/datasets", response_model=Page[DatasetOut])
def list_datasets(
    params: PageParams = Depends(),
    project_id: uuid.UUID | None = Query(default=None),
    principal: Principal = Depends(require("dataset:read")),
    db: Session = Depends(get_db),
) -> Page[DatasetOut]:
    return paginate(db, datasets.list_datasets(db, principal, project_id=project_id), params, DatasetOut.model_validate)


@router.post("/datasets", response_model=DatasetOut, status_code=201)
def create_dataset(
    body: DatasetIn, principal: Principal = Depends(require("dataset:write")), db: Session = Depends(get_db)
) -> DatasetOut:
    return DatasetOut.model_validate(
        datasets.create_dataset(
            db,
            principal,
            project_id=body.project_id,
            name=body.name,
            description=body.description,
            license=body.license,
            source=body.source,
        )
    )


@router.post("/datasets/{dataset_id}/versions", response_model=Accepted, status_code=202)
def upload_dataset_version(
    dataset_id: uuid.UUID,
    files: list[UploadFile] = File(...),
    roles: list[str] = Form(...),
    parent_version_id: uuid.UUID | None = Form(default=None),
    transformation_note: str | None = Form(default=None),
    principal: Principal = Depends(require("dataset:write")),
    db: Session = Depends(get_db),
) -> Accepted:
    """Multipart: repeat ``files`` and a parallel ``roles`` list (train|validation|test|harness_only)."""
    if len(files) != len(roles):
        raise ValidationFailed("Provide one role per file")
    dataset = get_scoped(db, principal, Dataset, dataset_id, label="Dataset")
    db.commit()
    staged = datasets.stage_files(
        principal,
        dataset,
        [
            (f.filename or f"file-{i}", roles[i], f.content_type or "application/octet-stream", f.file)
            for i, f in enumerate(files)
        ],
    )
    run = workflow_client.start(
        db,
        organization_id=principal.organization_id,
        workflow="dataset_processing",
        business_key=f"dataset:{dataset.id}:{datasets.version_checksum(staged)[:32]}",
        payload={
            "dataset_id": str(dataset.id),
            "files": staged,
            "parent_version_id": str(parent_version_id) if parent_version_id else None,
            "transformations": [{"op": "upload", "note": transformation_note}] if transformation_note else [],
        },
        principal=principal,
    )
    return Accepted(id=str(dataset.id), status="processing", workflow_run_id=str(run.id))


@router.get("/datasets/{dataset_id}/versions", response_model=list[DatasetVersionOut])
def dataset_versions(
    dataset_id: uuid.UUID, principal: Principal = Depends(require("dataset:read")), db: Session = Depends(get_db)
) -> list[DatasetVersionOut]:
    dataset = get_scoped(db, principal, Dataset, dataset_id, label="Dataset")
    return [DatasetVersionOut.model_validate(v) for v in datasets.versions(db, dataset)]


@router.get("/dataset-versions/{version_id}/lineage")
def dataset_lineage(
    version_id: uuid.UUID, principal: Principal = Depends(require("dataset:read")), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    return datasets.lineage(db, datasets.get_version(db, principal, version_id))


# --- artifacts ----------------------------------------------------------------------------------------------------


@router.get("/artifacts", response_model=Page[ArtifactOut])
def list_artifacts(
    params: PageParams = Depends(),
    project_id: uuid.UUID | None = Query(default=None),
    mission_id: uuid.UUID | None = Query(default=None),
    experiment_run_id: uuid.UUID | None = Query(default=None),
    kind: str | None = Query(default=None, max_length=32),
    principal: Principal = Depends(require("artifact:read")),
    db: Session = Depends(get_db),
) -> Page[ArtifactOut]:
    stmt = artifacts.list_artifacts(
        db, principal, project_id=project_id, mission_id=mission_id, experiment_run_id=experiment_run_id, kind=kind
    )
    return paginate(db, stmt, params, ArtifactOut.model_validate)


@router.post("/artifacts", response_model=ArtifactOut, status_code=201)
def upload_artifact(
    project_id: uuid.UUID = Form(...),
    kind: str = Form(default="upload", max_length=32),
    mission_id: uuid.UUID | None = Form(default=None),
    file: UploadFile = File(...),
    principal: Principal = Depends(require("artifact:upload")),
    db: Session = Depends(get_db),
) -> ArtifactOut:
    get_project(db, principal, project_id)
    db.commit()
    artifact, _ = artifacts.upload(
        db,
        principal,
        project_id=project_id,
        mission_id=mission_id,
        name=file.filename or "artifact",
        kind=kind,
        content_type=file.content_type or "application/octet-stream",
        stream=file.file,
    )
    return ArtifactOut.model_validate(artifact)


@router.get("/artifacts/{artifact_id}")
def get_artifact(
    artifact_id: uuid.UUID, principal: Principal = Depends(require("artifact:read")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    from aegis_api.models.lab import Artifact

    artifact = get_scoped(db, principal, Artifact, artifact_id, label="Artifact")
    return {
        **ArtifactOut.model_validate(artifact).model_dump(),
        "versions": [ArtifactVersionOut.model_validate(v).model_dump() for v in artifacts.versions(db, artifact)],
    }


_INLINE_SAFE = ("text/plain", "application/json", "text/csv", "text/markdown")


@router.get("/artifacts/{artifact_id}/download")
def download_artifact(
    artifact_id: uuid.UUID,
    version: int | None = Query(default=None, ge=1),
    principal: Principal = Depends(require("artifact:download")),
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limited("download", per="user")),
) -> Response:
    artifact, row, url, stream = artifacts.download(db, principal, artifact_id, version=version)
    if url:
        return RedirectResponse(url, status_code=307)
    assert stream is not None
    media = row.content_type if row.content_type in _INLINE_SAFE else "application/octet-stream"
    return StreamingResponse(
        stream,
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="{artifact.name}"',
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "ETag": f'"{row.sha256}"',
        },
    )


# --- environments -------------------------------------------------------------------------------------------------


@router.get("/environments")
def list_envs(
    principal: Principal = Depends(require("experiment:read")), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    return [environments.environment_dict(e) for e in environments.list_environments(db, principal.organization_id)]


@router.post("/environments", status_code=201)
def register_env(
    body: EnvironmentIn, principal: Principal = Depends(require("environment:manage")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    env = environments.register(
        db,
        principal,
        name=body.name,
        image=body.image,
        description=body.description,
        packages=body.packages,
        runtime=body.runtime,
    )
    return environments.environment_dict(env)


@router.get("/harnesses")
def list_harnesses(_: Principal = Depends(require("experiment:read"))) -> list[dict[str, Any]]:
    from engines.lab import harnesses as harness_registry

    return [
        {
            "key": h.key,
            "version": h.version,
            "description": h.description,
            "requires_data": h.requires_data,
            "self_reported": h.self_reported,
            "sha256": h.sha256,
        }
        for h in harness_registry.harnesses().values()
    ]
