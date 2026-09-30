"""Scientific memory: policy-gated writes, visibility rules, human review, versioning and promotion.

Writes (``write_memory``):

1. scope resolution + ``memory:write`` (inside the project for project/mission/workspace scopes);
2. a deterministic prompt-injection scan of title + content (:func:`engines.lab.prompt_security.scan_for_injection`);
3. the write policy (:func:`engines.lab.memory_policy.decide_memory_write`) decides status, trust level and
   whether review is required — never the caller. Provenance keys the policy relies on (``user_id``,
   ``agent_run_id``, ``mission_id``) are filled from the authenticated actor and cannot be spoofed;
4. de-duplication by content hash within the same scope (the existing live item is returned);
5. versioning via ``supersedes_id`` (the old item becomes ``SUPERSEDED`` once the new one is ``ACTIVE``);
6. embedding for semantic search, and a ``MEMORY_WRITTEN`` event (ids and status only — never content).

Visibility (list/get/search share :func:`visibility_clause`):

* ``ACTIVE`` / ``SUPERSEDED`` items: everyone who can read the project (or the organization for org-level items);
* ``PROPOSED`` / ``REJECTED``: reviewers (``memory:review``) and the item's creator;
* ``QUARANTINED``: reviewers only; ``restricted`` sensitivity: reviewers only;
* ``user`` scope: the owner only (nobody else, reviewers included).

Promotion of an item to a broader scope is governed by the ``memory.promote`` policy action (the baseline
requires a human approval); the approval hook applies it when a reviewer approves.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Any, Literal

import structlog
from sqlalchemy import and_, false, func, literal_column, or_, select, true
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from aegis_api.db.base import utcnow
from aegis_api.errors import Forbidden, NotFound, ValidationFailed
from aegis_api.lab.core.access import (
    effective_permissions,
    get_owned,
    load_project,
    visible_project_ids,
)
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.audit import AuditAction, audit
from aegis_api.lab.core.errors import PolicyDenied
from aegis_api.lab.core.events import EventType, emit
from aegis_api.lab.core.locks import advisory_xact_lock
from aegis_api.lab.core.pagination import paginate
from aegis_api.lab.knowledge import embeddings
from aegis_api.lab.knowledge.schemas import MemoryPromoteIn, MemoryWrite
from aegis_api.lab.models import Approval, Memory, MemoryLink, Mission, Project, ProjectMember, TeamMember
from aegis_api.schemas.common import Page, PageParams
from aegis_api.security.rbac import permissions_for_role
from engines.lab.memory_policy import MemoryWriteRequest, decide_memory_write
from engines.lab.prompt_security import scan_for_injection
from engines.lab.states import ApprovalStatus, MemoryStatus, assert_transition

log = structlog.get_logger("aegis.lab.knowledge.memory")

MEMORY_READ = "memory:read"
MEMORY_WRITE = "memory:write"
MEMORY_REVIEW = "memory:review"
PROMOTE_ACTION = "memory.promote"
MEMORY_PROMOTED = "MEMORY_PROMOTED"
LIVE_STATUSES = (MemoryStatus.PROPOSED, MemoryStatus.ACTIVE, MemoryStatus.QUARANTINED)
PUBLIC_STATUSES = frozenset({MemoryStatus.ACTIVE, MemoryStatus.SUPERSEDED})
REVIEWER_OR_CREATOR_STATUSES = frozenset({MemoryStatus.PROPOSED, MemoryStatus.REJECTED})
SCOPE_RANK = {"user": 0, "mission": 0, "project": 1, "workspace": 2, "organization": 3}
MAX_FINDINGS = 20
TS_CONFIG = literal_column("'english'::regconfig")


@dataclass(frozen=True)
class PromotionResult:
    status: Literal["promoted", "approval_required"]
    memory: Memory | None = None
    approval: Approval | None = None


def _uuid(value: Any, label: str) -> uuid.UUID:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except ValueError as exc:
        raise NotFound(f"{label} not found") from exc


def content_hash(title: str, content: str) -> str:
    """Hash of the normalized title + content (whitespace- and case-insensitive)."""
    normalized = "\x1f".join(" ".join(part.split()).casefold() for part in (title, content))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


# --- reviewer rights ------------------------------------------------------------------------------
def _review_project_ids(db: Session, actor: Actor) -> list[uuid.UUID]:
    """Projects in which the actor holds ``memory:review`` through a project/team role (humans only)."""
    if actor.kind != "user" or actor.user_id is None:
        return []
    team_ids = select(TeamMember.team_id).where(TeamMember.user_id == actor.user_id)
    rows = db.execute(
        select(ProjectMember.project_id, ProjectMember.role).where(
            or_(ProjectMember.user_id == actor.user_id, ProjectMember.team_id.in_(team_ids))
        )
    ).all()
    return sorted({pid for pid, role in rows if MEMORY_REVIEW in permissions_for_role(role)})


def can_review(db: Session, actor: Actor, project: Project | None) -> bool:
    """Human reviewer rights for items of ``project`` (or organization-level items when ``None``)."""
    if not actor.is_human:
        return False
    if project is None:
        return actor.has(MEMORY_REVIEW)
    return MEMORY_REVIEW in effective_permissions(db, actor, project)


def _creator_clause(actor: Actor) -> ColumnElement[bool]:
    clauses: list[ColumnElement[bool]] = []
    if actor.kind == "user" and actor.user_id is not None:
        clauses.append(and_(Memory.created_by_id == actor.user_id, Memory.created_by_agent_run_id.is_(None)))
    if actor.agent_run_id is not None:
        clauses.append(Memory.created_by_agent_run_id == actor.agent_run_id)
    return or_(*clauses) if clauses else false()


def _is_creator(memory: Memory, actor: Actor) -> bool:
    if actor.agent_run_id is not None and memory.created_by_agent_run_id == actor.agent_run_id:
        return True
    return (
        actor.kind == "user"
        and actor.user_id is not None
        and memory.created_by_id == actor.user_id
        and memory.created_by_agent_run_id is None
    )


def visibility_clause(
    db: Session,
    actor: Actor,
    *,
    statuses: list[str] | None = None,
    visible_projects: list[uuid.UUID] | Literal["unset"] | None = "unset",
) -> ColumnElement[bool]:
    """SQL predicate selecting the memory items ``actor`` may read (see the module docstring)."""
    visible = visible_project_ids(db, actor) if visible_projects == "unset" else visible_projects
    wanted = set(statuses) if statuses else {MemoryStatus.ACTIVE}
    reviewer_org = actor.is_human and actor.has(MEMORY_REVIEW)
    review_projects = _review_project_ids(db, actor) if actor.is_human else []
    if reviewer_org:
        reviewer: ColumnElement[bool] = true()
    elif review_projects:
        reviewer = Memory.project_id.in_(review_projects)
    else:
        reviewer = false()
    creator = _creator_clause(actor)

    status_terms: list[ColumnElement[bool]] = []
    for status in sorted(wanted):
        if status in PUBLIC_STATUSES:
            status_terms.append(Memory.status == status)
        elif status in REVIEWER_OR_CREATOR_STATUSES:
            status_terms.append(and_(Memory.status == status, or_(reviewer, creator)))
        elif status == MemoryStatus.QUARANTINED:
            status_terms.append(and_(Memory.status == status, reviewer))
    clauses: list[ColumnElement[bool]] = [
        Memory.organization_id == actor.organization_id,
        or_(*status_terms) if status_terms else false(),
        or_(Memory.sensitivity != "restricted", reviewer),
    ]
    if actor.user_id is not None and actor.kind in ("user", "agent"):
        clauses.append(or_(Memory.scope != "user", Memory.owner_user_id == actor.user_id))
    else:
        clauses.append(Memory.scope != "user")
    if visible is not None:
        clauses.append(or_(Memory.project_id.is_(None), Memory.project_id.in_(visible)))
    return and_(*clauses)


def can_read(db: Session, actor: Actor, memory: Memory) -> bool:
    """Python mirror of :func:`visibility_clause` for a loaded row (any status)."""
    if memory.organization_id != actor.organization_id:
        return False
    project = db.get(Project, memory.project_id) if memory.project_id else None
    if project is not None:
        try:
            load_project(db, actor, project.id, MEMORY_READ)
        except (NotFound, Forbidden):
            return False
    elif not actor.has(MEMORY_READ):
        return False
    if memory.scope == "user" and (actor.user_id is None or memory.owner_user_id != actor.user_id):
        return False
    reviewer = can_review(db, actor, project)
    if memory.sensitivity == "restricted" and not reviewer:
        return False
    if memory.status in PUBLIC_STATUSES:
        return True
    if memory.status in REVIEWER_OR_CREATOR_STATUSES:
        return reviewer or _is_creator(memory, actor)
    return reviewer


# --- reads ----------------------------------------------------------------------------------------
def get_memory(db: Session, actor: Actor, memory_id: uuid.UUID | str) -> Memory:
    memory = get_owned(db, Memory, memory_id, actor, label="Memory")
    if not can_read(db, actor, memory):
        if memory.project_id is None and not actor.has(MEMORY_READ):
            raise Forbidden(f"Missing required permission(s): {MEMORY_READ}")
        raise NotFound("Memory not found")
    return memory


def list_memories(
    db: Session,
    actor: Actor,
    params: PageParams,
    *,
    project_id: uuid.UUID | str | None = None,
    mission_id: uuid.UUID | str | None = None,
    category: str | None = None,
    scope: str | None = None,
    statuses: list[str] | None = None,
    tag: str | None = None,
    q: str | None = None,
    mapper: Any = None,
) -> Page[Any]:
    visible: list[uuid.UUID] | None
    project: Project | None = None
    if project_id is not None:
        project = load_project(db, actor, project_id, MEMORY_READ)
        visible = [project.id]
    else:
        actor.require(MEMORY_READ)
        visible = visible_project_ids(db, actor)
    stmt = select(Memory).where(visibility_clause(db, actor, statuses=statuses, visible_projects=visible))
    if project is not None:
        stmt = stmt.where(Memory.project_id == project.id)
    if mission_id is not None:
        stmt = stmt.where(Memory.mission_id == _uuid(mission_id, "Mission"))
    if category:
        stmt = stmt.where(Memory.category == category)
    if scope:
        stmt = stmt.where(Memory.scope == scope)
    if tag:
        stmt = stmt.where(Memory.tags.contains([tag.lower()]))
    if q:
        stmt = stmt.where(Memory.tsv.op("@@")(func.websearch_to_tsquery(TS_CONFIG, q)))
    stmt = stmt.order_by(Memory.updated_at.desc(), Memory.id.desc())
    return paginate(db, stmt, params, mapper or (lambda m: m))


# --- writes ---------------------------------------------------------------------------------------
@dataclass(frozen=True)
class _Scope:
    project: Project | None
    mission: Mission | None
    workspace_id: uuid.UUID | None
    owner_user_id: uuid.UUID | None


def _resolve_scope(db: Session, actor: Actor, data: MemoryWrite) -> _Scope:
    mission: Mission | None = None
    project: Project | None = None
    if data.mission_id is not None:
        mission = get_owned(db, Mission, data.mission_id, actor, label="Mission")
    if data.scope == "mission":
        if mission is None:
            raise ValidationFailed("mission_id is required for mission-scope memory")
        project = load_project(db, actor, mission.project_id, MEMORY_WRITE)
        if data.project_id is not None and _uuid(data.project_id, "Project") != project.id:
            raise ValidationFailed("project_id does not match the mission's project")
    elif data.scope in ("project", "workspace"):
        project_ref = data.project_id or (mission.project_id if mission is not None else None)
        if project_ref is None:
            raise ValidationFailed(f"project_id is required for {data.scope}-scope memory")
        project = load_project(db, actor, project_ref, MEMORY_WRITE)
        if mission is not None and mission.project_id != project.id:
            raise ValidationFailed("mission_id does not belong to the project")
    elif data.scope == "organization":
        if data.project_id is not None or mission is not None:
            raise ValidationFailed("Organization-scope memory is not tied to a project or mission")
        actor.require(MEMORY_WRITE)
    else:  # user scope: private notes of a signed-in person
        if actor.kind != "user" or actor.user_id is None:
            raise ValidationFailed("User-scope memory belongs to a person; agents and keys cannot write it")
        if data.project_id is not None:
            project = load_project(db, actor, data.project_id, MEMORY_WRITE)
        else:
            actor.require(MEMORY_WRITE)
        mission = None
    if data.scope == "workspace":
        assert project is not None
        return _Scope(project=None, mission=mission, workspace_id=project.workspace_id, owner_user_id=None)
    return _Scope(
        project=project,
        mission=mission if data.scope in ("mission", "project") else None,
        workspace_id=project.workspace_id if project is not None else None,
        owner_user_id=actor.user_id if data.scope == "user" else None,
    )


def _authoritative_provenance(actor: Actor, data: MemoryWrite, mission: Mission | None) -> dict[str, Any]:
    """Caller provenance plus identity facts only the platform can vouch for (never taken from the caller)."""
    provenance = {k: v for k, v in data.provenance.items() if k not in ("user_id", "agent_run_id", "write_policy")}
    if actor.kind == "user" and actor.user_id is not None:
        provenance["user_id"] = str(actor.user_id)
    elif actor.kind == "api_key" and actor.api_key_id is not None:
        provenance["user_id"] = str(actor.user_id) if actor.user_id else None
        provenance["api_key_id"] = str(actor.api_key_id)
    if actor.agent_run_id is not None:
        provenance["agent_run_id"] = str(actor.agent_run_id)
    if actor.workflow_run_id is not None:
        provenance["workflow_run_id"] = str(actor.workflow_run_id)
    if mission is not None:
        provenance["mission_id"] = str(mission.id)
    elif "mission_id" in provenance:
        provenance.pop("mission_id")
    provenance["actor"] = actor.as_dict()
    return {k: v for k, v in provenance.items() if v is not None}


def _find_duplicate(db: Session, actor: Actor, scope: _Scope, data: MemoryWrite, chash: str) -> Memory | None:
    stmt = select(Memory).where(
        Memory.organization_id == actor.organization_id,
        Memory.scope == data.scope,
        Memory.content_hash == chash,
        Memory.status.in_(LIVE_STATUSES),
    )
    for column, value in (
        (Memory.project_id, scope.project.id if scope.project else None),
        (Memory.workspace_id, scope.workspace_id),
        (Memory.mission_id, scope.mission.id if scope.mission else None),
        (Memory.owner_user_id, scope.owner_user_id),
    ):
        stmt = stmt.where(column.is_(None) if value is None else column == value)
    return db.scalars(stmt.order_by(Memory.created_at).limit(1)).first()


def _supersede(db: Session, actor: Actor, old: Memory, new: Memory) -> None:
    if old.status != MemoryStatus.ACTIVE:
        return
    assert_transition("memory", old.status, MemoryStatus.SUPERSEDED)
    old.status = MemoryStatus.SUPERSEDED
    db.flush()
    emit(
        db,
        organization_id=old.organization_id,
        type=EventType.MEMORY_WRITTEN,
        payload={"memory_id": str(old.id), "status": old.status, "superseded_by": str(new.id)},
        mission_id=old.mission_id,
        project_id=old.project_id,
        workspace_id=old.workspace_id,
        subject_type="memory",
        subject_id=old.id,
        actor=actor,
    )


def _index(db: Session, actor: Actor, memory: Memory) -> None:
    if memory.status in (MemoryStatus.QUARANTINED, MemoryStatus.REJECTED):
        return
    embeddings.index_text(db, actor, "memory", memory.id, f"{memory.title}\n{memory.content}", memory.project_id)


def _emit_written(db: Session, actor: Actor, memory: Memory, **extra: Any) -> None:
    emit(
        db,
        organization_id=memory.organization_id,
        type=EventType.MEMORY_WRITTEN,
        payload={
            "memory_id": str(memory.id),
            "category": memory.category,
            "scope": memory.scope,
            "status": memory.status,
            "trust_level": memory.trust_level,
            "review_required": memory.review_required,
            **extra,
        },
        mission_id=memory.mission_id,
        project_id=memory.project_id,
        workspace_id=memory.workspace_id,
        subject_type="memory",
        subject_id=memory.id,
        actor=actor,
    )


def write_memory(db: Session, actor: Actor, data: MemoryWrite) -> Memory:
    """Write a memory item through the write policy (see the module docstring). Idempotent per content."""
    scope = _resolve_scope(db, actor, data)
    scan = scan_for_injection(f"{data.title}\n{data.content}")
    provenance = _authoritative_provenance(actor, data, scope.mission)
    reviewer = can_review(db, actor, scope.project)
    decision = decide_memory_write(
        MemoryWriteRequest(
            category=data.category,
            scope=data.scope,
            source_type=data.source_type,
            actor_kind=actor.kind,
            confidence=data.confidence,
            provenance=provenance,
            injection_risk=scan.risk_score,
            sensitivity=data.sensitivity,
            actor_can_review=reviewer,
        )
    )
    chash = content_hash(data.title, data.content)
    advisory_xact_lock(
        db,
        f"memory:{actor.organization_id}:{data.scope}:{scope.project.id if scope.project else '-'}:"
        f"{scope.mission.id if scope.mission else '-'}:{scope.owner_user_id or '-'}:{chash}",
    )
    existing = _find_duplicate(db, actor, scope, data, chash)
    if existing is not None:
        return existing

    previous: Memory | None = None
    version = 1
    if data.supersedes_id is not None:
        previous = get_memory(db, actor, data.supersedes_id)
        same_scope = (
            previous.scope == data.scope
            and previous.project_id == (scope.project.id if scope.project else None)
            and previous.workspace_id == scope.workspace_id
            and previous.owner_user_id == scope.owner_user_id
        )
        if not same_scope:
            raise ValidationFailed("supersedes_id must reference a memory in the same scope")
        if previous.status != MemoryStatus.ACTIVE:
            assert_transition("memory", previous.status, MemoryStatus.SUPERSEDED)
        version = previous.version + 1

    policy = decision.model_dump(mode="json")
    memory = Memory(
        id=uuid.uuid4(),
        organization_id=actor.organization_id,
        workspace_id=scope.workspace_id,
        project_id=scope.project.id if scope.project else None,
        mission_id=scope.mission.id if scope.mission else None,
        owner_user_id=scope.owner_user_id,
        category=str(data.category),
        scope=data.scope,
        title=data.title,
        content=data.content,
        content_hash=chash,
        source_type=decision.effective_source_type,
        source_ref=data.source_ref,
        confidence=data.confidence,
        provenance={**provenance, "write_policy": policy},
        version=version,
        supersedes_id=previous.id if previous else None,
        status=decision.status,
        trust_level=decision.trust_level,
        sensitivity=data.sensitivity,
        review_required=decision.review_required,
        injection_findings=[f.as_dict() for f in scan.findings[:MAX_FINDINGS]],
        tags=data.tags,
        expires_at=data.expires_at,
        created_by_id=actor.user_id,
        created_by_agent_run_id=actor.agent_run_id,
    )
    db.add(memory)
    db.flush()
    if previous is not None and memory.status == MemoryStatus.ACTIVE:
        _supersede(db, actor, previous, memory)
    _index(db, actor, memory)
    _emit_written(db, actor, memory, version=memory.version)
    if scan.findings:
        log.info("memory_injection_findings", memory_id=str(memory.id), risk=scan.risk_score, status=memory.status)
    return memory


# --- review ---------------------------------------------------------------------------------------
def _lock(db: Session, actor: Actor, memory_id: uuid.UUID | str) -> Memory:
    memory = get_owned(db, Memory, memory_id, actor, label="Memory")
    db.flush()
    locked = db.execute(
        select(Memory).where(Memory.id == memory.id).with_for_update().execution_options(populate_existing=True)
    ).scalar_one()
    return locked


def review_memory(db: Session, actor: Actor, memory_id: uuid.UUID | str, *, approve: bool, reason: str) -> Memory:
    """Human review of a PROPOSED/QUARANTINED item → ACTIVE (trust ``reviewed``) or REJECTED."""
    actor.require_human("memory review")
    memory = _lock(db, actor, memory_id)
    if memory.scope == "user" and memory.owner_user_id != actor.user_id:
        raise NotFound("Memory not found")
    project = load_project(db, actor, memory.project_id) if memory.project_id else None
    if not can_review(db, actor, project):
        raise Forbidden(f"Missing required permission(s): {MEMORY_REVIEW}")
    target = MemoryStatus.ACTIVE if approve else MemoryStatus.REJECTED
    assert_transition("memory", memory.status, target)
    clean_reason = " ".join(reason.split())[:2000]
    if not clean_reason:
        raise ValidationFailed("A review reason is required")
    before = {"status": memory.status, "trust_level": memory.trust_level}
    was_quarantined = memory.status == MemoryStatus.QUARANTINED
    now = utcnow()
    memory.status = target
    memory.review_required = False
    memory.reviewed_by_id = actor.user_id
    memory.reviewed_at = now
    if approve:
        memory.trust_level = "reviewed"
    memory.provenance = {
        **(memory.provenance or {}),
        "review": {"by": str(actor.user_id), "at": now.isoformat(), "approved": approve, "reason": clean_reason},
    }
    db.flush()
    if approve and memory.supersedes_id is not None:
        previous = db.get(Memory, memory.supersedes_id)
        if previous is not None:
            _supersede(db, actor, previous, memory)
    if approve and was_quarantined:
        _index(db, actor, memory)
    audit(
        db,
        actor,
        AuditAction.MEMORY_REVIEWED,
        "memory",
        memory.id,
        before=before,
        after={"status": memory.status, "trust_level": memory.trust_level, "reason": clean_reason},
    )
    _emit_written(db, actor, memory, reviewed=True)
    return memory


# --- promotion ------------------------------------------------------------------------------------
def _promotion_target(
    db: Session, actor: Actor, memory: Memory, scope: str, project_id: str | None
) -> tuple[Project | None, uuid.UUID | None]:
    """(target project, target workspace id) for promoting ``memory`` to ``scope``."""
    if SCOPE_RANK[scope] <= SCOPE_RANK.get(memory.scope, 0):
        raise ValidationFailed(f"A {memory.scope}-scope memory cannot be promoted to {scope} scope")
    if scope == "organization":
        actor.require(MEMORY_WRITE)
        return None, None
    ref = project_id or memory.project_id
    if ref is None:
        raise ValidationFailed("project_id is required to promote this memory")
    project = load_project(db, actor, ref, MEMORY_WRITE)
    if memory.project_id is not None and memory.project_id != project.id and scope == "project":
        raise ValidationFailed("A memory can only be promoted within its own project")
    return (project if scope == "project" else None), project.workspace_id


def _apply_promotion(
    db: Session,
    actor: Actor,
    memory: Memory,
    *,
    scope: str,
    project: Project | None,
    workspace_id: uuid.UUID | None,
    reason: str,
    approval: Approval | None,
) -> Memory:
    project_uuid = project.id if project is not None else None
    advisory_xact_lock(db, f"memory-promote:{memory.id}:{scope}")
    stmt = select(Memory).where(
        Memory.organization_id == memory.organization_id,
        Memory.scope == scope,
        Memory.content_hash == memory.content_hash,
        Memory.status.in_(LIVE_STATUSES),
        Memory.project_id.is_(None) if project_uuid is None else Memory.project_id == project_uuid,
        Memory.workspace_id.is_(None)
        if workspace_id is None or scope == "organization"
        else (Memory.workspace_id == workspace_id),
    )
    promoted = db.scalars(stmt.limit(1)).first()
    if promoted is None:
        now = utcnow()
        promoted = Memory(
            id=uuid.uuid4(),
            organization_id=memory.organization_id,
            workspace_id=None if scope == "organization" else workspace_id,
            project_id=project_uuid,
            mission_id=None,
            owner_user_id=None,
            category=memory.category,
            scope=scope,
            title=memory.title,
            content=memory.content,
            content_hash=memory.content_hash,
            source_type=memory.source_type,
            source_ref={**(memory.source_ref or {}), "promoted_from": str(memory.id)},
            confidence=memory.confidence,
            provenance={
                **(memory.provenance or {}),
                "promotion": {
                    "from_memory_id": str(memory.id),
                    "from_scope": memory.scope,
                    "by": actor.as_dict(),
                    "at": now.isoformat(),
                    "reason": reason[:2000],
                    "approval_id": str(approval.id) if approval else None,
                },
            },
            version=1,
            status=MemoryStatus.ACTIVE,
            trust_level="reviewed" if approval is not None else memory.trust_level,
            sensitivity=memory.sensitivity,
            review_required=False,
            reviewed_by_id=approval.decided_by_id if approval is not None else None,
            reviewed_at=approval.decided_at if approval is not None else None,
            injection_findings=list(memory.injection_findings or []),
            tags=list(memory.tags or []),
            expires_at=memory.expires_at,
            created_by_id=actor.user_id,
            created_by_agent_run_id=actor.agent_run_id,
        )
        db.add(promoted)
        db.flush()
        _index(db, actor, promoted)
        _emit_written(db, actor, promoted, promoted_from=str(memory.id))
    db.execute(
        insert(MemoryLink)
        .values(
            id=uuid.uuid4(),
            organization_id=memory.organization_id,
            memory_id=memory.id,
            target_type="memory",
            target_id=promoted.id,
            relation="promoted_to",
            confidence=1.0,
        )
        .on_conflict_do_nothing(constraint="uq_memory_links_edge")
    )
    audit(
        db,
        actor,
        MEMORY_PROMOTED,
        "memory",
        memory.id,
        after={"promoted_memory_id": str(promoted.id), "scope": scope, "approval_id": approval and str(approval.id)},
    )
    return promoted


def promote_memory(db: Session, actor: Actor, memory_id: uuid.UUID | str, data: MemoryPromoteIn) -> PromotionResult:
    """Promote an ACTIVE memory to a broader scope, subject to the ``memory.promote`` policy."""
    memory = get_memory(db, actor, memory_id)
    if memory.status != MemoryStatus.ACTIVE:
        raise ValidationFailed("Only ACTIVE memories can be promoted", code="memory_not_active")
    if memory.sensitivity == "restricted":
        raise ValidationFailed("Restricted memories cannot be promoted to a shared scope")
    project, workspace_id = _promotion_target(db, actor, memory, data.scope, data.project_id)
    mission = db.get(Mission, memory.mission_id) if memory.mission_id else None
    from aegis_api.lab.governance.policies import evaluate_policy

    policy_project = memory.project_id or (project.id if project else None)
    decision = evaluate_policy(
        db,
        actor,
        PROMOTE_ACTION,
        {"source_trust": memory.trust_level, "scope": data.scope, "from_scope": memory.scope},
        project_id=policy_project if mission is None else None,
        mission=mission,
    )
    if decision.denied:
        raise PolicyDenied("; ".join(decision.reasons) or "Memory promotion is denied by policy")
    if decision.requires_approval:
        from aegis_api.lab.governance.approvals import request_approval

        approval = request_approval(
            db,
            actor,
            action=PROMOTE_ACTION,
            subject_type="memory",
            subject_id=memory.id,
            title=f"Promote memory '{memory.title[:120]}' to {data.scope} scope",
            payload={
                "target_scope": data.scope,
                "target_project_id": str(project.id) if project else None,
                "target_workspace_id": str(workspace_id) if workspace_id else None,
                "reason": data.reason,
                "trust_level": memory.trust_level,
            },
            risk_level="LOW",
            decision=decision,
            project_id=policy_project,
            mission_id=mission.id if mission else None,
            required_permission=decision.approver_permission or MEMORY_REVIEW,
        )
        return PromotionResult(status="approval_required", approval=approval)
    promoted = _apply_promotion(
        db,
        actor,
        memory,
        scope=data.scope,
        project=project,
        workspace_id=workspace_id,
        reason=data.reason,
        approval=None,
    )
    return PromotionResult(status="promoted", memory=promoted)


def on_promotion_decided(db: Session, actor: Actor, approval: Approval) -> None:
    """Approval hook (``memory.promote``): apply an approved promotion with the approver as actor."""
    if approval.subject_type != "memory" or approval.status != ApprovalStatus.APPROVED:
        return
    memory = db.get(Memory, _uuid(approval.subject_id, "Memory"))
    if memory is None or memory.organization_id != approval.organization_id:
        return
    if memory.status != MemoryStatus.ACTIVE:
        log.info("memory_promotion_skipped", memory_id=str(memory.id), status=memory.status)
        return
    payload = approval.request_payload or {}
    scope = str(payload.get("target_scope") or "")
    if scope not in ("project", "workspace", "organization"):
        return
    project = (
        db.get(Project, _uuid(payload["target_project_id"], "Project")) if payload.get("target_project_id") else None
    )
    workspace_raw = payload.get("target_workspace_id")
    workspace_id = _uuid(workspace_raw, "Workspace") if workspace_raw else None
    _apply_promotion(
        db,
        actor,
        memory,
        scope=scope,
        project=project,
        workspace_id=workspace_id,
        reason=str(payload.get("reason") or approval.decision_reason or ""),
        approval=approval,
    )


def register_hooks() -> None:
    from aegis_api.lab.governance.approvals import register_approval_hook

    register_approval_hook(PROMOTE_ACTION, on_promotion_decided)


register_hooks()
