"""HTTP API for the prompt registry (Tag "Agents"). Included by the Models router (``/api/v1/prompts``)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Path, status
from sqlalchemy.orm import Session

from aegis_api.deps import get_db
from aegis_api.errors import NotFound
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.deps import require_actor
from aegis_api.lab.models import PromptTemplate
from aegis_api.lab.prompts import registry
from aegis_api.lab.prompts.schemas import (
    KEY_PATTERN,
    PromptKeyOut,
    PromptStatusUpdate,
    PromptTemplateCreate,
    PromptTemplateOut,
)
from aegis_api.schemas.common import Page, PageParams

router = APIRouter(tags=["Agents"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"description": "Missing permission"},
    404: {"description": "Not found (or owned by another organization)"},
    422: {"description": "Validation error"},
}


def template_out(row: PromptTemplate) -> PromptTemplateOut:
    return PromptTemplateOut.model_validate(
        {
            "id": row.id,
            "organization_id": row.organization_id,
            "scope": "organization" if row.organization_id is not None else "system",
            "key": row.key,
            "version": row.version,
            "task_type": row.task_type,
            "description": row.description,
            "system_template": row.system_template,
            "user_template": row.user_template,
            "variables": list(row.variables or []),
            "output_schema": row.output_schema,
            "status": row.status,
            "content_hash": row.content_hash,
            "created_by_id": row.created_by_id,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
    )


def _effective(rows: list[PromptTemplate]) -> PromptTemplate | None:
    active = [r for r in rows if r.status in registry.ACTIVE_STATUSES]
    if not active:
        return None
    return max(active, key=lambda r: (r.organization_id is not None, r.version))


@router.get(
    "/prompts",
    response_model=Page[PromptKeyOut],
    status_code=status.HTTP_200_OK,
    summary="List prompt templates",
    description="Prompt keys visible to the organization (system templates and organization overrides), with the "
    "version `render_prompt` currently uses and every version's status.",
    responses={403: _ERRORS[403]},
)
def list_prompts(
    params: PageParams = Depends(),
    actor: Actor = Depends(require_actor("agent:read")),
    db: Session = Depends(get_db),
) -> Page[PromptKeyOut]:
    grouped: dict[str, list[PromptTemplate]] = {}
    for row in registry.visible_templates(db, actor.organization_id):
        grouped.setdefault(row.key, []).append(row)
    items: list[PromptKeyOut] = []
    for key in sorted(grouped):
        rows = grouped[key]
        effective = _effective(rows)
        reference = effective or rows[0]
        items.append(
            PromptKeyOut(
                key=key,
                task_type=reference.task_type,
                description=reference.description,
                effective_template_id=str(effective.id) if effective else None,
                effective_version=effective.version if effective else None,
                effective_scope=(
                    ("organization" if effective.organization_id is not None else "system") if effective else None
                ),
                versions=[
                    {
                        "version": r.version,
                        "scope": "organization" if r.organization_id is not None else "system",
                        "status": r.status,
                        "template_id": str(r.id),
                    }
                    for r in rows
                ],
            )
        )
    window = items[params.offset : params.offset + params.page_size]
    return Page[PromptKeyOut].build(window, len(items), params)


@router.get(
    "/prompts/{key}/versions",
    response_model=Page[PromptTemplateOut],
    status_code=status.HTTP_200_OK,
    summary="List versions of a prompt",
    description="Every version (system and organization) of one prompt key, newest first, including the full "
    "template text.",
    responses={403: _ERRORS[403], 404: _ERRORS[404]},
)
def list_prompt_versions(
    key: str = Path(pattern=KEY_PATTERN, max_length=120),
    params: PageParams = Depends(),
    actor: Actor = Depends(require_actor("agent:read")),
    db: Session = Depends(get_db),
) -> Page[PromptTemplateOut]:
    rows = registry.visible_templates(db, actor.organization_id, key=key)
    if not rows:
        raise NotFound(f"Prompt template '{key}' not found")
    window = rows[params.offset : params.offset + params.page_size]
    return Page[PromptTemplateOut].build([template_out(r) for r in window], len(rows), params)


@router.post(
    "/prompts",
    response_model=PromptTemplateOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create an organization prompt version",
    description="Adds a new organization version of a prompt key (it overrides the system template while active). "
    "Template text is immutable once created; `{{variable}}` placeholders must match `variables` exactly. The "
    "data-handling policy is appended to the system template when it is missing. Requires `agent:manage`.",
    responses=_ERRORS,
)
def create_prompt(
    payload: PromptTemplateCreate,
    actor: Actor = Depends(require_actor("agent:manage")),
    db: Session = Depends(get_db),
) -> PromptTemplateOut:
    return template_out(registry.create_org_template(db, actor, payload))


@router.patch(
    "/prompts/{template_id}",
    response_model=PromptTemplateOut,
    status_code=status.HTTP_200_OK,
    summary="Activate or deprecate an organization prompt version",
    description="Only the status of organization templates can change (text is immutable). Deprecated versions "
    "stay renderable when pinned explicitly, for reproducibility. Requires `agent:manage`.",
    responses=_ERRORS,
)
def update_prompt_status(
    template_id: uuid.UUID,
    payload: PromptStatusUpdate,
    actor: Actor = Depends(require_actor("agent:manage")),
    db: Session = Depends(get_db),
) -> PromptTemplateOut:
    return template_out(registry.set_template_status(db, actor, template_id, payload.status))
