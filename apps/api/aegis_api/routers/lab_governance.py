"""Governance: approvals, lab policies, tools & tool calls, MCP, models & routing, usage & billing, webhooks,
benchmarks, audit and administrative maintenance."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.deps import get_db, rate_limited, require
from aegis_api.idempotency import Idempotency, idempotency
from aegis_api.infrastructure.llm.router import get_registry
from aegis_api.models import AuditLog, Webhook, WebhookDelivery
from aegis_api.models.lab import (
    Approval,
    BenchmarkRun,
    LabPolicy,
    LabPolicyVersion,
    LabToolCall,
    MCPServer,
    MCPTool,
    ModelConfig,
)
from aegis_api.routers._helpers import paginate
from aegis_api.schemas.common import Message, Page, PageParams
from aegis_api.schemas.lab import (
    Accepted,
    ApprovalDecisionIn,
    ApprovalOut,
    BenchmarkIn,
    BenchmarkRunOut,
    LabPolicyOut,
    MCPServerIn,
    MCPServerOut,
    MCPToolApproveIn,
    MCPToolOut,
    ModelConfigIn,
    PolicyIn,
    PolicySimulateIn,
    PolicyStatusIn,
    PolicyVersionIn,
    RoutePreviewIn,
    ToolCallOut,
    WebhookIn,
    WebhookOut,
)
from aegis_api.security.context import Principal
from aegis_api.services import audit_log, quota_service, webhook_service
from aegis_api.services.lab import approvals, benchmarks, billing, mcp, retention, usage
from aegis_api.services.lab import policy as lab_policy
from aegis_api.services.lab.access import get_scoped
from aegis_api.services.lab.tools import BUILTIN_TOOLS
from aegis_api.workflows import client as workflow_client
from engines.lab.enums import WEBHOOK_EVENT_NAMES
from engines.lab.routing import ModelRouter, RoutingPolicy

router = APIRouter(prefix="/api/v1", tags=["Governance"])


# --- approvals ----------------------------------------------------------------------------------------------------


@router.get("/approvals", response_model=Page[ApprovalOut])
def list_approvals(
    params: PageParams = Depends(),
    status: str | None = Query(default=None, max_length=16),
    mission_id: uuid.UUID | None = Query(default=None),
    project_id: uuid.UUID | None = Query(default=None),
    principal: Principal = Depends(require("approval:read")),
    db: Session = Depends(get_db),
) -> Page[ApprovalOut]:
    stmt = approvals.list_approvals(db, principal, status=status, mission_id=mission_id, project_id=project_id)
    return paginate(db, stmt, params, ApprovalOut.model_validate)


@router.get("/approvals/{approval_id}", response_model=ApprovalOut)
def get_approval(
    approval_id: uuid.UUID, principal: Principal = Depends(require("approval:read")), db: Session = Depends(get_db)
) -> ApprovalOut:
    return ApprovalOut.model_validate(get_scoped(db, principal, Approval, approval_id, label="Approval"))


@router.post("/approvals/{approval_id}/decide", response_model=ApprovalOut)
def decide_approval(
    approval_id: uuid.UUID,
    body: ApprovalDecisionIn,
    principal: Principal = Depends(require("approval:decide")),
    db: Session = Depends(get_db),
) -> ApprovalOut:
    return ApprovalOut.model_validate(
        approvals.decide(db, principal, approval_id, decision=body.decision, reason=body.reason)
    )


# --- lab policies ---------------------------------------------------------------------------------------------------


@router.get("/lab-policies/baseline")
def baseline_policy(_: Principal = Depends(require("policy:read"))) -> dict[str, Any]:
    return lab_policy.baseline()


@router.get("/lab-policies", response_model=list[LabPolicyOut])
def list_lab_policies(
    principal: Principal = Depends(require("policy:read")), db: Session = Depends(get_db)
) -> list[LabPolicyOut]:
    return [LabPolicyOut.model_validate(p) for p in lab_policy.list_policies(db, principal.organization_id)]


@router.post("/lab-policies", response_model=LabPolicyOut, status_code=201)
def create_lab_policy(
    body: PolicyIn, principal: Principal = Depends(require("admin:policy")), db: Session = Depends(get_db)
) -> LabPolicyOut:
    policy, _ = lab_policy.create_policy(
        db,
        principal,
        key=body.key,
        name=body.name,
        description=body.description,
        document=body.document,
        change_note=body.change_note,
    )
    return LabPolicyOut.model_validate(policy)


@router.get("/lab-policies/{policy_id}")
def get_lab_policy(
    policy_id: uuid.UUID, principal: Principal = Depends(require("policy:read")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    policy = get_scoped(db, principal, LabPolicy, policy_id, label="Policy")
    versions = db.scalars(
        select(LabPolicyVersion).where(LabPolicyVersion.policy_id == policy.id).order_by(LabPolicyVersion.version)
    ).all()
    return {
        **LabPolicyOut.model_validate(policy).model_dump(),
        "versions": [
            {
                "id": str(v.id),
                "version": v.version,
                "document": v.document,
                "fingerprint": v.fingerprint,
                "change_note": v.change_note,
                "created_at": v.created_at.isoformat(),
            }
            for v in versions
        ],
    }


@router.post("/lab-policies/{policy_id}/versions", status_code=201)
def create_lab_policy_version(
    policy_id: uuid.UUID,
    body: PolicyVersionIn,
    principal: Principal = Depends(require("admin:policy")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    policy = get_scoped(db, principal, LabPolicy, policy_id, label="Policy")
    version = lab_policy.add_version(db, principal, policy, document=body.document, change_note=body.change_note)
    return {"id": str(version.id), "version": version.version, "fingerprint": version.fingerprint}


@router.post("/lab-policies/{policy_id}/status", response_model=LabPolicyOut)
def set_lab_policy_status(
    policy_id: uuid.UUID,
    body: PolicyStatusIn,
    principal: Principal = Depends(require("admin:policy")),
    db: Session = Depends(get_db),
) -> LabPolicyOut:
    policy = get_scoped(db, principal, LabPolicy, policy_id, label="Policy")
    return LabPolicyOut.model_validate(lab_policy.set_status(db, principal, policy, body.status))


@router.post("/lab-policies/simulate")
def simulate_policy(
    body: PolicySimulateIn, principal: Principal = Depends(require("policy:read")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    return lab_policy.simulate(db, principal.organization_id, body.action, body.facts)


# --- tools / MCP ----------------------------------------------------------------------------------------------------


@router.get("/tools")
def list_tools(
    principal: Principal = Depends(require("tool:read")), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    out = [
        {
            "name": t.name,
            "description": t.description,
            "risk": t.risk,
            "source": "builtin",
            "network": t.network,
            "parameters": t.parameters,
        }
        for t in BUILTIN_TOOLS.values()
    ]
    for server, tool in mcp.available_tools(db, principal.organization_id, None):
        out.append(
            {
                "name": f"mcp:{server.name}.{tool.name}",
                "description": tool.description,
                "risk": tool.risk_level,
                "source": "mcp",
                "network": True,
                "parameters": tool.input_schema,
            }
        )
    return out


@router.get("/tool-calls", response_model=Page[ToolCallOut])
def list_tool_calls(
    params: PageParams = Depends(),
    mission_id: uuid.UUID | None = Query(default=None),
    agent_run_id: uuid.UUID | None = Query(default=None),
    principal: Principal = Depends(require("tool:read")),
    db: Session = Depends(get_db),
) -> Page[ToolCallOut]:
    stmt = select(LabToolCall).where(LabToolCall.organization_id == principal.organization_id)
    if mission_id:
        stmt = stmt.where(LabToolCall.mission_id == mission_id)
    if agent_run_id:
        stmt = stmt.where(LabToolCall.agent_run_id == agent_run_id)
    return paginate(db, stmt.order_by(LabToolCall.created_at.desc()), params, ToolCallOut.model_validate)


@router.get("/mcp/servers", response_model=list[MCPServerOut])
def list_mcp_servers(
    principal: Principal = Depends(require("mcp:read")), db: Session = Depends(get_db)
) -> list[MCPServerOut]:
    rows = db.scalars(
        select(MCPServer).where(MCPServer.organization_id == principal.organization_id).order_by(MCPServer.name)
    ).all()
    return [MCPServerOut.model_validate(s) for s in rows]


@router.post("/mcp/servers", response_model=MCPServerOut, status_code=201)
def register_mcp_server(
    body: MCPServerIn, principal: Principal = Depends(require("mcp:manage")), db: Session = Depends(get_db)
) -> MCPServerOut:
    server = mcp.register(
        db,
        principal,
        name=body.name,
        endpoint=body.endpoint,
        auth_method=body.auth_method,
        auth_header=body.auth_header,
        secret_value=body.credential,
        risk_level=body.risk_level,
        allowed_tools=body.allowed_tools,
        project_ids=body.project_ids,
    )
    return MCPServerOut.model_validate(server)


@router.post("/mcp/servers/{server_id}/review", response_model=MCPServerOut)
def review_mcp_server(
    server_id: uuid.UUID,
    body: MCPToolApproveIn,
    principal: Principal = Depends(require("mcp:manage")),
    db: Session = Depends(get_db),
) -> MCPServerOut:
    return MCPServerOut.model_validate(mcp.review_server(db, principal, server_id, approve=body.approve))


@router.post("/mcp/servers/{server_id}/disable", response_model=MCPServerOut)
def disable_mcp_server(
    server_id: uuid.UUID, principal: Principal = Depends(require("mcp:manage")), db: Session = Depends(get_db)
) -> MCPServerOut:
    return MCPServerOut.model_validate(mcp.set_disabled(db, principal, server_id))


@router.post("/mcp/servers/{server_id}/discover")
def discover_mcp_tools(
    server_id: uuid.UUID, principal: Principal = Depends(require("mcp:manage")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    server = get_scoped(db, principal, MCPServer, server_id, label="MCP server")
    db.commit()  # the discovery performs network I/O in its own short transactions
    return mcp.discover(principal.organization_id, server.id)


@router.get("/mcp/servers/{server_id}/tools", response_model=list[MCPToolOut])
def list_mcp_tools(
    server_id: uuid.UUID, principal: Principal = Depends(require("mcp:read")), db: Session = Depends(get_db)
) -> list[MCPToolOut]:
    server = get_scoped(db, principal, MCPServer, server_id, label="MCP server")
    return [
        MCPToolOut.model_validate(t)
        for t in db.scalars(select(MCPTool).where(MCPTool.server_id == server.id).order_by(MCPTool.name)).all()
    ]


@router.post("/mcp/tools/{tool_id}/approval", response_model=MCPToolOut)
def approve_mcp_tool(
    tool_id: uuid.UUID,
    body: MCPToolApproveIn,
    principal: Principal = Depends(require("mcp:manage")),
    db: Session = Depends(get_db),
) -> MCPToolOut:
    return MCPToolOut.model_validate(
        mcp.approve_tool(db, principal, tool_id, approve=body.approve, risk_level=body.risk_level)
    )


# --- models / routing -------------------------------------------------------------------------------------------------


@router.get("/models")
def list_models(principal: Principal = Depends(require("model:read")), db: Session = Depends(get_db)) -> dict[str, Any]:
    overrides = db.scalars(
        select(ModelConfig).where(
            or_(ModelConfig.organization_id.is_(None), ModelConfig.organization_id == principal.organization_id)
        )
    ).all()
    registry = get_registry(
        [
            {
                "provider": o.provider,
                "model": o.model,
                "tier": o.tier,
                "features": o.features or None,
                "input_per_mtok": o.input_per_mtok,
                "output_per_mtok": o.output_per_mtok,
                "typical_latency_ms": o.typical_latency_ms,
                "is_agent": o.is_agent,
                "enabled": o.enabled,
            }
            for o in overrides
        ]
    )
    quota = quota_service.get_quota(db, principal.organization_id)
    return {
        "providers": sorted(registry.providers),
        "external_models_allowed": bool(quota.allow_external_models),
        "candidates": [
            {
                "provider": c.provider,
                "model": c.model,
                "tier": c.tier.value,
                "features": sorted(c.features),
                "input_per_mtok": c.input_per_mtok,
                "output_per_mtok": c.output_per_mtok,
                "data_leaves_organization": c.data_leaves_organization,
                "is_agent": c.is_agent,
            }
            for c in registry.candidates
        ],
        "organization_overrides": [
            {"id": str(o.id), "provider": o.provider, "model": o.model, "tier": o.tier, "enabled": o.enabled}
            for o in overrides
            if o.organization_id is not None
        ],
    }


@router.post("/models", status_code=201)
def upsert_model_config(
    body: ModelConfigIn, principal: Principal = Depends(require("model:manage")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    row = db.scalar(
        select(ModelConfig).where(
            ModelConfig.organization_id == principal.organization_id,
            ModelConfig.provider == body.provider,
            ModelConfig.model == body.model,
            ModelConfig.tier == body.tier,
        )
    )
    if row is None:
        row = ModelConfig(organization_id=principal.organization_id, **body.model_dump())
        db.add(row)
    else:
        for key, value in body.model_dump().items():
            setattr(row, key, value)
    db.flush()
    audit_log.record(
        db,
        organization_id=principal.organization_id,
        action="lab.model_config.upserted",
        resource_type="model_config",
        resource_id=row.id,
        principal=principal,
        after=body.model_dump(),
    )
    return {"id": str(row.id), **body.model_dump()}


@router.post("/models/route-preview")
def route_preview(
    body: RoutePreviewIn, principal: Principal = Depends(require("model:read")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    quota = quota_service.get_quota(db, principal.organization_id)
    registry = get_registry()
    decision = ModelRouter(registry.available_candidates()).route(
        body.task_type,
        body.complexity,
        body.latency_budget_ms,
        body.cost_budget_usd,
        RoutingPolicy(allow_external=bool(quota.allow_external_models)),
    )
    return {
        "task_type": decision.task_type,
        "tier": decision.tier.value,
        "model": decision.candidate.key if decision.candidate else None,
        "reason": decision.reason,
        "estimated_cost_usd": decision.estimated_cost_usd,
        "alternatives": decision.alternatives,
        "rejected": [{"model": k, "reason": r} for k, r in decision.rejected],
    }


# --- usage / billing ----------------------------------------------------------------------------------------------------


@router.get("/usage")
def usage_summary(
    days: int = Query(default=30, ge=1, le=366),
    project_id: uuid.UUID | None = Query(default=None),
    mission_id: uuid.UUID | None = Query(default=None),
    principal: Principal = Depends(require("usage:read")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    since = utcnow() - timedelta(days=days)
    out = usage.summary(db, principal.organization_id, project_id=project_id, mission_id=mission_id, since=since)
    out["organization_quota_check"] = usage.check_org_quota(db, principal.organization_id).to_dict()
    return out


@router.get("/billing")
def billing_summary(
    principal: Principal = Depends(require("billing:view")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    records = billing.usage_records(db, principal.organization_id)
    return {
        **billing.subscription(db, principal.organization_id),
        "usage_records": [
            {
                "metric": r.metric,
                "quantity": r.quantity,
                "unit": r.unit,
                "cost_usd": r.cost_usd,
                "period_start": r.period_start.isoformat(),
                "period_end": r.period_end.isoformat(),
            }
            for r in records
        ],
    }


# --- webhooks --------------------------------------------------------------------------------------------------------


@router.get("/webhooks", response_model=list[WebhookOut])
def list_webhooks(
    principal: Principal = Depends(require("webhook:manage")), db: Session = Depends(get_db)
) -> list[WebhookOut]:
    rows = db.scalars(
        select(Webhook).where(Webhook.organization_id == principal.organization_id).order_by(Webhook.created_at)
    ).all()
    return [WebhookOut.model_validate(w) for w in rows]


@router.get("/webhooks/events")
def webhook_event_names(_: Principal = Depends(require("webhook:manage"))) -> list[str]:
    return sorted(set(WEBHOOK_EVENT_NAMES.values()))


@router.post("/webhooks", status_code=201)
def create_webhook(
    body: WebhookIn, principal: Principal = Depends(require("webhook:manage")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    hook, secret = webhook_service.create_webhook(
        db,
        organization_id=principal.organization_id,
        url=body.url,
        events=body.events,
        description=body.description,
        created_by_id=principal.user_id if principal.is_human else None,
    )
    audit_log.record(
        db,
        organization_id=principal.organization_id,
        action="webhook.created",
        resource_type="webhook",
        resource_id=hook.id,
        principal=principal,
        after={"url": hook.url, "events": hook.events},
    )
    return {
        **WebhookOut.model_validate(hook).model_dump(),
        "signing_secret": secret,
        "note": "Store the signing secret now; it is not shown again. Verify the Aegis-Signature header.",
    }


@router.post("/webhooks/{webhook_id}/rotate-secret")
def rotate_webhook_secret(
    webhook_id: uuid.UUID, principal: Principal = Depends(require("webhook:manage")), db: Session = Depends(get_db)
) -> dict[str, str]:
    hook = get_scoped(db, principal, Webhook, webhook_id, label="Webhook")
    secret = webhook_service.rotate_secret(db, hook)
    audit_log.record(
        db,
        organization_id=principal.organization_id,
        action="webhook.secret_rotated",
        resource_type="webhook",
        resource_id=hook.id,
        principal=principal,
    )
    return {"id": str(hook.id), "signing_secret": secret}


@router.delete("/webhooks/{webhook_id}", response_model=Message)
def delete_webhook(
    webhook_id: uuid.UUID, principal: Principal = Depends(require("webhook:manage")), db: Session = Depends(get_db)
) -> Message:
    hook = get_scoped(db, principal, Webhook, webhook_id, label="Webhook")
    hook.active = False
    audit_log.record(
        db,
        organization_id=principal.organization_id,
        action="webhook.disabled",
        resource_type="webhook",
        resource_id=hook.id,
        principal=principal,
    )
    return Message(message="Webhook disabled")


@router.get("/webhooks/{webhook_id}/deliveries")
def webhook_deliveries(
    webhook_id: uuid.UUID, principal: Principal = Depends(require("webhook:manage")), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    hook = get_scoped(db, principal, Webhook, webhook_id, label="Webhook")
    rows = db.scalars(
        select(WebhookDelivery)
        .where(WebhookDelivery.webhook_id == hook.id)
        .order_by(WebhookDelivery.created_at.desc())
        .limit(100)
    ).all()
    return [
        {
            "id": str(d.id),
            "event_type": d.event_type,
            "event_id": d.event_id,
            "status": d.status,
            "attempts": d.attempts,
            "response_status": d.response_status,
            "last_error": d.last_error,
            "delivered_at": d.delivered_at.isoformat() if d.delivered_at else None,
            "created_at": d.created_at.isoformat(),
        }
        for d in rows
    ]


# --- benchmarks ------------------------------------------------------------------------------------------------------


@router.get("/benchmarks/suites")
def benchmark_suites(_: Principal = Depends(require("benchmark:read"))) -> list[dict[str, Any]]:
    return benchmarks.catalog()


@router.post("/benchmarks/runs", response_model=Accepted, status_code=202)
def start_benchmark(
    body: BenchmarkIn,
    idem: Idempotency = Depends(idempotency),
    principal: Principal = Depends(require("benchmark:run")),
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limited("model")),
) -> Any:
    if idem.replay_response is not None:
        return idem.replay_response
    run = benchmarks.create_run(db, principal, suite_key=body.suite_key, subject_ref=body.subject_ref)
    wf = workflow_client.start(
        db,
        organization_id=principal.organization_id,
        workflow="benchmark",
        business_key=f"benchmark:{run.id}",
        payload={"benchmark_run_id": str(run.id), "project_id": body.project_id},
        principal=principal,
    )
    payload = Accepted(id=str(run.id), status=run.status, workflow_run_id=str(wf.id)).model_dump()
    idem.complete(db, 202, payload)
    return payload


@router.get("/benchmarks/runs", response_model=list[BenchmarkRunOut])
def list_benchmark_runs(
    suite_key: str | None = Query(default=None, max_length=64),
    principal: Principal = Depends(require("benchmark:read")),
    db: Session = Depends(get_db),
) -> list[BenchmarkRunOut]:
    return [
        BenchmarkRunOut.model_validate(r)
        for r in db.scalars(benchmarks.list_runs(db, principal, suite_key=suite_key).limit(100)).all()
    ]


@router.get("/benchmarks/runs/{run_id}")
def get_benchmark_run(
    run_id: uuid.UUID, principal: Principal = Depends(require("benchmark:read")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    run = get_scoped(db, principal, BenchmarkRun, run_id, label="Benchmark run")
    return {
        **BenchmarkRunOut.model_validate(run).model_dump(),
        "results": [
            {"case_id": r.case_id, "score": r.score, "passed": r.passed, "details": r.details}
            for r in benchmarks.results(db, run)
        ],
    }


# --- audit / admin ---------------------------------------------------------------------------------------------------


@router.get("/audit")
def lab_audit(
    params: PageParams = Depends(),
    action_prefix: str | None = Query(default="lab.", max_length=60),
    principal: Principal = Depends(require("audit:read")),
    db: Session = Depends(get_db),
) -> Any:
    stmt = select(AuditLog).where(AuditLog.organization_id == principal.organization_id)
    if action_prefix:
        stmt = stmt.where(AuditLog.action.startswith(action_prefix))

    def mapper(a: AuditLog) -> dict[str, Any]:
        return {
            "id": str(a.id),
            "action": a.action,
            "actor_type": a.actor_type,
            "actor_label": a.actor_label,
            "resource_type": a.resource_type,
            "resource_id": a.resource_id,
            "before": a.before,
            "after": a.after,
            "request_id": a.request_id,
            "created_at": a.created_at.isoformat(),
        }

    return paginate(db, stmt.order_by(AuditLog.created_at.desc()), params, mapper)


@router.post("/admin/retention/run")
def run_retention(
    principal: Principal = Depends(require("org:manage")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    principal.require_human("retention run")
    result = retention.apply(db, principal.organization_id)
    audit_log.record(
        db,
        organization_id=principal.organization_id,
        action="lab.retention.run",
        resource_type="organization",
        resource_id=principal.organization_id,
        principal=principal,
        after=result,
    )
    return result


@router.post("/admin/billing/rollup")
def run_rollup(
    principal: Principal = Depends(require("billing:manage")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    return billing.rollup_day(db, principal.organization_id, utcnow())


@router.get("/admin/workflow-engine")
def workflow_engine_info(_: Principal = Depends(require("org:manage"))) -> dict[str, Any]:
    from aegis_api.config import get_settings
    from aegis_api.workflows.definitions import WORKFLOWS

    s = get_settings()
    return {
        "engine": s.effective_workflow_engine,
        "task_queue": s.temporal_task_queue if s.effective_workflow_engine == "temporal" else None,
        "workflows": [
            {"name": d.name, "description": d.description, "timeout_seconds": d.execution_timeout_seconds}
            for d in WORKFLOWS.values()
        ],
    }
