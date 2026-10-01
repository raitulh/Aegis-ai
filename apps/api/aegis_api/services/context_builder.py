"""Assemble engine execution context (target, controls, retriever, judge) from DB state."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis_api.models import AISystem, Control, KnowledgeChunk, KnowledgeDocument
from aegis_api.security.ssrf import guarded_client, validate_outbound_url
from aegis_api.services import secrets_service
from aegis_api.services.model_gateway import build_embedder, build_judge_router, build_target_provider
from engines.evaluation.base import ControlSpec, EvaluationContext, RetrievedDoc, SystemProfile
from engines.evaluation.targets import ProviderTarget, Target
from engines.providers.demo import SimulatedTarget
from engines.providers.demo.common import SimulatedSystem
from engines.providers.embeddings import cosine
from engines.providers.http_endpoint import HTTPEndpointTarget


def build_system_profile(system: AISystem) -> SystemProfile:
    config = system.config or {}
    return SystemProfile(
        system_type=system.system_type,
        environment=system.environment,
        risk_tier=system.risk_tier,
        tools=config.get("tools", {}),
        guardrails=config.get("guardrails", {}),
        decision_schema=config.get("decision_schema", {}),
        protected_attributes=config.get("protected_attributes", ["gender", "age", "location"]),
        canary=config.get("canary"),
        domain=config.get("domain", _infer_domain(system)),
    )


def _infer_domain(system: AISystem) -> str:
    profile = (system.config or {}).get("demo_profile", "")
    return {"hiring_agent": "hiring", "support_rag": "support", "research_agent": "research"}.get(profile, "general")


def build_target(session: Session, system: AISystem) -> Target:
    provider = build_target_provider(session, system)
    if provider is not None and hasattr(provider, "simulator"):
        sim: SimulatedSystem = provider.simulator
        return SimulatedTarget(sim)
    if provider is not None:
        return ProviderTarget(
            provider,
            model=system.model_name,
            system_instructions=system.system_instructions,
            temperature=(system.config or {}).get("temperature", 0.7),
        )
    if system.endpoint_url:
        url = validate_outbound_url(system.endpoint_url)
        auth = secrets_service.resolve_optional(session, system.auth_secret_id, system.organization_id)
        header = f"Bearer {auth}" if auth else None
        return HTTPEndpointTarget(url, auth_header=header, client=guarded_client(timeout=30.0))
    raise ValueError("System has no runnable provider, model or endpoint configured")


def load_controls(session: Session, policy_version_ids: list[uuid.UUID]) -> dict[str, ControlSpec]:
    if not policy_version_ids:
        return {}
    controls = session.scalars(select(Control).where(Control.policy_version_id.in_(policy_version_ids))).all()
    specs: dict[str, ControlSpec] = {}
    for c in controls:
        specs[c.control_id] = ControlSpec(
            control_id=c.control_id,
            test_type=c.test_type,
            domain=c.domain,
            severity=c.severity,
            threshold=c.threshold or {},
            condition=c.condition,
            name=c.name,
            requirement=(c.description or c.name),
        )
    return specs


def build_retriever(session: Session, system: AISystem):
    """Vector retriever over the system's knowledge base (falls back to lexical if no embeddings)."""
    doc_ids = session.scalars(
        select(KnowledgeDocument.id).where(
            KnowledgeDocument.organization_id == system.organization_id,
            (KnowledgeDocument.system_id == system.id) | (KnowledgeDocument.system_id.is_(None)),
        )
    ).all()
    if not doc_ids:
        return None
    embedder = build_embedder(session, system.organization_id)

    def retrieve(query: str, k: int) -> list[RetrievedDoc]:
        chunks = session.scalars(select(KnowledgeChunk).where(KnowledgeChunk.document_id.in_(doc_ids))).all()
        if not chunks:
            return []
        query_vec = embedder.embed([query])[0]
        scored: list[tuple[float, KnowledgeChunk]] = []
        for chunk in chunks:
            score = cosine(query_vec, list(chunk.embedding)) if chunk.embedding is not None else 0.0
            scored.append((score, chunk))
        scored.sort(key=lambda x: -x[0])
        docs_by_id = {
            d.id: d for d in session.scalars(select(KnowledgeDocument).where(KnowledgeDocument.id.in_(doc_ids))).all()
        }
        out: list[RetrievedDoc] = []
        for score, chunk in scored[:k]:
            doc = docs_by_id.get(chunk.document_id)
            out.append(
                RetrievedDoc(
                    doc_id=str(chunk.id),
                    title=doc.title if doc else "Knowledge",
                    text=chunk.text,
                    url=doc.url if doc else None,
                    score=round(score, 3),
                )
            )
        return out

    return retrieve


def build_context(
    session: Session,
    system: AISystem,
    policy_version_ids: list[uuid.UUID],
    *,
    seed: int = 1337,
    judge_preference: str | None = None,
    use_judge: bool = True,
    on_model_call=None,
) -> EvaluationContext:
    judge = (
        build_judge_router(session, system.organization_id, preference=judge_preference, on_call=on_model_call)
        if use_judge
        else None
    )
    return EvaluationContext(
        system=build_system_profile(system),
        controls=load_controls(session, policy_version_ids),
        retriever=build_retriever(session, system),
        judge=judge if (judge and judge.available()) else None,
        seed=seed,
    )
