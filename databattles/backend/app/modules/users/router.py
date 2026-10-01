from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile
from pydantic import Field
from sqlalchemy import select

from app.core.deps import Actor, get_actor, require_user
from app.core.images import store_image
from app.core.rate_limit import enforce
from app.core.schemas import Message, Schema
from app.core.time import utcnow
from app.models.credential import BadgeAward, Certificate
from app.models.org import Department, Organization
from app.models.project import Upload
from app.models.submission import CompetitionResult
from app.models.user import DEFAULT_PRIVACY
from app.modules.users import service

router = APIRouter(tags=["users"])


class ProfileOut(Schema):
    id: uuid.UUID
    handle: str
    email: str
    display_name: str
    headline: str | None
    bio_md: str | None
    avatar_url: str | None
    cover_style: str
    university_id: uuid.UUID | None
    department_id: uuid.UUID | None
    graduation_year: int | None
    skills: list[str]
    interests: list[str]
    website_url: str | None
    country: str | None
    timezone: str
    open_to_opportunities: bool
    onboarding_completed_at: datetime | None


class ProfileIn(Schema):
    handle: str | None = Field(default=None, max_length=30)
    display_name: str | None = Field(default=None, max_length=80)
    headline: str | None = Field(default=None, max_length=140)
    bio_md: str | None = Field(default=None, max_length=5000)
    website_url: str | None = Field(default=None, max_length=500)
    country: str | None = Field(default=None, pattern="^[A-Z]{2}$")
    graduation_year: int | None = None
    skills: list[str] | None = Field(default=None, max_length=30)
    interests: list[str] | None = Field(default=None, max_length=30)
    university_id: uuid.UUID | None = None
    department_id: uuid.UUID | None = None
    timezone: str | None = Field(default=None, max_length=64)
    cover_style: str | None = Field(default=None, max_length=32)
    open_to_opportunities: bool | None = None


class PrivacyIn(Schema):
    show_university: bool | None = None
    show_department: bool | None = None
    show_graduation_year: bool | None = None
    show_skills: bool | None = None
    show_activity: bool | None = None
    show_contributions: bool | None = None
    show_certificates: bool | None = None
    show_badges: bool | None = None
    show_university_on_leaderboards: bool | None = None
    indexable: bool | None = None


class DashboardPrefsIn(Schema):
    hidden: list[str] = Field(default_factory=list, max_length=20)
    order: list[str] = Field(default_factory=list, max_length=20)


class AchievementVisibilityIn(Schema):
    kind: str = Field(pattern="^(certificate|badge|result)$")
    item_id: str = Field(max_length=64)
    hidden: bool


class DeleteAccountIn(Schema):
    password: str | None = Field(default=None, max_length=256)
    confirm: str = Field(pattern="^DELETE$")


def _profile_out(user: Any) -> ProfileOut:
    return ProfileOut.model_validate(user)


@router.get("/me/profile", response_model=ProfileOut, tags=["me"])
def my_profile(actor: Actor = Depends(require_user)) -> ProfileOut:
    return _profile_out(actor.user)


@router.patch("/me/profile", response_model=ProfileOut, tags=["me"])
def update_profile(data: ProfileIn, actor: Actor = Depends(require_user)) -> ProfileOut:
    enforce("profile_update", str(actor.id), limit=30, window_seconds=300)
    return _profile_out(service.update_profile(actor, data.model_dump(exclude_unset=True)))


@router.get("/me/privacy", response_model=dict[str, bool], tags=["me"])
def my_privacy(actor: Actor = Depends(require_user)) -> dict[str, bool]:
    assert actor.user is not None
    return {**DEFAULT_PRIVACY, **(actor.user.privacy or {})}


@router.patch("/me/privacy", response_model=dict[str, bool], tags=["me"])
def update_privacy(data: PrivacyIn, actor: Actor = Depends(require_user)) -> dict[str, bool]:
    return service.update_privacy(actor, {k: v for k, v in data.model_dump(exclude_unset=True).items() if v is not None})


@router.post("/me/onboarding", response_model=ProfileOut, tags=["me"])
def onboarding(data: ProfileIn, actor: Actor = Depends(require_user)) -> ProfileOut:
    return _profile_out(service.complete_onboarding(actor, data.model_dump(exclude_unset=True)))


@router.get("/me/dashboard", tags=["me"])
def my_dashboard(actor: Actor = Depends(require_user)) -> dict[str, Any]:
    return service.dashboard(actor)


@router.put("/me/dashboard-prefs", tags=["me"])
def dashboard_prefs(data: DashboardPrefsIn, actor: Actor = Depends(require_user)) -> dict[str, Any]:
    return service.set_dashboard_prefs(actor, data.hidden, data.order)


