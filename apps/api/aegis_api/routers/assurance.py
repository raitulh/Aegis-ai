"""Continuous assurance endpoints: schedules, change triggers (CI/CD), baselines and regression reports."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis_api import idempotency
from aegis_api.deps import get_db, require
from aegis_api.errors import NotFound, ValidationFailed
from aegis_api.models import AssuranceSchedule, AssuranceTrigger
from aegis_api.routers._helpers import paginate
from aegis_api.schemas.common import Message, Page, PageParams
from aegis_api.schemas.platform import (
    BaselineUpdate,
    ScheduleCreate,
    ScheduleOut,
    ScheduleUpdate,
    TriggerCreate,
    TriggerOut,
)
from aegis_api.security.context import Principal
from aegis_api.services import assurance_service, audit_log, audit_service, system_service

router = APIRouter(prefix="/api/v1", tags=["Continuous Assurance"])


def _schedule(db: Session, schedule_id: uuid.UUID, org_id: uuid.UUID) -> AssuranceSchedule:
    schedule = db.get(AssuranceSchedule, schedule_id)
    if schedule is None or schedule.organization_id != org_id:
        raise NotFound("Schedule not found")
    return schedule


@router.get("/assurance/schedules", response_model=list[ScheduleOut])
def list_schedules(
    system_id: uuid.UUID | None = Query(None),
    principal: Principal = Depends(require("assurance:read")),
    db: Session = Depends(get_db),
) -> list[ScheduleOut]:
    stmt = select(AssuranceSchedule).where(AssuranceSchedule.organization_id == principal.organization_id)
    if system_id:
        stmt = stmt.where(AssuranceSchedule.system_id == system_id)
    return [ScheduleOut.model_validate(s) for s in db.scalars(stmt.order_by(AssuranceSchedule.created_at)).all()]


@router.post("/assurance/schedules", response_model=ScheduleOut, status_code=201)
def create_schedule(
    body: ScheduleCreate, principal: Principal = Depends(require("assurance:manage")), db: Session = Depends(get_db)
) -> ScheduleOut:
    return ScheduleOut.model_validate(assurance_service.create_schedule(db, principal, body))


@router.patch("/assurance/schedules/{schedule_id}", response_model=ScheduleOut)
def update_schedule(
    schedule_id: uuid.UUID,
    body: ScheduleUpdate,
    principal: Principal = Depends(require("assurance:manage")),
    db: Session = Depends(get_db),
) -> ScheduleOut:
    from datetime import timedelta

    from aegis_api.db.base import utcnow

    schedule = _schedule(db, schedule_id, principal.organization_id)
    changes = body.model_dump(exclude_unset=True)
    if "trigger_on" in changes:
        bad = [t for t in changes["trigger_on"] or [] if t not in assurance_service.EVENT_TYPES]
        if bad:
            raise ValidationFailed(f"Unknown trigger types: {', '.join(bad)}")
    for key, value in changes.items():
        setattr(schedule, key, value)
    if "interval_hours" in changes:
        schedule.next_run_at = utcnow() + timedelta(hours=schedule.interval_hours) if schedule.interval_hours else None
    audit_log.record(
        db,
        organization_id=principal.organization_id,
        action="assurance.schedule_updated",
        resource_type="assurance_schedule",
        resource_id=schedule.id,
        principal=principal,
        after=changes,
    )
    return ScheduleOut.model_validate(schedule)


@router.delete("/assurance/schedules/{schedule_id}", response_model=Message)
def delete_schedule(
    schedule_id: uuid.UUID, principal: Principal = Depends(require("assurance:manage")), db: Session = Depends(get_db)
) -> Message:
    schedule = _schedule(db, schedule_id, principal.organization_id)
    db.delete(schedule)
    audit_log.record(
        db,
        organization_id=principal.organization_id,
        action="assurance.schedule_deleted",
        resource_type="assurance_schedule",
        resource_id=schedule_id,
        principal=principal,
    )
    return Message(message="Schedule deleted")


@router.post("/assurance/triggers", response_model=TriggerOut, status_code=201)
def create_trigger(
    body: TriggerCreate,
    request: Request,
    principal: Principal = Depends(require("audits:run")),
    db: Session = Depends(get_db),
) -> Any:
    """Report a change (from CI/CD or your release tooling). Starts a risk-selected audit. Idempotent per
    (system, event_type, ref): re-sending the same deployment returns the original trigger."""
    handle = idempotency.begin(db, principal, request, body)
    if handle.replay is not None:
        return handle.replay
    record = assurance_service.trigger(
        db,
        principal,
        system_id=body.system_id,
        event_type=body.event_type,
        ref=body.ref,
        metadata=body.metadata,
        source="ci" if principal.auth_method == "api_key" else "api",
    )
    out = TriggerOut.model_validate(record)
    idempotency.complete(db, handle, 201, out)
    return out


@router.get("/assurance/triggers", response_model=Page[TriggerOut])
def list_triggers(
    params: PageParams = Depends(),
    system_id: uuid.UUID | None = Query(None),
    principal: Principal = Depends(require("assurance:read")),
    db: Session = Depends(get_db),
) -> Page[TriggerOut]:
    stmt = select(AssuranceTrigger).where(AssuranceTrigger.organization_id == principal.organization_id)
    if system_id:
        stmt = stmt.where(AssuranceTrigger.system_id == system_id)
    return paginate(db, stmt.order_by(AssuranceTrigger.created_at.desc()), params, TriggerOut.model_validate)


@router.get("/audits/{audit_id}/regression")
def audit_regression(
    audit_id: uuid.UUID, principal: Principal = Depends(require("audits:read")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    """Compare an audit with the previous audit of the same system and the known-good baseline."""
    audit = audit_service.get_audit(db, audit_id, principal.organization_id)
    return assurance_service.regression_report(db, audit)


@router.put("/systems/{system_id}/baseline")
def set_baseline(
    system_id: uuid.UUID,
    body: BaselineUpdate,
    principal: Principal = Depends(require("assurance:manage")),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    system = system_service.get_system(db, system_id, principal.organization_id)
    audit = audit_service.get_audit(db, uuid.UUID(body.audit_id), principal.organization_id)
    assurance_service.set_baseline(db, principal, system, audit)
    return {"system_id": str(system.id), "baseline_audit_id": body.audit_id}
