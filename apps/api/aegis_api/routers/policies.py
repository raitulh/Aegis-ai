"""Policy, control and framework endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis_api.deps import get_db, require
from aegis_api.errors import NotFound, ValidationFailed
from aegis_api.models import (
    Control,
    ControlMapping,
    Framework,
    FrameworkControl,
    Policy,
    PolicyRequirement,
    PolicyVersion,
)
from aegis_api.routers._helpers import paginate
from aegis_api.schemas.common import Page, PageParams
from aegis_api.schemas.policies import (
    CompileResultOut,
    ControlMappingOut,
    ControlOut,
    FrameworkOut,
    ManualControlCreate,
    PolicyCreate,
    PolicyDiffOut,
    PolicyOut,
    PolicyVersionCreate,
    PolicyVersionOut,
    RequirementOut,
)
from aegis_api.security.context import Principal
from aegis_api.security.uploads import validate_upload
from aegis_api.services import policy_service

router = APIRouter(prefix="/api/v1", tags=["Policies"])


@router.get("/policies", response_model=Page[PolicyOut])
def list_policies(
    params: PageParams = Depends(),
    principal: Principal = Depends(require("policies:read")),
    db: Session = Depends(get_db),
) -> Page[PolicyOut]:
    stmt = select(Policy).where(Policy.organization_id == principal.organization_id).order_by(Policy.created_at.desc())
    return paginate(db, stmt, params, PolicyOut.model_validate)


@router.post("/policies", response_model=PolicyOut, status_code=201)
def create_policy(
    body: PolicyCreate, principal: Principal = Depends(require("policies:write")), db: Session = Depends(get_db)
) -> PolicyOut:
    policy, _ = policy_service.create_policy(
        db,
        principal,
        name=body.name,
        key=body.key,
        description=body.description,
        category=body.category,
        owner_name=body.owner_name,
        source_text=body.source_text,
    )
    return PolicyOut.model_validate(policy)


@router.get("/policies/{policy_id}", response_model=PolicyOut)
def get_policy(
    policy_id: uuid.UUID, principal: Principal = Depends(require("policies:read")), db: Session = Depends(get_db)
) -> PolicyOut:
    return PolicyOut.model_validate(policy_service.get_policy(db, policy_id, principal.organization_id))


@router.get("/policies/{policy_id}/versions", response_model=list[PolicyVersionOut])
def policy_versions(
    policy_id: uuid.UUID, principal: Principal = Depends(require("policies:read")), db: Session = Depends(get_db)
) -> list[PolicyVersionOut]:
    policy_service.get_policy(db, policy_id, principal.organization_id)
    versions = db.scalars(
        select(PolicyVersion).where(PolicyVersion.policy_id == policy_id).order_by(PolicyVersion.created_at)
    ).all()
    return [PolicyVersionOut.model_validate(v) for v in versions]


@router.post("/policies/{policy_id}/upload", response_model=PolicyVersionOut)
async def upload_document(
    policy_id: uuid.UUID,
    file: UploadFile = File(...),
    principal: Principal = Depends(require("policies:write")),
    db: Session = Depends(get_db),
) -> PolicyVersionOut:
    policy = policy_service.get_policy(db, policy_id, principal.organization_id)
    version = db.get(PolicyVersion, policy.current_version_id) if policy.current_version_id else None
    if version is None or version.status == "compiled":
        version = policy_service.create_version(
            db, principal, policy, source_text=None, dsl_yaml=None, change_note="Document upload"
        )
        policy.current_version_id = version.id
    data = await file.read()
    upload = validate_upload(file.filename or "policy", file.content_type, data)
    policy_service.attach_document(db, principal, version, upload)
    return PolicyVersionOut.model_validate(version)


@router.post(
    "/policies/{policy_id}/compile",
    response_model=CompileResultOut,
    dependencies=[Depends(require("policies:compile"))],
)
def compile_policy(
    policy_id: uuid.UUID, principal: Principal = Depends(require("policies:compile")), db: Session = Depends(get_db)
) -> CompileResultOut:
    policy = policy_service.get_policy(db, policy_id, principal.organization_id)
    if not policy.current_version_id:
        raise ValidationFailed("Policy has no version to compile")
    version = policy_service.compile_version(db, policy.current_version_id)
    requirements = db.scalars(select(PolicyRequirement).where(PolicyRequirement.policy_version_id == version.id)).all()
    controls = db.scalars(select(Control).where(Control.policy_version_id == version.id)).all()
    return CompileResultOut(
        policy_version_id=str(version.id),
        requirements=[RequirementOut.model_validate(r) for r in requirements],
        controls=[ControlOut.model_validate(c) for c in controls],
        report=version.compile_report,
    )


@router.post("/policies/{policy_id}/version", response_model=PolicyVersionOut, status_code=201)
def new_version(
    policy_id: uuid.UUID,
    body: PolicyVersionCreate,
    principal: Principal = Depends(require("policies:write")),
    db: Session = Depends(get_db),
) -> PolicyVersionOut:
    policy = policy_service.get_policy(db, policy_id, principal.organization_id)
    version = policy_service.create_version(
        db, principal, policy, source_text=body.source_text, dsl_yaml=body.dsl_yaml, change_note=body.change_note
    )
    policy.current_version_id = version.id
    return PolicyVersionOut.model_validate(version)


@router.get("/policies/{policy_id}/diff", response_model=PolicyDiffOut)
def diff(
    policy_id: uuid.UUID,
    from_version: uuid.UUID = Query(...),
    to_version: uuid.UUID = Query(...),
    principal: Principal = Depends(require("policies:read")),
    db: Session = Depends(get_db),
) -> PolicyDiffOut:
    policy = policy_service.get_policy(db, policy_id, principal.organization_id)
    return PolicyDiffOut(**policy_service.diff_versions(db, policy, from_version, to_version))


@router.get("/policies/{policy_id}/controls", response_model=list[ControlOut])
def policy_controls(
    policy_id: uuid.UUID, principal: Principal = Depends(require("policies:read")), db: Session = Depends(get_db)
) -> list[ControlOut]:
    policy = policy_service.get_policy(db, policy_id, principal.organization_id)
    controls = db.scalars(
        select(Control).where(Control.policy_id == policy_id, Control.policy_version_id == policy.current_version_id)
    ).all()
    return [ControlOut.model_validate(c) for c in controls]


@router.post("/policies/{policy_id}/controls", response_model=ControlOut, status_code=201)
def add_manual_control(
    policy_id: uuid.UUID,
    body: ManualControlCreate,
    principal: Principal = Depends(require("policies:write")),
    db: Session = Depends(get_db),
) -> ControlOut:
    policy = policy_service.get_policy(db, policy_id, principal.organization_id)
    from engines.policy.dsl import DOMAIN_BY_TEST_TYPE

    control = Control(
        organization_id=principal.organization_id,
        policy_id=policy.id,
        policy_version_id=policy.current_version_id,
        control_id=body.control_id,
        name=body.name,
        description=body.description,
        domain=DOMAIN_BY_TEST_TYPE.get(body.test_type, "governance"),
        test_type=body.test_type,
        threshold=body.threshold,
        severity=body.severity,
        condition=body.condition,
        required_evidence=body.required_evidence,
        source="manual",
        created_by_id=principal.user_id,
    )
    db.add(control)
    db.flush()
    policy_service._auto_map(db, control)
    return ControlOut.model_validate(control)


@router.get("/controls", response_model=Page[ControlOut])
def list_controls(
    params: PageParams = Depends(),
    principal: Principal = Depends(require("policies:read")),
    db: Session = Depends(get_db),
) -> Page[ControlOut]:
    stmt = (
        select(Control)
        .where(Control.organization_id == principal.organization_id, Control.status == "active")
        .order_by(Control.control_id)
    )
    return paginate(db, stmt, params, ControlOut.model_validate)


@router.get("/controls/{control_id}/mappings", response_model=list[ControlMappingOut])
def control_mappings(
    control_id: uuid.UUID, principal: Principal = Depends(require("policies:read")), db: Session = Depends(get_db)
) -> list[ControlMappingOut]:
    control = db.get(Control, control_id)
    if control is None or control.organization_id != principal.organization_id:
        raise NotFound("Control not found")
    mappings = db.scalars(select(ControlMapping).where(ControlMapping.control_id == control_id)).all()
    out = []
    for m in mappings:
        fc = db.get(FrameworkControl, m.framework_control_id)
        fw = db.get(Framework, fc.framework_id) if fc else None
        if fc and fw:
            out.append(
                ControlMappingOut(
                    control_id=str(control.id),
                    control_ref=control.control_id,
                    framework_key=fw.key,
                    framework_ref=fc.ref,
                    framework_title=fc.title,
                    rationale=m.rationale,
                    confidence=m.confidence,
                    mapping_type=m.mapping_type,
                )
            )
    return out


@router.get("/frameworks", response_model=list[FrameworkOut])
def list_frameworks(
    principal: Principal = Depends(require("frameworks:read")), db: Session = Depends(get_db)
) -> list[FrameworkOut]:
    frameworks = db.scalars(
        select(Framework)
        .where((Framework.organization_id.is_(None)) | (Framework.organization_id == principal.organization_id))
        .order_by(Framework.name)
    ).all()
    return [FrameworkOut.model_validate(f) for f in frameworks]


@router.get("/frameworks/{framework_id}", response_model=FrameworkOut)
def get_framework(
    framework_id: uuid.UUID, principal: Principal = Depends(require("frameworks:read")), db: Session = Depends(get_db)
) -> FrameworkOut:
    fw = db.get(Framework, framework_id)
    if fw is None or (fw.organization_id is not None and fw.organization_id != principal.organization_id):
        raise NotFound("Framework not found")
    return FrameworkOut.model_validate(fw)