@router.post("/me/achievements/visibility", response_model=Message, tags=["me"])
def achievement_visibility(data: AchievementVisibilityIn, actor: Actor = Depends(require_user)) -> Message:
    service.set_achievement_visibility(actor, data.kind, data.item_id, data.hidden)
    return Message(message="Hidden from your public profile." if data.hidden else "Shown on your public profile.")


@router.post("/me/avatar", response_model=ProfileOut, tags=["me"])
def upload_avatar(file: UploadFile = File(...), actor: Actor = Depends(require_user)) -> ProfileOut:
    enforce("avatar_upload", str(actor.id), limit=10, window_seconds=3600)
    user = actor.user
    assert user is not None
    key, url, _, _ = store_image(file.file, max_side=512, square=True)
    actor.db.add(Upload(owner_id=user.id, purpose="avatar", storage_key=key, content_type="image/webp", size_bytes=0, attached=True))
    user.avatar_url = url
    actor.db.commit()
    return _profile_out(user)


@router.delete("/me/avatar", response_model=ProfileOut, tags=["me"])
def remove_avatar(actor: Actor = Depends(require_user)) -> ProfileOut:
    assert actor.user is not None
    actor.user.avatar_url = None
    actor.db.commit()
    return _profile_out(actor.user)


@router.post("/me/delete", response_model=Message, tags=["me"])
def delete_account(data: DeleteAccountIn, request: Request, response: Response, actor: Actor = Depends(require_user)) -> Message:
    from app.modules.auth.service import clear_session_cookie
    from app.modules.auth.service import delete_account as do_delete

    enforce("account_delete", str(actor.id), limit=5, window_seconds=3600)
    do_delete(actor.db, actor, data.password, request)
    clear_session_cookie(response)
    return Message(message="Your account was deleted. Historical competition results remain attributed to 'Deleted user'.")


@router.get("/me/export", tags=["me"], summary="Download a copy of your personal data (JSON)")
def export_data(actor: Actor = Depends(require_user)) -> dict[str, Any]:
    enforce("data_export", str(actor.id), limit=5, window_seconds=3600)
    user = actor.user
    assert user is not None
    db = actor.db
    return {
        "exported_at": utcnow().isoformat(),
        "profile": _profile_out(user).model_dump(mode="json"),
        "privacy": {**DEFAULT_PRIVACY, **(user.privacy or {})},
        "results": [{"competition_id": str(r.competition_id), "rank": r.rank, "label": r.label, "score": r.score}
                    for r in db.scalars(select(CompetitionResult).where(CompetitionResult.user_id == user.id))],
        "certificates": [{"public_id": c.public_id, "event": c.event_title, "result": c.result_label, "status": c.status}
                         for c in db.scalars(select(Certificate).where(Certificate.recipient_id == user.id))],
        "badges": [{"badge_id": str(b.badge_id), "awarded_at": b.awarded_at.isoformat()}
                   for b in db.scalars(select(BadgeAward).where(BadgeAward.user_id == user.id))],
        "public_profile": service.public_profile(actor, user.handle),
    }


@router.get("/users/{handle}")
def public_profile(handle: str, actor: Actor = Depends(get_actor)) -> dict[str, Any]:
    return service.public_profile(actor, handle)


@router.get("/users/{handle}/contributions")
def user_contributions(handle: str, actor: Actor = Depends(get_actor)) -> dict[str, Any]:
    user = service.get_user_by_handle(actor.db, handle)
    if actor.id != user.id and not user.privacy_flag("show_contributions"):
        return {"github_login": None, "merged_count": 0, "items": [], "repositories": 0, "hidden": True}
    return service.contributions(actor.db, user.id, limit=100)


class UniversityOption(Schema):
    id: uuid.UUID
    name: str
    slug: str
    email_domains: list[str]
    departments: list[dict[str, Any]]


@router.get("/universities/options", response_model=list[UniversityOption], summary="Universities for profile/onboarding pickers")
def university_options(actor: Actor = Depends(get_actor)) -> list[UniversityOption]:
    db = actor.db
    orgs = db.scalars(select(Organization).where(Organization.type == "university").order_by(Organization.name)).all()
    depts: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for d in db.scalars(select(Department).where(Department.org_id.in_([o.id for o in orgs])).order_by(Department.name)):
        depts.setdefault(d.org_id, []).append({"id": str(d.id), "name": d.name})
    return [UniversityOption(id=o.id, name=o.name, slug=o.slug, email_domains=list(o.email_domains or []),
                             departments=depts.get(o.id, [])) for o in orgs]
