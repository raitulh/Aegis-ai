"""Prompt registry service: built-in versioned templates plus organization-authored versions.

Resolution order for a template name: the organization's newest *active* version, else the platform built-in.
The platform ``system.policy`` template can never be overridden by organizations. Every agent run records the
template ref and SHA-256 it used, so prompts are reproducible and auditable.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis_api.errors import Conflict, ValidationFailed
from aegis_api.models.lab import PromptTemplateRecord
from aegis_api.security.context import Principal
from aegis_api.services import audit_log
from engines.lab.prompts.registry import PromptError, PromptTemplate, builtin_templates, latest

PROTECTED = frozenset({"system.policy"})


def _to_template(row: PromptTemplateRecord) -> PromptTemplate:
    return PromptTemplate(
        name=row.name,
        version=row.version,
        task_type=row.task_type,
        role=row.role,
        description=row.description or "",
        variables=list(row.variables or []),
        template=row.template,
        status=row.status,
    )


def _version_key(version: str) -> tuple[int, ...]:
    parts = []
    for p in version.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def resolve(db: Session, organization_id: uuid.UUID | None, name: str) -> PromptTemplate:
    if name not in PROTECTED and organization_id is not None:
        rows = db.scalars(
            select(PromptTemplateRecord).where(
                PromptTemplateRecord.organization_id == organization_id,
                PromptTemplateRecord.name == name,
                PromptTemplateRecord.status == "active",
            )
        ).all()
        if rows:
            return _to_template(max(rows, key=lambda r: _version_key(r.version)))
    return latest(name)


def catalog(db: Session, organization_id: uuid.UUID) -> list[dict[str, object]]:
    out: list[dict[str, object]] = [
        {
            "name": t.name,
            "version": t.version,
            "task_type": t.task_type,
            "role": t.role,
            "sha256": t.sha256,
            "source": "builtin",
            "status": t.status,
            "variables": t.variables,
        }
        for t in builtin_templates().values()
    ]
    for row in db.scalars(
        select(PromptTemplateRecord)
        .where(PromptTemplateRecord.organization_id == organization_id)
        .order_by(PromptTemplateRecord.name, PromptTemplateRecord.created_at)
    ).all():
        out.append(
            {
                "id": str(row.id),
                "name": row.name,
                "version": row.version,
                "task_type": row.task_type,
                "role": row.role,
                "sha256": row.sha256,
                "source": "organization",
                "status": row.status,
                "variables": row.variables,
            }
        )
    return out


def register(
    db: Session,
    principal: Principal,
    *,
    name: str,
    version: str,
    template: str,
    variables: list[str],
    description: str | None = None,
) -> PromptTemplateRecord:
    if name in PROTECTED:
        raise ValidationFailed(f"'{name}' is a protected platform template")
    try:
        base = latest(name)
    except (KeyError, PromptError) as exc:
        raise ValidationFailed(f"Unknown template '{name}'; organizations can only version built-in templates") from exc
    candidate = PromptTemplate(
        name=name,
        version=version,
        task_type=base.task_type,
        role=base.role,
        description=description or base.description,
        variables=variables,
        template=template,
    )
    try:
        candidate.check()
    except PromptError as exc:
        raise ValidationFailed(str(exc)) from exc
    if set(candidate.variables) != set(base.variables):
        raise ValidationFailed(
            f"Template variables must match the built-in contract: {sorted(base.variables)} (the agent runtime "
            "supplies exactly these)"
        )
    exists = db.scalar(
        select(PromptTemplateRecord.id).where(
            PromptTemplateRecord.organization_id == principal.organization_id,
            PromptTemplateRecord.name == name,
            PromptTemplateRecord.version == version,
        )
    )
    if exists:
        raise Conflict(f"{name}@{version} already exists; prompt versions are immutable")
    row = PromptTemplateRecord(
        organization_id=principal.organization_id,
        name=name,
        version=version,
        task_type=candidate.task_type,
        role=candidate.role,
        description=candidate.description,
        template=template,
        variables=candidate.variables,
        status="active",
        sha256=candidate.sha256,
        created_by_id=principal.user_id if principal.is_human else None,
    )
    db.add(row)
    db.flush()
    audit_log.record(
        db,
        organization_id=principal.organization_id,
        action="lab.prompt.version_created",
        resource_type="prompt_template",
        resource_id=row.id,
        principal=principal,
        after={"ref": candidate.ref, "sha256": candidate.sha256},
    )
    return row


def set_status(db: Session, principal: Principal, row: PromptTemplateRecord, status: str) -> PromptTemplateRecord:
    if status not in ("active", "retired"):
        raise ValidationFailed("status must be 'active' or 'retired'")
    row.status = status
    audit_log.record(
        db,
        organization_id=principal.organization_id,
        action="lab.prompt.status_changed",
        resource_type="prompt_template",
        resource_id=row.id,
        principal=principal,
        after={"status": status},
    )
    return row
