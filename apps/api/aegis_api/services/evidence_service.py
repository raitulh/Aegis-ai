"""Evidence integrity: chain verification, signed evidence packages and workspace integrity status.

Terminology is deliberately precise: packages are *tamper-evident* and *cryptographically verifiable*. A
``VERIFIED`` result means the hashes, chain links and signature check out — nothing more is claimed.
"""

from __future__ import annotations

import inspect
import io
import json
import uuid
import zipfile
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.models import (
    Audit,
    ControlAssessment,
    Evidence,
    EvidenceExport,
    EvidenceLink,
    Finding,
    FindingOccurrence,
    Organization,
    Report,
)
from aegis_api.security import signing
from aegis_api.security.context import Principal
from aegis_api.services import audit_log, usage_service
from engines.evidence import package as evidence_package

VERIFY_INSTRUCTIONS = """# Verifying this evidence package

This package was exported from Aegis. It is tamper-evident: every artifact is content-hashed and linked into a
SHA-256 hash chain, every file is listed with its SHA-256 in `manifest.json`, and the manifest is signed with
Ed25519 (`manifest.sig.json`).

Verify it independently (Python 3.10+, standard library; `pip install cryptography` to check the signature):

    python verify.py <this-package>.zip

For a stronger check, obtain the workspace's public signing key out of band (Aegis: Evidence → Verify, or
`GET /api/v1/evidence/signing-key`) and pass it explicitly:

    python verify.py <this-package>.zip --public-key <base64-key>

Result statuses: VERIFIED, TAMPERED, INCOMPLETE, UNSIGNED. A VERIFIED result attests to integrity only. It is
not a legal opinion, certification or statement of regulatory compliance.
"""


def _artifact(ev: Evidence) -> dict[str, Any]:
    return {
        "id": str(ev.id),
        "seq": ev.seq,
        "kind": ev.kind,
        "title": ev.title,
        "content": ev.content,
        "content_hash": ev.content_hash,
        "prev_hash": ev.prev_hash,
        "chain_hash": ev.chain_hash,
        "confidence_level": ev.confidence_level,
        "source_uri": ev.source_uri,
        "sensitive": ev.sensitive,
        "created_at": ev.created_at.isoformat() if ev.created_at else None,
        "purged": ev.purged_at is not None,
    }


