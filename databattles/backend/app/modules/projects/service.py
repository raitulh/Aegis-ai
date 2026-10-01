"""Project showcase: portfolio projects, open-source projects and competition write-ups."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.audit import record_audit
from app.core.deps import Actor
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.core.markdown import render_markdown
from app.core.pagination import PageParams
from app.core.permissions import can_edit_project, can_view_project, listable_projects_clause, project_role
from app.core.schemas import org_mini, user_mini
from app.core.slugs import unique_slug
from app.core.time import utcnow
from app.core.validators import clean_tags, validate_external_url
from app.models.community import Thread
from app.models.competition import Competition
from app.models.dataset import Dataset
from app.models.enums import CompetitionVisibility, ProjectRole, ProjectStatus, ProjectVisibility
from app.models.github import GitHubAccount, GitHubRepository
from app.models.org import Organization
from app.models.project import Project, ProjectDataset, ProjectMedia
from app.models.project import ProjectMember as PM
from app.models.user import User
from app.modules.search.indexer import index_project, remove
from app.storage import media_url

URL_FIELDS = ("repo_url", "demo_url", "paper_url", "video_url", "docs_url")
COVER_STYLES = {"aurora", "nebula", "circuit", "dunes", "mono", "sunrise"}
MAX_MEDIA = 8


def get_by_slug(db: Session, slug: str) -> Project | None:
    return db.scalar(select(Project).where(Project.slug == slug.lower()))


def load_visible(actor: Actor, slug: str) -> Project:
    p = get_by_slug(actor.db, slug)
    if p is None or not can_view_project(actor, p):
        raise NotFound("Project not found.")
    return p


def load_editable(actor: Actor, slug: str) -> Project:
    p = load_visible(actor, slug)
    if not can_edit_project(actor, p):
        raise Forbidden("Only project owners and maintainers can do that.")
    return p


def _members(db: Session, p: Project) -> list[dict[str, Any]]:
    rows = db.execute(select(PM, User).join(User, User.id == PM.user_id).where(PM.project_id == p.id).order_by(PM.added_at)).all()
    out = []
    owner = db.get(User, p.owner_id) if p.owner_id else None
    if owner:
        out.append({"user": user_mini(owner), "role": ProjectRole.owner})
    out += [{"user": user_mini(u), "role": m.role} for m, u in rows if u.id != p.owner_id and u.status != "deleted"]
    return out


def card(db: Session, p: Project, owners: dict[uuid.UUID, User] | None = None) -> dict[str, Any]:
    owner = (owners or {}).get(p.owner_id) if p.owner_id else None  # type: ignore[arg-type]
    if owner is None and p.owner_id:
        owner = db.get(User, p.owner_id)
    cover = db.scalar(select(ProjectMedia).where(ProjectMedia.project_id == p.id).order_by(ProjectMedia.position).limit(1))
    return {
        "id": p.id, "slug": p.slug, "title": p.title, "summary": p.summary, "tags": list(p.tags or []),
        "technologies": list(p.technologies or []), "owner": user_mini(owner), "is_featured": p.is_featured,
        "is_open_source": p.is_open_source, "repo_url": p.repo_url, "demo_url": p.demo_url, "cover_style": p.cover_style,
        "cover_image_url": media_url(cover.storage_key) if cover else None, "visibility": p.visibility, "status": p.status,
        "maintainer_verified": p.maintainer_verified_at is not None, "updated_at": p.updated_at, "is_demo": p.is_demo,
    }


def list_projects(actor: Actor, params: PageParams, *, q: str | None, tag: str | None, tech: str | None, open_source: bool | None,
                  featured: bool | None, org: str | None, owner: str | None, sort: str) -> tuple[list[dict[str, Any]], int]:
    db = actor.db
    if owner == "me" and actor.is_authenticated:
        base = or_(Project.owner_id == actor.id, Project.id.in_(select(PM.project_id).where(PM.user_id == actor.id)))
    else:
        base = listable_projects_clause(actor)
    stmt = select(Project).where(base)
    if q:
        like = f"%{q.strip()[:80]}%"
        stmt = stmt.where(or_(Project.title.ilike(like), Project.summary.ilike(like)))
    if tag:
        stmt = stmt.where(Project.tags.any(tag.lower()))
    if tech:
        stmt = stmt.where(Project.technologies.any(tech.lower()))
    if open_source is not None:
        stmt = stmt.where(Project.is_open_source.is_(open_source))
    if featured:
        stmt = stmt.where(Project.is_featured.is_(True))
    if org:
        stmt = stmt.join(Organization, Organization.id == Project.org_id).where(Organization.slug == org)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    order = {"updated": Project.updated_at.desc(), "new": Project.created_at.desc(), "title": func.lower(Project.title)}[sort]
    rows = db.scalars(stmt.order_by(Project.is_featured.desc() if sort == "updated" else order, order)
                      .limit(params.page_size).offset(params.offset)).all()
    owners = {u.id: u for u in db.scalars(select(User).where(User.id.in_([p.owner_id for p in rows if p.owner_id])))}
    return [card(db, p, owners) for p in rows], total


def detail(actor: Actor, p: Project) -> dict[str, Any]:
    db = actor.db
    media = db.scalars(select(ProjectMedia).where(ProjectMedia.project_id == p.id).order_by(ProjectMedia.position)).all()
    datasets = db.scalars(select(Dataset).join(ProjectDataset, ProjectDataset.dataset_id == Dataset.id)
                          .where(ProjectDataset.project_id == p.id, Dataset.status == "active", Dataset.visibility == "public")).all()
    comp = db.get(Competition, p.competition_id) if p.competition_id else None
    if comp and not (comp.visibility == CompetitionVisibility.public and comp.lifecycle != "draft"):
        comp = None
    repo = db.get(GitHubRepository, p.github_repo_id) if p.github_repo_id else None
    org = db.get(Organization, p.org_id) if p.org_id else None
    threads = db.scalar(select(func.count()).select_from(Thread).where(Thread.project_id == p.id, Thread.hidden.is_(False),
                                                                        Thread.deleted_at.is_(None))) or 0
    role = project_role(actor, p)
    return {
        **card(db, p),
        "description_html": p.description_html, "description_md": p.description_md if can_edit_project(actor, p) else None,
        "paper_url": p.paper_url, "video_url": p.video_url, "docs_url": p.docs_url, "org": org_mini(org),
        "members": _members(db, p),
        "media": [{"id": m.id, "url": media_url(m.storage_key), "width": m.width, "height": m.height, "alt_text": m.alt_text,
                   "position": m.position} for m in media],
        "datasets": [{"slug": d.slug, "title": d.title} for d in datasets],
        "competition": {"slug": comp.slug, "title": comp.title} if comp else None,
        "repository": {"full_name": repo.full_name, "html_url": repo.html_url, "stars": repo.stars, "forks": repo.forks,
                       "open_issues": repo.open_issues, "language": repo.language, "license": repo.license,
                       "last_synced_at": repo.last_synced_at, "sync_status": repo.sync_status, "is_demo": repo.source == "seed"}
        if repo and not repo.is_private else None,
        "discussion_count": threads, "taken_down": p.taken_down, "takedown_reason": p.takedown_reason if role else None,
        "viewer": {"role": role, "can_edit": can_edit_project(actor, p), "is_owner": role == ProjectRole.owner,
                   "can_moderate": actor.is_moderator},
        "created_at": p.created_at,
    }


def _apply(actor: Actor, p: Project, data: dict[str, Any]) -> None:
    db = actor.db
    if "title" in data and data["title"] is not None:
        title = data["title"].strip()
        if len(title) < 3:
            raise ValidationFailed(details={"fields": {"title": "Enter at least 3 characters."}})
        p.title = title[:140]
    if "summary" in data and data["summary"] is not None:
        p.summary = data["summary"].strip()[:280]
    if "description_md" in data and data["description_md"] is not None:
        p.description_md = data["description_md"]
        p.description_html = render_markdown(p.description_md)
    for key in URL_FIELDS:
        if key in data:
            setattr(p, key, validate_external_url(data[key], key))
    if "tags" in data and data["tags"] is not None:
        p.tags = clean_tags(data["tags"])
    if "technologies" in data and data["technologies"] is not None:
        p.technologies = clean_tags(data["technologies"], limit=20)
    if "visibility" in data and data["visibility"]:
        p.visibility = ProjectVisibility(data["visibility"])
    if "status" in data and data["status"]:
        p.status = ProjectStatus(data["status"])
    if "is_open_source" in data and data["is_open_source"] is not None:
        p.is_open_source = bool(data["is_open_source"])
    if "cover_style" in data and data["cover_style"]:
        if data["cover_style"] not in COVER_STYLES:
            raise ValidationFailed(details={"fields": {"cover_style": "Unknown style."}})
        p.cover_style = data["cover_style"]
    if "org_id" in data:
        if data["org_id"] and not actor.is_org_member(data["org_id"]) and not actor.is_admin:
            raise Forbidden("You can only attach projects to organizations you belong to.")
        p.org_id = data["org_id"]
    if "competition_slug" in data:
        slug = data["competition_slug"]
        if slug:
            comp = db.scalar(select(Competition).where(Competition.slug == slug))
            if comp is None or comp.visibility != CompetitionVisibility.public or comp.lifecycle == "draft":
                raise ValidationFailed(details={"fields": {"competition_slug": "Choose a public competition."}})
            p.competition_id = comp.id
        else:
            p.competition_id = None
    if "dataset_slugs" in data and data["dataset_slugs"] is not None:
        db.execute(ProjectDataset.__table__.delete().where(ProjectDataset.project_id == p.id))
        for s in data["dataset_slugs"][:10]:
            ds = db.scalar(select(Dataset).where(Dataset.slug == s, Dataset.visibility == "public", Dataset.status == "active"))
            if ds is None:
                raise ValidationFailed(details={"fields": {"dataset_slugs": f"Unknown public dataset: {s}"}})
            db.add(ProjectDataset(project_id=p.id, dataset_id=ds.id))


def create_project(actor: Actor, data: dict[str, Any]) -> Project:
    db = actor.db
    title = (data.get("title") or "").strip()
    if len(title) < 3:
        raise ValidationFailed(details={"fields": {"title": "Enter at least 3 characters."}})
    p = Project(slug=unique_slug(db, Project, title, requested=data.get("slug")), title=title, owner_id=actor.id,
                status=ProjectStatus.active, visibility=ProjectVisibility.public)
    db.add(p)
    db.flush()
    _apply(actor, p, data)
    db.add(PM(project_id=p.id, user_id=actor.id, role=ProjectRole.owner))
    _maybe_verify_maintainer(db, p)
    record_audit(db, actor.id, "project.create", target_type="project", target_id=p.id)
    _reindex(db, p)
    db.commit()
    return p


def _reindex(db: Session, p: Project) -> None:
    if p.visibility == ProjectVisibility.public and not p.taken_down and p.status != ProjectStatus.draft:
        index_project(db, p)
    else:
        remove(db, "project", p.id)


def update_project(actor: Actor, p: Project, data: dict[str, Any]) -> Project:
    _apply(actor, p, data)
    _maybe_verify_maintainer(actor.db, p)
    _reindex(actor.db, p)
    record_audit(actor.db, actor.id, "project.update", target_type="project", target_id=p.id, meta={"fields": sorted(data.keys())})
    actor.db.commit()
    return p


def delete_project(actor: Actor, p: Project) -> None:
    if project_role(actor, p) != ProjectRole.owner and not actor.is_admin:
        raise Forbidden("Only the project owner can delete it.")
    record_audit(actor.db, actor.id, "project.delete", target_type="project", target_id=p.id, meta={"title": p.title})
    remove(actor.db, "project", p.id)
    actor.db.delete(p)
    actor.db.commit()


def add_member(actor: Actor, p: Project, handle: str, role: str) -> None:
    db = actor.db
    if project_role(actor, p) != ProjectRole.owner and not actor.is_admin:
        raise Forbidden("Only the owner can manage members.")
    if role not in (ProjectRole.maintainer, ProjectRole.contributor):
        raise ValidationFailed(details={"fields": {"role": "Choose maintainer or contributor."}})
    user = db.scalar(select(User).where(User.handle == handle.lower().lstrip("@"), User.status == "active"))
    if user is None:
        raise NotFound("No user with that handle.")
    existing = db.scalar(select(PM).where(PM.project_id == p.id, PM.user_id == user.id))
    if existing:
        if existing.role == ProjectRole.owner:
            raise Conflict("That user owns the project.")
        existing.role = role
    else:
        if (db.scalar(select(func.count()).select_from(PM).where(PM.project_id == p.id)) or 0) >= 50:
            raise Conflict("Projects can have at most 50 members.", code="member_limit")
        db.add(PM(project_id=p.id, user_id=user.id, role=role))
    from app.models.enums import NotificationKind
    from app.modules.notifications.service import notify

    notify(db, user.id, NotificationKind.team_update, f"You were added to the project {p.title}", link=f"/projects/{p.slug}",
           dedupe_key=f"projmember:{p.id}:{user.id}:{role}")
    record_audit(db, actor.id, "project.member_add", target_type="project", target_id=p.id, meta={"user_id": str(user.id), "role": role})
    db.commit()


def remove_member(actor: Actor, p: Project, user_id: uuid.UUID) -> None:
    db = actor.db
    is_self = actor.id == user_id
    if not is_self and project_role(actor, p) != ProjectRole.owner and not actor.is_admin:
        raise Forbidden("Only the owner can manage members.")
    if user_id == p.owner_id:
        raise Conflict("Transfer ownership before removing the owner.", code="owner")
    m = db.scalar(select(PM).where(PM.project_id == p.id, PM.user_id == user_id))
    if m is None:
        raise NotFound()
    db.delete(m)
    record_audit(db, actor.id, "project.member_remove", target_type="project", target_id=p.id, meta={"user_id": str(user_id)})
    db.commit()


def transfer_ownership(actor: Actor, p: Project, user_id: uuid.UUID) -> None:
    db = actor.db
    if project_role(actor, p) != ProjectRole.owner and not actor.is_admin:
        raise Forbidden()
    target = db.scalar(select(PM).where(PM.project_id == p.id, PM.user_id == user_id))
    if target is None:
        raise ValidationFailed("The new owner must already be a project member.")
    old = db.scalar(select(PM).where(PM.project_id == p.id, PM.user_id == p.owner_id))
    if old:
        old.role = ProjectRole.maintainer
    target.role = ProjectRole.owner
    p.owner_id = user_id
    p.maintainer_verified_at = None
    record_audit(db, actor.id, "project.transfer", target_type="project", target_id=p.id, meta={"to": str(user_id)})
    db.commit()


def add_media(actor: Actor, p: Project, key: str, width: int, height: int, alt_text: str) -> ProjectMedia:
    db = actor.db
    count = db.scalar(select(func.count()).select_from(ProjectMedia).where(ProjectMedia.project_id == p.id)) or 0
    if count >= MAX_MEDIA:
        raise Conflict(f"Projects can have up to {MAX_MEDIA} images.", code="media_limit")
    m = ProjectMedia(project_id=p.id, storage_key=key, content_type="image/webp", width=width, height=height,
                     alt_text=alt_text[:200], position=count)
    db.add(m)
    db.commit()
    return m


def update_media(actor: Actor, p: Project, media_id: uuid.UUID, alt_text: str | None, position: int | None) -> None:
    db = actor.db
    m = db.get(ProjectMedia, media_id)
    if m is None or m.project_id != p.id:
        raise NotFound()
    if alt_text is not None:
        m.alt_text = alt_text[:200]
    if position is not None:
        items = list(db.scalars(select(ProjectMedia).where(ProjectMedia.project_id == p.id).order_by(ProjectMedia.position)))
        items.remove(m)
        items.insert(max(0, min(position, len(items))), m)
        for i, it in enumerate(items):
            it.position = i
    db.commit()


def delete_media(actor: Actor, p: Project, media_id: uuid.UUID) -> None:
    db = actor.db
    m = db.get(ProjectMedia, media_id)
    if m is None or m.project_id != p.id:
        raise NotFound()
    db.delete(m)
    db.commit()


def set_featured(actor: Actor, p: Project, featured: bool) -> None:
    if not actor.is_moderator:
        raise Forbidden()
    p.is_featured = featured
    record_audit(actor.db, actor.id, "project.feature" if featured else "project.unfeature", target_type="project", target_id=p.id)
    actor.db.commit()


def takedown(actor: Actor, p: Project, reason: str, restore: bool) -> None:
    if not actor.is_moderator:
        raise Forbidden()
    p.taken_down = not restore
    p.takedown_reason = None if restore else reason[:500]
    _reindex(actor.db, p)
    record_audit(actor.db, actor.id, "project.restore" if restore else "project.takedown", target_type="project", target_id=p.id,
                 reason=reason)
    actor.db.commit()


def _maybe_verify_maintainer(db: Session, p: Project) -> None:
    """A project is 'maintainer verified' when it links a registered GitHub repository and the owner's linked
    GitHub account owns that repository. The claim is re-evaluated on every save."""
    p.maintainer_verified_at = None
    if not p.repo_url or not p.owner_id:
        p.github_repo_id = None
        return
    from app.modules.opensource.service import parse_repo_url

    full_name = parse_repo_url(p.repo_url)
    if not full_name:
        p.github_repo_id = None
        return
    repo = db.scalar(select(GitHubRepository).where(func.lower(GitHubRepository.full_name) == full_name.lower()))
    p.github_repo_id = repo.id if repo else None
    gh = db.scalar(select(GitHubAccount).where(GitHubAccount.user_id == p.owner_id))
    if repo and gh and gh.login.lower() == repo.owner_login.lower():
        p.maintainer_verified_at = utcnow()
