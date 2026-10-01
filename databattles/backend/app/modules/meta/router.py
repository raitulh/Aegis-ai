"""Public configuration, landing-page data and health probes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.core.deps import Actor, get_actor
from app.core.markdown import render_markdown
from app.core.rate_limit import client_ip, enforce
from app.core.schemas import Schema
from app.evaluation.metrics import METRICS
from app.evaluation.registry import EVALUATORS
from app.integrations.oauth import enabled_providers
from app.models.competition import Competition
from app.models.dataset import Dataset
from app.models.enums import CompetitionVisibility, CourseStatus, Lifecycle, ProjectVisibility
from app.models.learning import Course
from app.models.org import Organization
from app.models.project import Project
from app.models.system import FeatureFlag
from app.models.user import User

router = APIRouter(prefix="/meta", tags=["meta"])


@router.get("/config")
def public_config(db: Session = Depends(get_db)) -> dict[str, Any]:
    flags = {f.key: f.enabled for f in db.scalars(select(FeatureFlag).where(FeatureFlag.is_public.is_(True)))}
    return {
        "app_name": settings.APP_NAME, "demo_mode": settings.DEMO_MODE, "env": settings.ENV,
        "auth_providers": enabled_providers(), "github_integration": settings.github_oauth_enabled,
        "limits": {"dataset_file_mb": settings.MAX_DATASET_FILE_MB, "submission_mb": settings.MAX_SUBMISSION_MB,
                   "image_mb": settings.MAX_IMAGE_MB},
        "metrics": [{"key": k, "label": m.label, "direction": m.direction, "kind": m.kind, "description": m.description} for k, m in METRICS.items()],
        "evaluators": [{"key": e.key, "label": e.label, "version": e.version, "submission_format": e.submission_format}
                       for e in EVALUATORS.values()],
        "flags": flags,
    }


@router.get("/landing")
def landing(actor: Actor = Depends(get_actor)) -> dict[str, Any]:
    db = actor.db
    from app.modules.competitions.service import build_cards

    public = (Competition.visibility == CompetitionVisibility.public) & Competition.lifecycle.in_([Lifecycle.published, Lifecycle.finalized])
    featured = db.scalars(select(Competition).where(public).order_by(Competition.participant_count.desc(), Competition.ends_at.desc())
                          .limit(6)).all()
    projects = db.scalars(select(Project).where(Project.visibility == ProjectVisibility.public, Project.taken_down.is_(False),
                                                Project.status != "draft")
                          .order_by(Project.is_featured.desc(), Project.updated_at.desc()).limit(6)).all()
    from app.modules.projects.service import card as project_card

    def count(model: Any, *where: Any) -> int:
        return db.scalar(select(func.count()).select_from(model).where(*where)) or 0

    demo_rows = count(Competition, Competition.is_demo.is_(True)) + count(User, User.is_demo.is_(True))
    return {
        "stats": {
            "competitions": count(Competition, public), "datasets": count(Dataset, Dataset.visibility == "public", Dataset.status == "active"),
            "projects": count(Project, Project.visibility == "public", Project.taken_down.is_(False)),
            "courses": count(Course, Course.status == CourseStatus.published, Course.visibility == "public"),
            "universities": count(Organization, Organization.type == "university"),
            "members": count(User, User.status == "active"),
        },
        "includes_demo_data": demo_rows > 0,
        "featured_competitions": [c.model_dump(mode="json") for c in build_cards(db, list(featured))],
        "featured_projects": [project_card(db, p) for p in projects],
    }


class MarkdownIn(Schema):
    text: str = Field(max_length=100_000)


@router.post("/markdown-preview")
def markdown_preview(data: MarkdownIn, request: Request, actor: Actor = Depends(get_actor)) -> dict[str, str]:
    """Server-side preview uses the exact sanitizer that stores content, so previews never differ from the result."""
    enforce("markdown_preview", str(actor.id) if actor.id else client_ip(request), limit=60, window_seconds=60)
    return {"html": render_markdown(data.text)}


health_router = APIRouter(tags=["health"])


@health_router.get("/healthz", include_in_schema=False)
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@health_router.get("/readyz", include_in_schema=False)
def readyz(db: Session = Depends(get_db)) -> dict[str, str]:
    db.execute(text("SELECT 1"))
    return {"status": "ready"}