def audit_artifacts(session: Session, audit_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = session.scalars(select(Evidence).where(Evidence.audit_id == audit_id).order_by(Evidence.seq)).all()
    return [_artifact(ev) for ev in rows]


def verify_audit(session: Session, audit: Audit) -> dict[str, Any]:
    """Recompute content hashes and the hash chain for one audit's evidence from the database."""
    artifacts = audit_artifacts(session, audit.id)
    if not artifacts:
        status = "EMPTY" if audit.status in ("completed", "partially_completed") else "PENDING"
        return {
            "audit_id": str(audit.id),
            "status": status,
            "records": 0,
            "content_checked": 0,
            "purged": 0,
            "head": None,
            "recorded_head": audit.evidence_head_hash,
            "problems": [],
            "checked_at": utcnow().isoformat(),
        }
    report = evidence_package.verify_artifacts(artifacts, audit.evidence_head_hash)
    return {
        "audit_id": str(audit.id),
        **report.as_dict(),
        "recorded_head": audit.evidence_head_hash,
        "checked_at": utcnow().isoformat(),
    }


def integrity_summary(session: Session, organization_id: uuid.UUID, limit: int = 20) -> dict[str, Any]:
    audits = session.scalars(
        select(Audit)
        .where(
            Audit.organization_id == organization_id,
            Audit.status.in_(["completed", "partially_completed"]),
        )
        .order_by(Audit.created_at.desc())
        .limit(limit)
    ).all()
    results = []
    for audit in audits:
        verdict = verify_audit(session, audit)
        results.append(
            {
                "audit_id": str(audit.id),
                "audit_name": audit.name,
                "status": verdict["status"],
                "records": verdict["records"],
                "head": verdict["head"],
                "completed_at": audit.completed_at.isoformat() if audit.completed_at else None,
            }
        )
    total = session.scalar(select(func.count(Evidence.id)).where(Evidence.organization_id == organization_id)) or 0
    tampered = [r for r in results if r["status"] == "TAMPERED"]
    overall = "TAMPERED" if tampered else ("VERIFIED" if results else "EMPTY")
    return {
        "status": overall,
        "audits_checked": len(results),
        "audits_verified": sum(1 for r in results if r["status"] == "VERIFIED"),
        "evidence_total": int(total),
        "results": results,
        "signing_key_id": signing.key_id(),
        "signing_key_development": signing.is_development_key(),
        "checked_at": utcnow().isoformat(),
    }


def _findings_for_audit(session: Session, audit_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = session.execute(
        select(Finding, FindingOccurrence)
        .join(FindingOccurrence, FindingOccurrence.finding_id == Finding.id)
        .where(FindingOccurrence.audit_id == audit_id, FindingOccurrence.source_type == "audit")
        .order_by(Finding.number)
    ).all()
    out = []
    for finding, occurrence in rows:
        evidence_ids = session.scalars(
            select(EvidenceLink.evidence_id).where(
                EvidenceLink.target_type == "finding", EvidenceLink.target_id == finding.id
            )
        ).all()
        out.append(
            {
                "id": str(finding.id),
                "number": finding.number,
                "title": finding.title,
                "category": finding.category,
                "severity_observed": occurrence.severity,
                "risk_level_observed": occurrence.risk_level,
                "status": finding.status,
                "control_ref": finding.control_ref,
                "occurrences": occurrence.occurrences,
                "sample_size": occurrence.sample_size,
                "evidence_ids": [str(e) for e in evidence_ids],
            }
        )
    return out


def build_package(
    session: Session, audit: Audit, principal: Principal | None
) -> tuple[bytes, dict[str, Any], EvidenceExport]:
    """Assemble, sign and record a zipped evidence package for one audit."""
    org = session.get(Organization, audit.organization_id)
    artifacts = audit_artifacts(session, audit.id)
    findings = _findings_for_audit(session, audit.id)
    assessments = session.scalars(select(ControlAssessment).where(ControlAssessment.audit_id == audit.id)).all()
    report = session.scalar(select(Report).where(Report.audit_id == audit.id).order_by(Report.created_at.desc()))
    verification = verify_audit(session, audit)
    prefix = f"aegis-evidence-{str(audit.id)[:8]}"

    files: dict[str, bytes] = {}
    artifact_entries = []
    for art in artifacts:
        path = f"evidence/{art['seq']:05d}.json"
        files[path] = evidence_package.canonical(dict(art))
        artifact_entries.append(
            {
                "seq": art["seq"],
                "id": art["id"],
                "kind": art["kind"],
                "title": art["title"],
                "content_hash": art["content_hash"],
                "chain_hash": art["chain_hash"],
                "file": path,
            }
        )
    files["findings.json"] = evidence_package.canonical(findings)
    files["controls.json"] = evidence_package.canonical(
        [
            {
                "control_id": str(a.control_id),
                "status": a.status,
                "tests_run": a.tests_run,
                "failures": a.failures,
                "evidence_count": a.evidence_count,
            }
            for a in assessments
        ]
    )
    if report is not None:
        files["report.json"] = evidence_package.canonical(report.content)
    files["VERIFY.md"] = VERIFY_INSTRUCTIONS.encode()
    files["verify.py"] = inspect.getsource(evidence_package).encode()
    digests = {path: evidence_package.sha256_hex(data) for path, data in files.items()}
    root = evidence_package.root_hash(audit.evidence_head_hash, digests)
    generated_at = utcnow().isoformat()
    summary = audit.summary or {}
    manifest = {
        "format": evidence_package.FORMAT,
        "generated_at": generated_at,
        "generated_by": principal.actor_label if principal else "system",
        "organization": {"id": str(audit.organization_id), "name": org.name if org else None},
        "audit": {
            "id": str(audit.id),
            "name": audit.name,
            "system_id": str(audit.system_id),
            "system_name": audit.system.name if audit.system else None,
            "status": audit.status,
            "categories": audit.categories,
            "started_at": audit.started_at.isoformat() if audit.started_at else None,
            "completed_at": audit.completed_at.isoformat() if audit.completed_at else None,
            "manifest": audit.manifest,
        },
        "counts": {
            "artifacts": len(artifacts),
            "findings": len(findings),
            "controls": len(assessments),
            "tests": int((summary.get("tests") or {}).get("total", audit.test_count or 0)),
        },
        "chain_head": audit.evidence_head_hash,
        "integrity_at_export": verification["status"],
        "artifacts": artifact_entries,
        "files": digests,
        "root_hash": root,
    }
    manifest_bytes = json.dumps(manifest, sort_keys=True, indent=2).encode()
    signature: dict[str, Any] = {
        "algorithm": "Ed25519",
        "key_id": signing.key_id(),
        "public_key": signing.public_key_b64(),
        "signature": signing.sign(manifest_bytes),
        "development_key": signing.is_development_key(),
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{prefix}/manifest.json", manifest_bytes)
        zf.writestr(f"{prefix}/manifest.sig.json", json.dumps(signature, indent=2))
        for path, data in files.items():
            zf.writestr(f"{prefix}/{path}", data)

    export = EvidenceExport(
        organization_id=audit.organization_id,
        audit_id=audit.id,
        scope="audit",
        root_hash=root,
        artifact_count=len(artifacts),
        integrity_status=verification["status"],
        signature=signature["signature"][:200],
        key_id=signature["key_id"],
        manifest={k: v for k, v in manifest.items() if k not in ("artifacts", "files")},
        created_by_id=principal.user_id if principal and principal.auth_method != "api_key" else None,
    )
    session.add(export)
    session.flush()
    audit_log.record(
        session,
        organization_id=audit.organization_id,
        action="evidence.exported",
        resource_type="audit",
        resource_id=audit.id,
        principal=principal,
        after={"export_id": str(export.id), "root_hash": root, "artifacts": len(artifacts)},
    )
    usage_service.record(
        session,
        audit.organization_id,
        "evidence_export",
        source_type="evidence_export",
        source_id=export.id,
        metadata={"audit_id": str(audit.id)},
    )
    return buffer.getvalue(), manifest, export


def package_filename(audit: Audit, when: datetime | None = None) -> str:
    stamp = (when or utcnow()).strftime("%Y%m%dT%H%M%SZ")
    return f"aegis-evidence-{str(audit.id)[:8]}-{stamp}.zip"
