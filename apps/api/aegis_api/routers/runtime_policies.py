"""Policy Studio endpoints: runtime policies, versions, validation, testing, simulation, publishing,
rollback, assignments and the template library."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis_api.deps import get_db, require
from aegis_api.errors import NotFound, ValidationFailed
from aegis_api.models import RuntimePolicy, RuntimePolicyAssignment, RuntimePolicyVersion
from aegis_api.routers._helpers import paginate
from aegis_api.schemas.common import Message, Page, PageParams
from aegis_api.schemas.platform import (
    PolicySourceRequest,
    PolicyTestRequest,
    PublishRequest,
    RuntimePolicyAssignmentCreate,
    RuntimePolicyAssignmentOut,
    RuntimePolicyCreate,
    RuntimePolicyOut,
    RuntimePolicyVersionCreate,
    RuntimePolicyVersionOut,
    SimulationRequest,
)
from aegis_api.security.context import Principal
from aegis_api.services import audit_log
from aegis_api.services import runtime_policy_service as svc
from engines.runtime.policy import evaluate
from engines.runtime.schema import normalize
from engines.runtime.templates import TEMPLATES

router = APIRouter(prefix="/api/v1/runtime-policies", tags=["Policy Studio"])


@router.get("/templates")
def templates(principal: Principal = Depends(require("policies:read"))) -> list[dict[str, Any]]:
    """The built-in policy library. Templates are technical controls, not compliance attestations."""
    return [
        {"key": t.key, "name": t.name, "category": t.category, "summary": t.summary, "source_yaml": t.source}
        for t in TEMPLATES
    ]


@router.post("/validate")
def validate(body: PolicySourceRequest, principal: Principal = Depends(require("policies:read"))) -> dict[str, Any]:
    try:
        compiled = svc.validate_source(body.source_yaml)
    except ValidationFailed as exc:
        return {"valid": False, "errors": (exc.details or {}).get("errors", [exc.message])}
    return {"valid": True, "errors": [], "rules": len(compiled["rules"]), "compiled": compiled}


@router.post("/test")
def test_policy(body: PolicyTestRequest, principal: Principal = Depends(require("policies:read"))) -> dict[str, Any]:
    """Evaluate a policy source against one sample event (nothing is recorded)."""
    compiled = svc.validate_source(body.source_yaml)
    event = normalize(body.event)
    result = evaluate(event, [{"policy_id": None, "policy_key": "draft", "version": "draft", "compiled": compiled}])
    return {**result, "signals": event["signals"], "redacted_payload": event["payload"]}


@router.post("/simulate")
def simulate(
    body: SimulationRequest, principal: Principal = Depends(require("policies:read")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    """Replay recorded runtime events through a draft source or a stored version. Counts are real."""
    if body.source_yaml:
        compiled = svc.validate_source(body.source_yaml)
        key = compiled["name"]
    elif body.policy_id and body.version:
        policy = svc.get_policy(db, uuid.UUID(body.policy_id), principal.organization_id)
        version = svc.get_version(db, policy, body.version)
        compiled, key = version.compiled, policy.key
    else:
        raise ValidationFailed("Provide source_yaml, or policy_id and version")
    system_ids = [uuid.UUID(s) for s in body.system_ids] if body.system_ids else None
    result = svc.simulate(
        db, principal.organization_id, compiled, policy_key=key, system_ids=system_ids, days=body.days
    )
    audit_log.record(
        db,
        organization_id=principal.organization_id,
        action="policy.simulated",
        resource_type="runtime_policy",
        resource_id=body.policy_id,
        principal=principal,
        after={"events_evaluated": result["events_evaluated"], "blocked": result["blocked"]},
    )
    return result


@router.get("", response_model=Page[RuntimePolicyOut])
def list_policies(
    params: PageParams = Depends(),
    principal: Principal = Depends(require("policies:read")),
    db: Session = Depends(get_db),
) -> Page[RuntimePolicyOut]:
    stmt = (
        select(RuntimePolicy)
        .where(RuntimePolicy.organization_id == principal.organization_id)
        .order_by(RuntimePolicy.updated_at.desc())
    )
    return paginate(db, stmt, params, RuntimePolicyOut.model_validate)


@router.post("", response_model=RuntimePolicyOut, status_code=201)
def create_policy(
    body: RuntimePolicyCreate, principal: Principal = Depends(require("policies:write")), db: Session = Depends(get_db)
) -> RuntimePolicyOut:
    policy, _ = svc.create_policy(
        db,
        principal,
        name=body.name,
        source_yaml=body.source_yaml,
        template_key=body.template_key,
        key=body.key,
        description=body.description,
    )
    return RuntimePolicyOut.model_validate(policy)


@router.get("/{policy_id}", response_model=RuntimePolicyOut)
def get_policy(
    policy_id: uuid.UUID, principal: Principal = Depends(require("policies:read")), db: Session = Depends(get_db)
) -> RuntimePolicyOut:
    return RuntimePolicyOut.model_validate(svc.get_policy(db, policy_id, principal.organization_id))


@router.get("/{policy_id}/versions", response_model=list[RuntimePolicyVersionOut])
def list_versions(
    policy_id: uuid.UUID, principal: Principal = Depends(require("policies:read")), db: Session = Depends(get_db)
) -> list[RuntimePolicyVersionOut]:
    svc.get_policy(db, policy_id, principal.organization_id)
    rows = db.scalars(
        select(RuntimePolicyVersion)
        .where(RuntimePolicyVersion.policy_id == policy_id)
        .order_by(RuntimePolicyVersion.version.desc())
    ).all()
    return [RuntimePolicyVersionOut.model_validate(v) for v in rows]


@router.post("/{policy_id}/versions", response_model=RuntimePolicyVersionOut, status_code=201)
def create_version(
    policy_id: uuid.UUID,
    body: RuntimePolicyVersionCreate,
    principal: Principal = Depends(require("policies:write")),
    db: Session = Depends(get_db),
) -> RuntimePolicyVersionOut:
    policy = svc.get_policy(db, policy_id, principal.organization_id)
    return RuntimePolicyVersionOut.model_validate(
        svc.new_version(db, principal, policy, body.source_yaml, body.change_note)
    )


@router.get("/{policy_id}/diff")
def diff_versions(
    policy_id: uuid.UUID,
    from_version: int = Query(..., alias="from", ge=1),
    to_version: int = Query(..., alias="to", ge=1),
    principal: Principal = Depends(require("policies:read")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    policy = svc.get_policy(db, policy_id, principal.organization_id)
    return svc.diff(db, policy, from_version, to_version)


@router.post("/{policy_id}/publish", response_model=RuntimePolicyOut)
def publish(
    policy_id: uuid.UUID,
    body: PublishRequest,
    principal: Principal = Depends(require("policies:publish")),
    db: Session = Depends(get_db),
) -> RuntimePolicyOut:
    policy = svc.get_policy(db, policy_id, principal.organization_id)
    version = svc.get_version(db, policy, body.version)
    return RuntimePolicyOut.model_validate(svc.publish(db, principal, policy, version))


@router.post("/{policy_id}/rollback", response_model=RuntimePolicyOut)
def rollback(
    policy_id: uuid.UUID,
    body: PublishRequest,
    principal: Principal = Depends(require("policies:publish")),
    db: Session = Depends(get_db),
) -> RuntimePolicyOut:
    """Re-publish an earlier (immutable) version."""
    policy = svc.get_policy(db, policy_id, principal.organization_id)
    version = svc.get_version(db, policy, body.version)
    return RuntimePolicyOut.model_validate(svc.publish(db, principal, policy, version, rollback=True))


@router.post("/{policy_id}/disable", response_model=RuntimePolicyOut)
def disable(
    policy_id: uuid.UUID, principal: Principal = Depends(require("policies:publish")), db: Session = Depends(get_db)
) -> RuntimePolicyOut:
    policy = svc.get_policy(db, policy_id, principal.organization_id)
    return RuntimePolicyOut.model_validate(svc.set_enabled(db, principal, policy, False))


@router.post("/{policy_id}/enable", response_model=RuntimePolicyOut)
def enable(
    policy_id: uuid.UUID, principal: Principal = Depends(require("policies:publish")), db: Session = Depends(get_db)
) -> RuntimePolicyOut:
    policy = svc.get_policy(db, policy_id, principal.organization_id)
    return RuntimePolicyOut.model_validate(svc.set_enabled(db, principal, policy, True))


@router.post("/{policy_id}/clone", response_model=RuntimePolicyOut, status_code=201)
def clone(
    policy_id: uuid.UUID, principal: Principal = Depends(require("policies:write")), db: Session = Depends(get_db)
) -> RuntimePolicyOut:
    policy = svc.get_policy(db, policy_id, principal.organization_id)
    copy, _ = svc.clone(db, principal, policy)
    return RuntimePolicyOut.model_validate(copy)


@router.get("/{policy_id}/assignments", response_model=list[RuntimePolicyAssignmentOut])
def list_assignments(
    policy_id: uuid.UUID, principal: Principal = Depends(require("policies:read")), db: Session = Depends(get_db)
) -> list[RuntimePolicyAssignmentOut]:
    svc.get_policy(db, policy_id, principal.organization_id)
    rows = db.scalars(select(RuntimePolicyAssignment).where(RuntimePolicyAssignment.policy_id == policy_id)).all()
    return [RuntimePolicyAssignmentOut.model_validate(a) for a in rows]


@router.post("/{policy_id}/assignments", response_model=RuntimePolicyAssignmentOut, status_code=201)
def create_assignment(
    policy_id: uuid.UUID,
    body: RuntimePolicyAssignmentCreate,
    principal: Principal = Depends(require("policies:publish")),
    db: Session = Depends(get_db),
) -> RuntimePolicyAssignmentOut:
    policy = svc.get_policy(db, policy_id, principal.organization_id)
    assignment = svc.assign(
        db, principal, policy, scope_type=body.scope_type, system_id=body.system_id, environment=body.environment
    )
    return RuntimePolicyAssignmentOut.model_validate(assignment)


@router.delete("/{policy_id}/assignments/{assignment_id}", response_model=Message)
def delete_assignment(
    policy_id: uuid.UUID,
    assignment_id: uuid.UUID,
    principal: Principal = Depends(require("policies:publish")),
    db: Session = Depends(get_db),
) -> Message:
    svc.get_policy(db, policy_id, principal.organization_id)
    assignment = db.get(RuntimePolicyAssignment, assignment_id)
    if assignment is None or assignment.policy_id != policy_id:
        raise NotFound("Assignment not found")
    db.delete(assignment)
    audit_log.record(
        db,
        organization_id=principal.organization_id,
        action="policy.unassigned",
        resource_type="runtime_policy",
        resource_id=policy_id,
        principal=principal,
        before={"scope_type": assignment.scope_type, "scope_key": assignment.scope_key},
    )
    return Message(message="Assignment removed")
