"""Policy management: create, compile (from text/DSL/upload), version, diff, frameworks, mappings."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.errors import Conflict, NotFound
from aegis_api.models import (
    Control,
    ControlMapping,
    DocumentChunk,
    Framework,
    FrameworkControl,
    Policy,
    PolicyDocument,
    PolicyRequirement,
    PolicyVersion,
)
from aegis_api.models.enums import PolicyStatus, PolicyVersionStatus
from aegis_api.security.context import Principal
from aegis_api.security.uploads import ValidatedUpload
from aegis_api.services import audit_log, model_gateway
from engines.common.text import stable_hash
from engines.compliance.frameworks import FRAMEWORKS_BY_KEY, suggest_mappings
from engines.policy.compiler import PolicyCompiler
from engines.policy.diff import diff_policies
from engines.policy.dsl import PolicyDSL, parse_dsl
from engines.policy.ingestion import Chunk, ingest


def create_policy(
    session: Session,
    principal: Principal,
    *,
    name: str,
    key: str,
    description: str | None,
    category: str | None,
    owner_name: str | None,
    source_text: str | None,
) -> tuple[Policy, PolicyVersion]:
    key = key.upper()
    if session.scalar(select(Policy.id).where(Policy.organization_id == principal.organization_id, Policy.key == key)):
        raise Conflict(f"A policy with key '{key}' already exists")
    policy = Policy(
        organization_id=principal.organization_id,
        name=name,
        key=key,
        description=description,
        category=category,
        owner_name=owner_name,
        status=PolicyStatus.DRAFT,
    )
    session.add(policy)
    session.flush()
    version = PolicyVersion(
        organization_id=principal.organization_id,
        policy_id=policy.id,
        version="1.0",
        status=PolicyVersionStatus.DRAFT,
        source_type="text" if source_text else "dsl",
        source_text=source_text,
        source_hash=stable_hash(source_text) if source_text else None,
        created_by_id=principal.user_id,
    )
    session.add(version)
    session.flush()
    policy.current_version_id = version.id
    audit_log.record(
        session,
        organization_id=principal.organization_id,
        action="policy.created",
        resource_type="policy",
        resource_id=policy.id,
        principal=principal,
        after={"key": key},
    )
    return policy, version


def _chunks_from_text(text: str) -> list[Chunk]:
    from engines.policy.ingestion import Page, chunk_document

    return chunk_document("policy.txt", [Page(number=1, text=text)]).chunks


def attach_document(
    session: Session, principal: Principal, version: PolicyVersion, upload: ValidatedUpload
) -> PolicyDocument:
    extracted = ingest(upload.filename, upload.data, upload.extension)
    doc = PolicyDocument(
        organization_id=principal.organization_id,
        policy_id=version.policy_id,
        policy_version_id=version.id,
        filename=upload.filename,
        content_type=upload.content_type,
        size_bytes=len(upload.data),
        sha256=stable_hash(upload.data.decode("utf-8", "ignore")),
        page_count=extracted.page_count,
        scan_status="not_scanned",
        extracted_at=utcnow(),
    )
    session.add(doc)
    session.flush()
    version.source_type = "upload"
    version.source_text = extracted.text[:100000]
    version.source_hash = stable_hash(extracted.text)
    _store_chunks(session, principal.organization_id, version, extracted.chunks, doc.id)
    return doc


def _store_chunks(
    session: Session, org_id: uuid.UUID, version: PolicyVersion, chunks: list[Chunk], document_id: uuid.UUID | None
) -> None:
    embedder = model_gateway.build_embedder(session, org_id)
    texts = [c.text for c in chunks]
    vectors = embedder.embed(texts) if texts else []
    for chunk, vector in zip(chunks, vectors, strict=False):
        session.add(
            DocumentChunk(
                organization_id=org_id,
                policy_version_id=version.id,
                document_id=document_id,
                chunk_index=chunk.index,
                page_number=chunk.page_number,
                section=chunk.section,
                heading=chunk.heading,
                text=chunk.text,
                content_hash=chunk.content_hash,
                char_start=chunk.char_start,
                char_end=chunk.char_end,
                embedding=vector,
                embedding_model=embedder.name,
            )
        )


def compile_version(session: Session, version_id: uuid.UUID) -> PolicyVersion:
    version = session.get(PolicyVersion, version_id)
    if version is None:
        raise NotFound("Policy version not found")
    policy = session.get(Policy, version.policy_id)
    if policy is None:
        raise NotFound("Policy not found for version")
    version.status = PolicyVersionStatus.COMPILING
    session.flush()
    try:
        if version.dsl_yaml:
            dsl = parse_dsl(version.dsl_yaml)
            _apply_dsl(session, version, policy, dsl, report={"source": "dsl", "controls_generated": len(dsl.controls)})
        else:
            chunks = session.scalars(
                select(DocumentChunk)
                .where(DocumentChunk.policy_version_id == version.id)
                .order_by(DocumentChunk.chunk_index)
            ).all()
            if not chunks and version.source_text:
                _store_chunks(session, version.organization_id, version, _chunks_from_text(version.source_text), None)
                chunks = session.scalars(
                    select(DocumentChunk)
                    .where(DocumentChunk.policy_version_id == version.id)
                    .order_by(DocumentChunk.chunk_index)
                ).all()
            engine_chunks = [
                Chunk(
                    index=c.chunk_index,
                    text=c.text,
                    page_number=c.page_number,
                    section=c.section,
                    heading=c.heading,
                    char_start=c.char_start,
                    char_end=c.char_end,
                )
                for c in chunks
            ]
            judge = model_gateway.build_judge_router(session, version.organization_id)
            compiler = PolicyCompiler(judge=judge if judge.available() else None)
            result = compiler.compile(policy.key, version.version, engine_chunks)
            _persist_requirements(session, version, result.requirements, {c.chunk_index: c for c in chunks})
            _apply_dsl(session, version, policy, result.to_dsl(policy.name), report=result.report)
        version.status = PolicyVersionStatus.COMPILED
        version.compiled_at = utcnow()
        version.compiler_version = "1.1.0"
        policy.status = PolicyStatus.ACTIVE
    except Exception as exc:
        version.status = PolicyVersionStatus.FAILED
        version.compile_report = {"error": str(exc)}
        raise
    return version


def _persist_requirements(
    session: Session, version: PolicyVersion, requirements: list[Any], chunk_map: dict[int, DocumentChunk]
) -> None:
    for req in requirements:
        chunk = chunk_map.get(req.chunk_index)
        session.add(
            PolicyRequirement(
                organization_id=version.organization_id,
                policy_version_id=version.id,
                requirement_key=req.key,
                text=req.text,
                normalized_text=req.normalized,
                modality=req.modality,
                chunk_id=chunk.id if chunk else None,
                document_id=chunk.document_id if chunk else None,
                page_number=req.page_number,
                section=req.section,
                source_excerpt=req.source_excerpt,
                source_hash=req.source_hash,
                confidence=req.confidence,
                needs_human_review=req.needs_human_review,
            )
        )


def _apply_dsl(
    session: Session, version: PolicyVersion, policy: Policy, dsl: PolicyDSL, report: dict[str, Any]
) -> None:
    req_by_key = {
        r.requirement_key: r
        for r in session.scalars(
            select(PolicyRequirement).where(PolicyRequirement.policy_version_id == version.id)
        ).all()
    }
    for c in dsl.controls:
        req = req_by_key.get((c.source or {}).get("requirement_key", ""))
        control = Control(
            organization_id=version.organization_id,
            policy_id=policy.id,
            policy_version_id=version.id,
            requirement_id=req.id if req else None,
            control_id=c.id,
            name=c.name or c.requirement[:120],
            description=c.requirement,
            domain=c.domain,
            test_type=c.test_type,
            threshold=c.threshold,
            severity=c.severity,
            automation=c.automation,
            required_evidence=c.required_evidence,
            condition=c.condition,
            confidence=req.confidence if req else 1.0,
            needs_human_review=c.needs_human_review,
            source="dsl" if version.dsl_yaml else "compiled",
        )
        session.add(control)
        session.flush()
        _auto_map(session, control)
    version.dsl_yaml = version.dsl_yaml or _to_yaml(dsl)
    version.compile_report = report


def _to_yaml(dsl: PolicyDSL) -> str:
    from engines.policy.dsl import to_yaml

    return to_yaml(dsl)


def _auto_map(session: Session, control: Control) -> None:
    for fw_key, framework in FRAMEWORKS_BY_KEY.items():
        for match in suggest_mappings(control.domain, control.test_type, framework):
            db_fw = session.scalar(
                select(Framework).where(Framework.key == fw_key, Framework.organization_id.is_(None))
            )
            if db_fw is None:
                continue
            fc = session.scalar(
                select(FrameworkControl).where(
                    FrameworkControl.framework_id == db_fw.id, FrameworkControl.ref == match["ref"]
                )
            )
            if fc is None:
                continue
            exists = session.scalar(
                select(ControlMapping.id).where(
                    ControlMapping.control_id == control.id, ControlMapping.framework_control_id == fc.id
                )
            )
            if not exists:
                session.add(
                    ControlMapping(
                        organization_id=control.organization_id,
                        control_id=control.id,
                        framework_control_id=fc.id,
                        rationale=match["rationale"],
                        confidence=match["confidence"],
                    )
                )


def create_version(
    session: Session,
    principal: Principal,
    policy: Policy,
    *,
    source_text: str | None,
    dsl_yaml: str | None,
    change_note: str | None,
) -> PolicyVersion:
    current = session.get(PolicyVersion, policy.current_version_id) if policy.current_version_id else None
    new_version = _next_version(current.version if current else "1.0")
    if dsl_yaml:
        parse_dsl(dsl_yaml)  # validate
    version = PolicyVersion(
        organization_id=principal.organization_id,
        policy_id=policy.id,
        version=new_version,
        status=PolicyVersionStatus.DRAFT,
        source_type="dsl" if dsl_yaml else "text",
        source_text=source_text,
        source_hash=stable_hash(source_text or dsl_yaml or ""),
        dsl_yaml=dsl_yaml,
        change_note=change_note,
        parent_version_id=current.id if current else None,
        created_by_id=principal.user_id,
    )
    session.add(version)
    session.flush()
    return version


def _next_version(version: str) -> str:
    try:
        major = version.split(".")[0]
        return f"{int(major) + 1}.0"
    except (ValueError, IndexError):
        return "2.0"


def diff_versions(session: Session, policy: Policy, from_id: uuid.UUID, to_id: uuid.UUID) -> dict[str, Any]:
    def to_dsl(vid: uuid.UUID) -> PolicyDSL:
        version = session.get(PolicyVersion, vid)
        if version is None or version.policy_id != policy.id:
            raise NotFound("Policy version not found")
        controls = session.scalars(select(Control).where(Control.policy_version_id == vid)).all()
        from engines.policy.dsl import ControlDSL

        return PolicyDSL(
            id=policy.key,
            name=policy.name,
            version=version.version,
            controls=[
                ControlDSL(
                    id=c.control_id,
                    requirement=c.description or c.name,
                    test_type=c.test_type,
                    name=c.name,
                    severity=c.severity,
                    threshold=c.threshold,
                    condition=c.condition,
                )
                for c in controls
            ],
        )

    old, new = to_dsl(from_id), to_dsl(to_id)
    d = diff_policies(old, new)
    return {"from_version": old.version, "to_version": new.version, **d.summary()}


def seed_frameworks(session: Session) -> int:
    """Load global framework reference packs (idempotent)."""
    from engines.compliance.frameworks import FRAMEWORKS

    count = 0
    for pack in FRAMEWORKS:
        existing = session.scalar(
            select(Framework).where(Framework.key == pack.key, Framework.organization_id.is_(None))
        )
        if existing:
            continue
        fw = Framework(
            organization_id=None,
            key=pack.key,
            name=pack.name,
            version=pack.version,
            version_date=pack.version_date,
            publisher=pack.publisher,
            description=pack.description,
            source_url=pack.source_url,
            kind="reference",
            disclaimer=pack.disclaimer,
        )
        session.add(fw)
        session.flush()
        for i, ctrl in enumerate(pack.controls):
            session.add(
                FrameworkControl(
                    framework_id=fw.id,
                    ref=ctrl.ref,
                    title=ctrl.title,
                    description=ctrl.description,
                    group=ctrl.group,
                    domains=list(ctrl.domains),
                    test_types=list(ctrl.test_types),
                    sort_order=i,
                )
            )
        count += 1
    return count


def get_policy(session: Session, policy_id: uuid.UUID, organization_id: uuid.UUID) -> Policy:
    policy = session.get(Policy, policy_id)
    if policy is None or policy.organization_id != organization_id:
        raise NotFound("Policy not found")
    return policy
