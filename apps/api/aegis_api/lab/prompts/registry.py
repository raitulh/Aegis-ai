"""Versioned prompt registry.

* System templates live in ``templates/*.yaml`` (one file per template: ``key``, ``version``, ``task_type``,
  ``description``, ``system``, ``user``, ``variables``, optional ``output_schema``) and are seeded into
  ``prompt_templates`` with ``organization_id`` NULL. Template text is immutable in the database; changing a
  YAML file requires bumping its ``version`` (a changed file with an existing key+version is logged and skipped).
* Organizations add their own versions through the API; the latest *active* organization template overrides the
  system template of the same key.
* Rendering is strict ``{{variable}}`` substitution: every declared variable must be supplied, unknown variables
  are rejected, nothing is evaluated, values are inserted once (never re-expanded).
* Every system prompt embeds the data-handling policy. Placeholders that the template places inside
  ``<untrusted_data>`` / ``<tool_output>`` blocks are treated as untrusted: hidden characters are stripped,
  delimiters neutralised, and values that look like prompt injection are withheld (see
  :mod:`engines.lab.prompt_security`).
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import jsonschema
import structlog
import yaml
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from aegis_api.errors import NotFound, ValidationFailed
from aegis_api.lab.core.access import get_owned
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.audit import AuditAction, audit
from aegis_api.lab.core.locks import advisory_xact_lock
from aegis_api.lab.llm.schemas import TaskType
from aegis_api.lab.models import PromptTemplate
from aegis_api.lab.prompts.schemas import KEY_PATTERN, VARIABLE_PATTERN, PromptTemplateCreate
from engines.lab.prompt_security import (
    DATA_HANDLING_MARKER,
    DATA_HANDLING_POLICY,
    InjectionFinding,
    sanitize_untrusted,
    scan_for_injection,
)

log = structlog.get_logger("aegis.lab.prompts")

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_ANY_BRACES = re.compile(r"\{\{.*?\}\}", re.S)
_UNTRUSTED_REGION = re.compile(r"<(untrusted_data|tool_output)\b[^>]*>(.*?)</\1>", re.S)
MAX_UNTRUSTED_VALUE_CHARS = 60_000
ACTIVE_STATUSES = ("active",)
PINNABLE_STATUSES = ("active", "deprecated")


class PromptTemplateError(ValidationFailed):
    """The prompt template is invalid."""

    code = "prompt_template_invalid"


class PromptRenderError(ValidationFailed):
    """The prompt could not be rendered with the supplied variables."""

    code = "prompt_render_error"


@dataclass(frozen=True)
class TemplateSpec:
    key: str
    version: int
    task_type: str
    description: str | None
    system: str
    user: str
    variables: tuple[str, ...]
    output_schema: dict[str, Any] | None = None

    @property
    def content_hash(self) -> str:
        return content_hash(self.task_type, self.system, self.user, list(self.variables), self.output_schema)


@dataclass(frozen=True)
class RenderedPrompt:
    template_id: str
    key: str
    version: int
    system: str
    user: str
    prompt_hash: str
    output_schema: dict[str, Any] | None
    task_type: str = ""
    scope: str = "system"
    injection_findings: list[dict[str, Any]] = field(default_factory=list)


# --- validation & hashing --------------------------------------------------------------------------
def content_hash(
    task_type: str, system: str, user: str, variables: list[str], output_schema: dict[str, Any] | None
) -> str:
    canonical = json.dumps(
        {
            "task_type": task_type,
            "system": system,
            "user": user,
            "variables": variables,
            "output_schema": output_schema,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def placeholders(*texts: str) -> set[str]:
    return {m.group(1) for text in texts for m in PLACEHOLDER.finditer(text)}


def untrusted_placeholders(*texts: str) -> set[str]:
    names: set[str] = set()
    for text in texts:
        for region in _UNTRUSTED_REGION.finditer(text):
            names |= placeholders(region.group(2))
    return names


def validate_template(
    *,
    key: str,
    task_type: str,
    system: str,
    user: str,
    variables: list[str],
    output_schema: dict[str, Any] | None,
    require_policy: bool = True,
) -> None:
    """Raise :class:`PromptTemplateError` unless the template is well-formed and self-consistent."""
    problems: list[str] = []
    if not re.match(KEY_PATTERN, key or ""):
        problems.append(f"invalid key {key!r}")
    try:
        TaskType(task_type)
    except ValueError:
        problems.append(f"unknown task_type {task_type!r}")
    for text in (system, user):
        for match in _ANY_BRACES.finditer(text):
            if not PLACEHOLDER.fullmatch(match.group(0)):
                problems.append(f"malformed placeholder {match.group(0)[:40]!r}")
    bad_names = [v for v in variables if not re.match(VARIABLE_PATTERN, v)]
    if bad_names:
        problems.append(f"invalid variable names {bad_names}")
    if len(set(variables)) != len(variables):
        problems.append("duplicate variable names")
    used = placeholders(system, user)
    if set(variables) != used:
        missing = sorted(used - set(variables))
        unused = sorted(set(variables) - used)
        if missing:
            problems.append(f"placeholders not declared in variables: {missing}")
        if unused:
            problems.append(f"declared variables never used: {unused}")
    if require_policy and DATA_HANDLING_MARKER not in system:
        problems.append("the system template must embed the data-handling policy")
    if output_schema is not None:
        try:
            jsonschema.Draft202012Validator.check_schema(output_schema)
        except jsonschema.SchemaError as exc:
            problems.append(f"output_schema is not a valid JSON Schema: {exc.message}")
    if problems:
        raise PromptTemplateError(f"Invalid prompt template '{key}': " + "; ".join(problems), details=problems)


# --- YAML templates --------------------------------------------------------------------------------
def load_template_file(path: Path) -> TemplateSpec:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise PromptTemplateError(f"{path.name}: expected a mapping")
    try:
        spec = TemplateSpec(
            key=str(data["key"]),
            version=int(data["version"]),
            task_type=str(data["task_type"]),
            description=(str(data["description"]).strip() or None) if data.get("description") else None,
            system=str(data["system"]).strip(),
            user=str(data["user"]).strip(),
            variables=tuple(str(v) for v in data.get("variables") or ()),
            output_schema=data.get("output_schema"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise PromptTemplateError(f"{path.name}: missing or invalid field ({exc})") from exc
    if spec.version < 1:
        raise PromptTemplateError(f"{path.name}: version must be >= 1")
    validate_template(
        key=spec.key,
        task_type=spec.task_type,
        system=spec.system,
        user=spec.user,
        variables=list(spec.variables),
        output_schema=spec.output_schema,
    )
    return spec


def load_system_templates(directory: Path = TEMPLATES_DIR) -> list[TemplateSpec]:
    specs = [load_template_file(path) for path in sorted(directory.glob("*.yaml"))]
    seen: set[tuple[str, int]] = set()
    for spec in specs:
        if (spec.key, spec.version) in seen:
            raise PromptTemplateError(f"duplicate system template {spec.key} v{spec.version}")
        seen.add((spec.key, spec.version))
    return specs


def seed_system_prompts(session: Session, directory: Path = TEMPLATES_DIR) -> dict[str, Any]:
    """Idempotently insert the YAML system templates (owner/admin session). Returns a summary."""
    specs = load_system_templates(directory)
    existing = {
        (row.key, row.version): row.content_hash
        for row in session.execute(
            select(PromptTemplate.key, PromptTemplate.version, PromptTemplate.content_hash).where(
                PromptTemplate.organization_id.is_(None)
            )
        )
    }
    inserted, unchanged, conflicts = 0, 0, []
    for spec in specs:
        current = existing.get((spec.key, spec.version))
        if current == spec.content_hash:
            unchanged += 1
            continue
        if current is not None:
            conflicts.append(f"{spec.key}@{spec.version}")
            log.error(
                "prompt_template_changed_without_version_bump",
                key=spec.key,
                version=spec.version,
                hint="bump the version in the YAML file; stored template text is immutable",
            )
            continue
        result = session.execute(
            insert(PromptTemplate)
            .values(
                id=uuid.uuid4(),
                organization_id=None,
                key=spec.key,
                version=spec.version,
                task_type=spec.task_type,
                description=spec.description,
                system_template=spec.system,
                user_template=spec.user,
                variables=list(spec.variables),
                output_schema=spec.output_schema,
                status="active",
                content_hash=spec.content_hash,
            )
            .on_conflict_do_nothing(constraint="uq_prompt_templates_key_version")
            .returning(PromptTemplate.id)
        )
        inserted += 1 if result.scalar_one_or_none() is not None else 0
    session.flush()
    return {"inserted": inserted, "unchanged": unchanged, "conflicts": conflicts, "total": len(specs)}


# --- rendering -------------------------------------------------------------------------------------
def _stringify(name: str, value: Any) -> str:
    if value is None:
        raise PromptRenderError(f"Variable '{name}' is None")
    if isinstance(value, str):
        return value
    if isinstance(value, bool | int | float | Decimal):
        return str(value)
    if isinstance(value, dict | list | tuple):
        return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, default=str)
    return str(value)


def render_text(
    template: str,
    values: Mapping[str, str],
) -> str:
    return PLACEHOLDER.sub(lambda m: values[m.group(1)], template)


def render_template(
    row: PromptTemplate | TemplateSpec, variables: Mapping[str, Any]
) -> tuple[str, str, list[dict[str, Any]]]:
    """(system, user, injection findings) for a template row/spec — strict substitution."""
    if isinstance(row, PromptTemplate):
        system_t, user_t = row.system_template, row.user_template
        declared = [str(v) for v in (row.variables or [])]
    else:
        system_t, user_t, declared = row.system, row.user, list(row.variables)
    provided = set(variables)
    missing = sorted(set(declared) - provided)
    unknown = sorted(provided - set(declared))
    if missing or unknown:
        parts = []
        if missing:
            parts.append(f"missing variables: {', '.join(missing)}")
        if unknown:
            parts.append(f"unknown variables: {', '.join(unknown)}")
        raise PromptRenderError(
            "Cannot render prompt: " + "; ".join(parts), details={"missing": missing, "unknown": unknown}
        )
    untrusted = untrusted_placeholders(system_t, user_t)
    values: dict[str, str] = {}
    findings: list[dict[str, Any]] = []
    for name in declared:
        text = _stringify(name, variables[name])
        if name in untrusted:
            scan = scan_for_injection(text)
            if scan.findings:
                findings.append(
                    {
                        "variable": name,
                        "risk_score": scan.risk_score,
                        "quarantined": scan.quarantine,
                        "findings": [f.as_dict() for f in scan.findings],
                    }
                )
            if scan.quarantine:
                text = _withheld_notice(scan.findings)
            else:
                text = sanitize_untrusted(text, MAX_UNTRUSTED_VALUE_CHARS)
        values[name] = text
    return render_text(system_t, values), render_text(user_t, values), findings


def _withheld_notice(findings: list[InjectionFinding]) -> str:
    categories = ", ".join(sorted({f.category for f in findings}))
    return f"[content withheld: possible prompt injection detected ({categories})]"


def prompt_hash(system: str, user: str) -> str:
    return hashlib.sha256(f"{system}\x1e{user}".encode()).hexdigest()


def resolve_template(
    db: Session, organization_id: uuid.UUID, key: str, *, version: int | None = None
) -> PromptTemplate:
    """The template ``render_prompt`` uses: organization rows override system rows; latest active unless pinned."""
    stmt = select(PromptTemplate).where(
        PromptTemplate.key == key,
        or_(PromptTemplate.organization_id == organization_id, PromptTemplate.organization_id.is_(None)),
    )
    if version is None:
        stmt = stmt.where(PromptTemplate.status.in_(ACTIVE_STATUSES))
    else:
        stmt = stmt.where(PromptTemplate.version == version, PromptTemplate.status.in_(PINNABLE_STATUSES))
    rows = [r for r in db.scalars(stmt) if r.organization_id in (None, organization_id)]
    if not rows:
        suffix = f" version {version}" if version is not None else ""
        raise NotFound(f"Prompt template '{key}'{suffix} not found")
    rows.sort(key=lambda r: (r.organization_id is not None, r.version), reverse=True)
    return rows[0]


def render_prompt(
    db: Session,
    organization_id: uuid.UUID,
    key: str,
    variables: Mapping[str, Any],
    *,
    version: int | None = None,
) -> RenderedPrompt:
    """Render the effective template for ``key`` (see :func:`resolve_template`)."""
    row = resolve_template(db, organization_id, key, version=version)
    system, user, findings = render_template(row, variables)
    if findings:
        log.warning(
            "prompt_untrusted_variable_flagged",
            key=key,
            variables=[f["variable"] for f in findings],
            quarantined=[f["variable"] for f in findings if f["quarantined"]],
        )
    return RenderedPrompt(
        template_id=str(row.id),
        key=row.key,
        version=row.version,
        system=system,
        user=user,
        prompt_hash=prompt_hash(system, user),
        output_schema=row.output_schema,
        task_type=row.task_type,
        scope="organization" if row.organization_id is not None else "system",
        injection_findings=findings,
    )


# --- organization templates ----------------------------------------------------------------------
def create_org_template(db: Session, actor: Actor, data: PromptTemplateCreate) -> PromptTemplate:
    """Add a new organization version of ``data.key`` (version = highest system/org version + 1)."""
    actor.require("agent:manage")
    system = data.system.strip()
    if DATA_HANDLING_MARKER not in system:
        system = f"{system}\n\n{DATA_HANDLING_POLICY}"
    user = data.user.strip()
    validate_template(
        key=data.key,
        task_type=data.task_type.value,
        system=system,
        user=user,
        variables=list(data.variables),
        output_schema=data.output_schema,
    )
    advisory_xact_lock(db, f"prompt-template:{actor.organization_id}:{data.key}")
    current = db.scalar(
        select(func.max(PromptTemplate.version)).where(
            PromptTemplate.key == data.key,
            or_(PromptTemplate.organization_id == actor.organization_id, PromptTemplate.organization_id.is_(None)),
        )
    )
    row = PromptTemplate(
        organization_id=actor.organization_id,
        key=data.key,
        version=int(current or 0) + 1,
        task_type=data.task_type.value,
        description=data.description,
        system_template=system,
        user_template=user,
        variables=list(data.variables),
        output_schema=data.output_schema,
        status=data.status,
        content_hash=content_hash(data.task_type.value, system, user, list(data.variables), data.output_schema),
        created_by_id=actor.user_id,
    )
    db.add(row)
    db.flush()
    audit(
        db,
        actor,
        AuditAction.AGENT_CONFIGURED,
        "prompt_template",
        row.id,
        after={"key": row.key, "version": row.version, "status": row.status, "content_hash": row.content_hash},
    )
    return row


def set_template_status(db: Session, actor: Actor, template_id: uuid.UUID | str, status: str) -> PromptTemplate:
    """Change the status of an organization template (system templates are managed by the platform)."""
    actor.require("agent:manage")
    row = get_owned(db, PromptTemplate, template_id, actor, label="Prompt template")
    if status not in ("active", "deprecated"):
        raise ValidationFailed("status must be 'active' or 'deprecated'")
    if row.status == status:
        return row
    before = {"status": row.status}
    row.status = status
    db.flush()
    audit(db, actor, AuditAction.AGENT_CONFIGURED, "prompt_template", row.id, before=before, after={"status": status})
    return row


def visible_templates(db: Session, organization_id: uuid.UUID, *, key: str | None = None) -> list[PromptTemplate]:
    stmt = select(PromptTemplate).where(
        or_(PromptTemplate.organization_id == organization_id, PromptTemplate.organization_id.is_(None))
    )
    if key is not None:
        stmt = stmt.where(PromptTemplate.key == key)
    rows = [r for r in db.scalars(stmt) if r.organization_id in (None, organization_id)]
    rows.sort(key=lambda r: (r.key, -r.version, r.organization_id is None))
    return rows
