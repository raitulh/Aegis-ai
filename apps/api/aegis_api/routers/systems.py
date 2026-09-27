"""AI system and provider endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis_api.deps import get_db, require
from aegis_api.models import AISystem, Audit, Provider, SystemEvent, SystemVersion
from aegis_api.routers._helpers import paginate
from aegis_api.schemas.audits import AuditSummary
from aegis_api.schemas.common import Message, Page, PageParams
from aegis_api.schemas.systems import (
    ChangeImpact,
    ConnectionTestResult,
    ProviderCreate,
    ProviderOut,
    SystemCreate,
    SystemEventOut,
    SystemOut,
    SystemSummary,
    SystemUpdate,
    SystemVersionOut,
)
from aegis_api.security.context import Principal
from aegis_api.services import system_service

router = APIRouter(prefix="/api/v1", tags=["Systems"])


def _provider_out(p: Provider) -> ProviderOut:
    out = ProviderOut.model_validate(p)
    out.has_credentials = p.secret_id is not None
    return out


# --- providers ---------------------------------------------------------------------------------
@router.get("/providers", response_model=list[ProviderOut])
def list_providers(
    principal: Principal = Depends(require("providers:read")), db: Session = Depends(get_db)
) -> list[ProviderOut]:
    providers = db.scalars(
        select(Provider).where(Provider.organization_id == principal.organization_id).order_by(Provider.created_at)
    ).all()
    return [_provider_out(p) for p in providers]


@router.post("/providers", response_model=ProviderOut, status_code=201)
def create_provider(
    body: ProviderCreate, principal: Principal = Depends(require("providers:manage")), db: Session = Depends(get_db)
) -> ProviderOut:
    return _provider_out(system_service.create_provider(db, principal, body))


@router.post("/providers/{provider_id}/test", response_model=ConnectionTestResult)
def test_provider(
    provider_id: uuid.UUID, principal: Principal = Depends(require("providers:manage")), db: Session = Depends(get_db)
) -> ConnectionTestResult:
    provider = db.get(Provider, provider_id)
    if provider is None or provider.organization_id != principal.organization_id:
        from aegis_api.errors import NotFound

        raise NotFound("Provider not found")
    return ConnectionTestResult(**system_service.test_provider(db, provider))


@router.delete("/providers/{provider_id}", response_model=Message)
def delete_provider(
    provider_id: uuid.UUID, principal: Principal = Depends(require("providers:manage")), db: Session = Depends(get_db)
) -> Message:
    provider = db.get(Provider, provider_id)
    if provider and provider.organization_id == principal.organization_id:
        db.delete(provider)
    return Message(message="Provider removed")


# --- systems -----------------------------------------------------------------------------------
@router.get("/systems", response_model=Page[SystemSummary])
def list_systems(
    params: PageParams = Depends(),
    environment: str | None = Query(None),
    system_type: str | None = Query(None),
    q: str | None = Query(None),
    principal: Principal = Depends(require("systems:read")),
    db: Session = Depends(get_db),
) -> Page[SystemSummary]:
    stmt = select(AISystem).where(AISystem.organization_id == principal.organization_id, AISystem.deleted_at.is_(None))
    if environment:
        stmt = stmt.where(AISystem.environment == environment)
    if system_type:
        stmt = stmt.where(AISystem.system_type == system_type)
    if q:
        stmt = stmt.where(AISystem.name.ilike(f"%{q}%"))
    return paginate(db, stmt.order_by(AISystem.created_at.desc()), params, SystemSummary.model_validate)


@router.post("/systems", response_model=SystemOut, status_code=201)
def create_system(
    body: SystemCreate, principal: Principal = Depends(require("systems:write")), db: Session = Depends(get_db)
) -> SystemOut:
    return SystemOut.model_validate(system_service.create_system(db, principal, body))


@router.get("/systems/{system_id}", response_model=SystemOut)
def get_system(
    system_id: uuid.UUID, principal: Principal = Depends(require("systems:read")), db: Session = Depends(get_db)
) -> SystemOut:
    return SystemOut.model_validate(system_service.get_system(db, system_id, principal.organization_id))


@router.patch("/systems/{system_id}", response_model=SystemOut)
def update_system(
    system_id: uuid.UUID,
    body: SystemUpdate,
    principal: Principal = Depends(require("systems:write")),
    db: Session = Depends(get_db),
) -> SystemOut:
    system = system_service.get_system(db, system_id, principal.organization_id)
    updated, _ = system_service.update_system(db, system, body, principal)
    return SystemOut.model_validate(updated)


@router.post("/systems/{system_id}/change-impact", response_model=ChangeImpact)
def preview_change_impact(
    system_id: uuid.UUID,
    body: SystemUpdate,
    principal: Principal = Depends(require("systems:read")),
    db: Session = Depends(get_db),
) -> ChangeImpact:
    system = system_service.get_system(db, system_id, principal.organization_id)
    payload = body.model_dump(exclude_unset=True)
    changed = [f for f in payload if f != "change_note" and getattr(system, f, None) != payload[f]]
    db.rollback()
    return ChangeImpact(**system_service.change_impact(db, system, changed))


@router.delete("/systems/{system_id}", response_model=Message)
def delete_system(
    system_id: uuid.UUID, principal: Principal = Depends(require("systems:delete")), db: Session = Depends(get_db)
) -> Message:
    system = system_service.get_system(db, system_id, principal.organization_id)
    system_service.delete_system(db, system, principal)
    return Message(message="System archived")


@router.get("/systems/{system_id}/versions", response_model=list[SystemVersionOut])
def system_versions(
    system_id: uuid.UUID, principal: Principal = Depends(require("systems:read")), db: Session = Depends(get_db)
) -> list[SystemVersionOut]:
    system_service.get_system(db, system_id, principal.organization_id)
    versions = db.scalars(
        select(SystemVersion).where(SystemVersion.system_id == system_id).order_by(SystemVersion.created_at.desc())
    ).all()
    return [SystemVersionOut.model_validate(v) for v in versions]


@router.get("/systems/{system_id}/events", response_model=list[SystemEventOut])
def system_events(
    system_id: uuid.UUID, principal: Principal = Depends(require("systems:read")), db: Session = Depends(get_db)
) -> list[SystemEventOut]:
    system_service.get_system(db, system_id, principal.organization_id)
    events = db.scalars(
        select(SystemEvent).where(SystemEvent.system_id == system_id).order_by(SystemEvent.occurred_at.desc())
    ).all()
    return [SystemEventOut.model_validate(e) for e in events]


@router.get("/systems/{system_id}/audits", response_model=list[AuditSummary])
def system_audits(
    system_id: uuid.UUID, principal: Principal = Depends(require("audits:read")), db: Session = Depends(get_db)
) -> list[AuditSummary]:
    system_service.get_system(db, system_id, principal.organization_id)
    audits = db.scalars(select(Audit).where(Audit.system_id == system_id).order_by(Audit.created_at.desc())).all()
    return [AuditSummary.model_validate(a) for a in audits]
