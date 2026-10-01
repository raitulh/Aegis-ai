"""The Assurance Graph: real relationships between systems, agents, models, tools, policies, controls, audits,
findings, evidence, remediations and retests.

Every node and edge is derived from stored records (no decorative nodes). Queries are bounded and batched
(no per-node queries); large categories are clustered (evidence is one node per audit with a count).
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.deps import get_db, require
from aegis_api.models import (
    AISystem,
    Audit,
    Control,
    ControlAssessment,
    Evidence,
    Finding,
    Policy,
    RegressionRun,
    Remediation,
    RuntimeEvent,
    RuntimePolicy,
    RuntimePolicyAssignment,
)
from aegis_api.models.enums import OPEN_FINDING_STATUSES
from aegis_api.security.context import Principal

router = APIRouter(prefix="/api/v1", tags=["Assurance Graph"])
SEVERITY_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


class _Graph:
    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: list[dict[str, Any]] = []
        self._edge_keys: set[tuple[str, str, str]] = set()

    def node(self, node_id: str, type_: str, label: str, **extra: Any) -> str:
        if node_id not in self.nodes:
            self.nodes[node_id] = {"id": node_id, "type": type_, "label": label[:120], **extra}
        return node_id

    def edge(self, source: str, target: str, relation: str, **extra: Any) -> None:
        key = (source, target, relation)
        if source in self.nodes and target in self.nodes and key not in self._edge_keys:
            self._edge_keys.add(key)
            self.edges.append({"source": source, "target": target, "relation": relation, **extra})


@router.get("/graph")
def assurance_graph(
    system_id: uuid.UUID | None = Query(None),
    min_severity: str = Query("low", pattern="^(info|low|medium|high|critical)$"),
    days: int = Query(90, ge=1, le=365),
    include_resolved: bool = Query(False),
    principal: Principal = Depends(require("org:read")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    org = principal.organization_id
    since = utcnow() - timedelta(days=days)
    g = _Graph()

    systems_q = select(AISystem).where(AISystem.organization_id == org, AISystem.deleted_at.is_(None))
    if system_id:
        systems_q = systems_q.where(AISystem.id == system_id)
    systems = db.scalars(systems_q.limit(200)).all()
    system_ids = [s.id for s in systems]
    for s in systems:
        kind = "agent" if s.system_type in ("agent", "multi_agent") else "system"
        sid = g.node(
            f"system:{s.id}",
            kind,
            s.name,
            href=f"/dashboard/{'agents' if kind == 'agent' else 'systems'}/{s.id}",
            environment=s.environment,
            risk_tier=s.risk_tier,
            runtime_mode=s.runtime_mode,
        )
        if s.model_name:
            mid = g.node(f"model:{s.model_name}", "model", s.model_name)
            g.edge(sid, mid, "uses_model")
        for tool in sorted(((s.config or {}).get("tools") or {}).keys())[:30]:
            tid = g.node(f"tool:{s.id}:{tool}", "tool", tool, system_id=str(s.id))
            g.edge(sid, tid, "can_call")

    if system_ids:
        observed = db.execute(
            select(RuntimeEvent.system_id, RuntimeEvent.tool_name, func.count())
            .where(
                RuntimeEvent.organization_id == org,
                RuntimeEvent.system_id.in_(system_ids),
                RuntimeEvent.tool_name.is_not(None),
                RuntimeEvent.occurred_at >= since,
            )
            .group_by(RuntimeEvent.system_id, RuntimeEvent.tool_name)
            .limit(300)
        ).all()
        for sys_id, tool, count in observed:
            tid = g.node(f"tool:{sys_id}:{tool}", "tool", str(tool), system_id=str(sys_id))
            g.nodes[tid]["observed_calls"] = int(count)
            g.edge(f"system:{sys_id}", tid, "called", count=int(count))

    # Runtime policies and their scope.
    assignments = db.execute(
        select(RuntimePolicy, RuntimePolicyAssignment)
        .join(RuntimePolicyAssignment, RuntimePolicyAssignment.policy_id == RuntimePolicy.id)
        .where(RuntimePolicy.organization_id == org, RuntimePolicyAssignment.enabled.is_(True))
    ).all()
    for policy, assignment in assignments:
        pid = g.node(
            f"runtime_policy:{policy.id}",
            "runtime_policy",
            policy.name,
            status=policy.status,
            href=f"/dashboard/policies/runtime/{policy.id}",
        )
        for s in systems:
            applies = (
                assignment.scope_type == "organization"
                or (assignment.scope_type == "environment" and assignment.scope_key == s.environment)
                or (assignment.scope_type == "system" and assignment.system_id == s.id)
            )
            if applies:
                g.edge(pid, f"system:{s.id}", "governs", scope=assignment.scope_type)

    # Audits in the window.
    audits = (
        db.scalars(
            select(Audit)
            .where(Audit.organization_id == org, Audit.system_id.in_(system_ids), Audit.created_at >= since)
            .order_by(Audit.created_at.desc())
            .limit(60)
        ).all()
        if system_ids
        else []
    )
    audit_ids = [a.id for a in audits]
    for a in audits:
        aid = g.node(
            f"audit:{a.id}",
            "test",
            a.name,
            status=a.status,
            created_at=a.created_at.isoformat(),
            href=f"/dashboard/audits/{a.id}",
            posture=(a.summary or {}).get("posture"),
        )
        g.edge(aid, f"system:{a.system_id}", "tested")
    if audit_ids:
        counts = db.execute(
            select(Evidence.audit_id, func.count()).where(Evidence.audit_id.in_(audit_ids)).group_by(Evidence.audit_id)
        ).all()
        for audit_id, count in counts:
            eid = g.node(
                f"evidence:{audit_id}",
                "evidence",
                f"{count} artifacts",
                count=int(count),
                href=f"/dashboard/audits/{audit_id}?tab=evidence",
            )
            g.edge(eid, f"audit:{audit_id}", "proves")
        assessments = db.execute(
            select(ControlAssessment, Control)
            .join(Control, Control.id == ControlAssessment.control_id)
            .where(ControlAssessment.audit_id.in_(audit_ids))
        ).all()
        policy_ids = {c.policy_id for _, c in assessments if c.policy_id}
        policies = (
            {p.id: p for p in db.scalars(select(Policy).where(Policy.id.in_(policy_ids))).all()} if policy_ids else {}
        )
        for assessment, control in assessments:
            cid = g.node(
                f"control:{control.id}",
                "control",
                f"{control.control_id} {control.name}",
                status=assessment.status,
                href=f"/dashboard/policies/{control.policy_id}" if control.policy_id else None,
            )
            g.edge(f"audit:{assessment.audit_id}", cid, "assessed", status=assessment.status)
            compliance_policy = policies.get(control.policy_id) if control.policy_id else None
            if compliance_policy is not None:
                pid = g.node(
                    f"policy:{compliance_policy.id}",
                    "policy",
                    compliance_policy.name,
                    href=f"/dashboard/policies/{compliance_policy.id}",
                )
                g.edge(pid, cid, "defines")

    # Findings (open by default), with remediations and retests.
    statuses = None if include_resolved else [str(s) for s in OPEN_FINDING_STATUSES]
    # Findings only for systems in scope (an empty scope yields none; archived systems are excluded).
    findings_q = select(Finding).where(Finding.organization_id == org, Finding.system_id.in_(system_ids))
    if statuses:
        findings_q = findings_q.where(Finding.status.in_(statuses))
    threshold = SEVERITY_ORDER[min_severity]
    findings = [
        f
        for f in db.scalars(findings_q.order_by(Finding.risk_score.desc()).limit(300)).all()
        if SEVERITY_ORDER.get(f.severity, 0) >= threshold
    ][:150]
    finding_ids = [f.id for f in findings]
    for f in findings:
        fid = g.node(
            f"finding:{f.id}",
            "finding",
            f"#{f.number} {f.title}",
            severity=f.severity,
            risk_level=f.risk_level,
            status=f.status,
            source=f.source,
            href=f"/dashboard/findings/{f.id}",
        )
        g.edge(fid, f"system:{f.system_id}", "affects")
        if f.audit_id:
            g.edge(f"audit:{f.audit_id}", fid, "detected")
            g.edge(fid, f"evidence:{f.audit_id}", "evidenced_by")
        if f.control_id:
            g.edge(fid, f"control:{f.control_id}", "violates")
    if finding_ids:
        for r in db.scalars(select(Remediation).where(Remediation.finding_id.in_(finding_ids))).all():
            rid = g.node(f"remediation:{r.id}", "remediation", r.title, status=r.status)
            g.edge(rid, f"finding:{r.finding_id}", "remediates")
        runs = db.scalars(
            select(RegressionRun)
            .where(RegressionRun.organization_id == org, RegressionRun.created_at >= since)
            .order_by(RegressionRun.created_at.desc())
            .limit(100)
        ).all()
        finding_set = {str(fid) for fid in finding_ids}
        for run in runs:
            for result in run.results or []:
                target = str(result.get("finding_id") or "")
                if target in finding_set:
                    xid = g.node(
                        f"retest:{run.id}",
                        "retest",
                        f"Retest {run.verdict or run.status}",
                        status=run.verdict or run.status,
                    )
                    g.edge(xid, f"finding:{target}", "retests")

    type_counts: dict[str, int] = {}
    for node in g.nodes.values():
        type_counts[node["type"]] = type_counts.get(node["type"], 0) + 1
    # Only return edges whose endpoints are both in the (bounded) node set, de-duplicated.
    seen: set[tuple[str, str, str]] = set()
    edges = []
    for e in g.edges:
        key = (e["source"], e["target"], e["relation"])
        if e["source"] in g.nodes and e["target"] in g.nodes and key not in seen:
            seen.add(key)
            edges.append(e)
    return {
        "nodes": list(g.nodes.values()),
        "edges": edges,
        "counts": type_counts,
        "filters": {
            "system_id": str(system_id) if system_id else None,
            "min_severity": min_severity,
            "days": days,
            "include_resolved": include_resolved,
        },
        "generated_at": utcnow().isoformat(),
    }
