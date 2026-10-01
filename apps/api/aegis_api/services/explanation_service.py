"""Finding explanations: what happened, why it matters, what is affected, the evidence, and how to fix it.

Assembled deterministically from stored records (the finding, its evidence, controls and the remediation
library). It never invents facts and never overwrites evidence; every statement points back to the record it
came from. A model-written narrative can be layered on later behind the ``ai_assistant`` feature flag, but
it would always be shown next to — never instead of — the evidence below.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis_api.models import AISystem, Evidence, EvidenceLink, Finding
from aegis_api.services import regression_service

WHY = {
    "fairness": "Decisions that change with protected attributes can cause discriminatory outcomes and legal exposure.",
    "truthfulness": "Unsupported or contradicted claims mislead users and erode trust in the system's answers.",
    "safety": "Unsafe responses can cause real-world harm to users or third parties.",
    "privacy": "Exposed personal data can harm individuals and breach data-protection obligations.",
    "security": "A bypassed guardrail lets an attacker steer the system outside its intended behaviour.",
    "governance": "The system acted outside the controls your organization defined for it.",
}


def explain(session: Session, finding: Finding) -> dict[str, Any]:
    system = session.get(AISystem, finding.system_id)
    evidence_ids = session.scalars(
        select(EvidenceLink.evidence_id).where(
            EvidenceLink.target_type == "finding", EvidenceLink.target_id == finding.id
        )
    ).all()
    evidence = (
        session.scalars(select(Evidence).where(Evidence.id.in_(evidence_ids)).order_by(Evidence.seq).limit(5)).all()
        if evidence_ids
        else []
    )
    recommendation = regression_service.recommend_for_finding(finding)
    rate = f"{finding.occurrences} of {finding.sample_size}" if finding.sample_size else str(finding.occurrences)
    return {
        "generated_by": "deterministic-template",
        "disclaimer": "Assembled from the finding and its evidence. The evidence below is the source of truth.",
        "what_happened": f"{finding.title}. Observed in {rate} test(s) by evaluator "
        f"{finding.evaluator_key or 'n/a'} v{finding.evaluator_version or 'n/a'}.",
        "why_it_matters": WHY.get(finding.dimension, WHY["governance"]),
        "risk": {
            "level": finding.risk_level,
            "score": finding.risk_score,
            "reasons": finding.risk_reasons,
            "factors": finding.risk_factors,
        },
        "affected": {
            "system": system.name if system else None,
            "system_id": str(finding.system_id),
            "environment": system.environment if system else None,
            "system_version": finding.system_version,
            "model": finding.model_version,
            "control": finding.control_ref,
        },
        "evidence": [
            {
                "id": str(e.id),
                "kind": e.kind,
                "title": e.title,
                "content_hash": e.content_hash,
                "confidence": e.confidence_level,
            }
            for e in evidence
        ],
        "evidence_unavailable_reason": None if evidence else finding.evidence_unavailable_reason,
        "remediation": recommendation,
        "next_steps": [
            "Review the evidence artifacts listed above.",
            f"Apply the recommended {recommendation.get('category', 'remediation').replace('_', ' ')}.",
            "Run a retest to verify the fix; the finding resolves only when the retest passes.",
        ],
    }
