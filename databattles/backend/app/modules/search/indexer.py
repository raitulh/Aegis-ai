"""Keeps the search index in sync with source records.

Only public (or organization-scoped) content is indexed. Drafts, private and
invite-only competitions, taken-down or private datasets/projects are removed
from the index. Services call these functions after writes; `reindex_all`
rebuilds everything (seed, admin tool).
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.ids import uuid7
from app.core.markdown import plain_text
from app.core.time import utcnow
from app.models.community import Thread
from app.models.competition import Competition
from app.models.dataset import Dataset
from app.models.enums import (
    CompetitionVisibility,
    ContentStatus,
    ContentVisibility,
    CourseStatus,
    Lifecycle,
    ProjectVisibility,
    UserStatus,
)
from app.models.learning import Course
from app.models.org import Organization
from app.models.project import Project
from app.models.system import SearchDocument
from app.models.user import User


def _upsert(db: Session, entity_type: str, entity_id: uuid.UUID, *, title: str, subtitle: str | None, body: str,
            url: str, visibility: str = "public", org_id: uuid.UUID | None = None, tags: list[str] | None = None) -> None:
    values = dict(title=title[:300], subtitle=(subtitle or "")[:300] or None, body=body[:20000], url=url[:300],
                  visibility=visibility, org_id=org_id, tags=(tags or [])[:12], updated_at=utcnow())
    stmt = insert(SearchDocument).values(id=uuid7(), entity_type=entity_type, entity_id=entity_id, **values)
    db.execute(stmt.on_conflict_do_update(index_elements=["entity_type", "entity_id"], set_=values))


def remove(db: Session, entity_type: str, entity_id: uuid.UUID) -> None:
    db.execute(delete(SearchDocument).where(SearchDocument.entity_type == entity_type, SearchDocument.entity_id == entity_id))


def index_competition(db: Session, c: Competition) -> None:
    listed = c.lifecycle in (Lifecycle.published, Lifecycle.finalized, Lifecycle.archived)
    if not listed or c.visibility in (CompetitionVisibility.private, CompetitionVisibility.invite_only):
        remove(db, "competition", c.id)
        return
    vis = "org" if c.visibility == CompetitionVisibility.university else "public"
    _upsert(db, "competition", c.id, title=c.title, subtitle=c.summary, body=plain_text(c.description_md),
            url=f"/competitions/{c.slug}", visibility=vis, org_id=c.host_org_id, tags=list(c.tags or []) + [c.task_type])


def index_dataset(db: Session, d: Dataset) -> None:
    if d.status != ContentStatus.active or d.visibility == ContentVisibility.private:
        remove(db, "dataset", d.id)
        return
    vis = "org" if d.visibility == ContentVisibility.org else "public"
    _upsert(db, "dataset", d.id, title=d.title, subtitle=d.subtitle, body=plain_text(d.description_md),
            url=f"/datasets/{d.slug}", visibility=vis, org_id=d.owner_org_id, tags=list(d.tags or []))


def index_project(db: Session, p: Project) -> None:
    if p.taken_down or p.visibility != ProjectVisibility.public or p.status == "draft":
        remove(db, "project", p.id)
        return
    _upsert(db, "project", p.id, title=p.title, subtitle=p.summary, body=plain_text(p.description_md), url=f"/projects/{p.slug}",
            tags=list(p.tags or []) + list(p.technologies or []))


def index_course(db: Session, c: Course) -> None:
    if c.status != CourseStatus.published or c.visibility == ContentVisibility.private:
        remove(db, "course", c.id)
        return
    vis = "org" if c.visibility == ContentVisibility.org else "public"
    _upsert(db, "course", c.id, title=c.title, subtitle=c.summary, body=plain_text(c.description_md),
            url=f"/learn/{c.slug}", visibility=vis, org_id=c.org_id, tags=list(c.tags or []) + [c.category])


def index_org(db: Session, o: Organization) -> None:
    _upsert(db, "organization", o.id, title=o.name, subtitle=o.tagline, body=plain_text(o.description_md),
            url=f"/orgs/{o.slug}", tags=[o.type])


def index_user(db: Session, u: User) -> None:
    if u.status != UserStatus.active:
        remove(db, "user", u.id)
        return
    body = " ".join(u.skills or []) if u.privacy_flag("show_skills") else ""
    _upsert(db, "user", u.id, title=u.display_name, subtitle=f"@{u.handle}" + (f" · {u.headline}" if u.headline else ""),
            body=body, url=f"/u/{u.handle}")


def index_thread(db: Session, t: Thread) -> None:
    """Only global (category) threads are indexed; competition/project threads inherit their parent's privacy."""
    if t.hidden or t.deleted_at is not None or t.competition_id is not None or t.project_id is not None:
        remove(db, "thread", t.id)
        return
    _upsert(db, "thread", t.id, title=t.title, subtitle=None, body=plain_text(t.body_md), url=f"/discussions/t/{t.id}")


def reindex_all(db: Session) -> int:
    count = 0
    for model, fn in ((Competition, index_competition), (Dataset, index_dataset), (Project, index_project),
                      (Course, index_course), (Organization, index_org), (User, index_user), (Thread, index_thread)):
        for obj in db.scalars(select(model)):
            fn(db, obj)
            count += 1
    return count
