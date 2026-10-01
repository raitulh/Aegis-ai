from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import Response
from pydantic import EmailStr, Field

from app.core.deps import Actor, get_actor, require_user, require_verified_user
from app.core.images import store_image
from app.core.pagination import Page, PageParams, make_page
from app.core.rate_limit import enforce
from app.core.schemas import Message, OrgMini, Schema, UserMini
from app.modules.orgs import service

router = APIRouter(prefix="/orgs", tags=["organizations"])


class OrgCard(Schema):
    id: uuid.UUID
    slug: str
    name: str
    type: str
    tagline: str | None
    logo_url: str | None
    accent_color: str | None
    country: str | None
    city: str | None
    verification_status: str
    member_count: int
    is_demo: bool


class OrgWrite(Schema):
    name: str | None = Field(default=None, max_length=160)
    slug: str | None = Field(default=None, max_length=80)
    type: str | None = Field(default=None, pattern="^(university|club|community|sponsor|company)$")
    tagline: str | None = Field(default=None, max_length=200)
    description_md: str | None = Field(default=None, max_length=20_000)
    website_url: str | None = Field(default=None, max_length=500)
    country: str | None = Field(default=None, max_length=2)
    city: str | None = Field(default=None, max_length=80)
    accent_color: str | None = Field(default=None, max_length=9)
    allow_membership_requests: bool | None = None
    email_domains: list[str] | None = Field(default=None, max_length=20)


class NoteIn(Schema):
    note: str | None = Field(default=None, max_length=500)
    department_id: uuid.UUID | None = None


class ReasonIn(Schema):
    reason: str = Field(min_length=3, max_length=1000)


class DomainEmailIn(Schema):
    email: EmailStr


class TokenIn(Schema):
    token: str = Field(min_length=10, max_length=200)


class ReviewIn(Schema):
    approve: bool
    note: str | None = Field(default=None, max_length=500)


class RoleIn(Schema):
    role: str = Field(pattern="^(owner|admin|manager|member)$")


class DepartmentIn(Schema):
    name: str = Field(min_length=2, max_length=160)
    description: str | None = Field(default=None, max_length=2000)


class DepartmentOut(Schema):
    id: uuid.UUID
    slug: str
    name: str
    description: str | None


class InviteIn(Schema):
    email: EmailStr | None = None
    role: str = Field(default="member", pattern="^(admin|manager|member)$")
    max_uses: int = Field(default=1, ge=1, le=1000)
    days: int = Field(default=14, ge=1, le=90)


class InviteOut(Schema):
    id: uuid.UUID
    email: str | None
    role: str
    expires_at: datetime
    max_uses: int
    uses: int
    revoked_at: datetime | None
    created_at: datetime


class InviteCreated(InviteOut):
    link: str


class InvitePreview(Schema):
    org: OrgMini
    role: str
    email_bound: bool
    expires_at: datetime


class RosterRow(Schema):
    id: uuid.UUID
    user: UserMini
    role: str
    status: str
    verified: bool
    verification_method: str
    department: str | None
    department_id: uuid.UUID | None
    request_note: str | None
    joined_at: datetime
    competitions_joined: int
    account_status: str


class MembershipOut(Schema):
    org: OrgMini
    role: str
    status: str
    verified: bool
    verification_method: str
    joined_at: datetime


class VerificationIn(Schema):
    status: str = Field(pattern="^(unverified|pending|verified)$")
    reason: str = Field(min_length=3, max_length=1000)


@router.get("", response_model=Page[OrgCard])
def list_orgs(params: PageParams = Depends(), q: str | None = Query(None, max_length=80),
              type: str | None = Query(None, pattern="^(university|club|community|sponsor|company)$"),
              verified: bool | None = None, actor: Actor = Depends(get_actor)) -> dict:
    items, total = service.list_orgs(actor, params, q=q, type_=type, verified=verified)
    return make_page(items, total, params)


@router.post("", status_code=201)
def create_org(data: OrgWrite, actor: Actor = Depends(require_verified_user)) -> dict[str, Any]:
    enforce("org_create", str(actor.id), limit=5, window_seconds=86400)
    org = service.create_org(actor, data.model_dump(exclude_unset=True))
    return service.detail(actor, org)


@router.get("/me/memberships", response_model=list[MembershipOut], tags=["me"])
def my_memberships(actor: Actor = Depends(require_user)) -> list[dict[str, Any]]:
    return service.my_memberships(actor)


@router.get("/invites/preview", response_model=InvitePreview)
def invite_preview(token: str = Query(min_length=10, max_length=200), actor: Actor = Depends(get_actor)) -> dict[str, Any]:
    return service.invite_preview(actor.db, token)


@router.post("/invites/accept")
def accept_invite(data: TokenIn, actor: Actor = Depends(require_verified_user)) -> dict[str, Any]:
    enforce("org_invite_accept", str(actor.id), limit=20, window_seconds=3600)
    return service.detail(actor, service.accept_invite(actor, data.token))


@router.post("/verify-email/confirm")
def confirm_domain_email(data: TokenIn, actor: Actor = Depends(require_user)) -> dict[str, Any]:
    return service.detail(actor, service.confirm_domain_verification(actor, data.token))


@router.get("/{slug}")
def org_detail(slug: str, actor: Actor = Depends(get_actor)) -> dict[str, Any]:
    return service.detail(actor, service.get_org(actor.db, slug))


@router.patch("/{slug}")
def update_org(slug: str, data: OrgWrite, actor: Actor = Depends(require_user)) -> dict[str, Any]:
    org = service.load_admin(actor, slug)
    return service.detail(actor, service.update_org(actor, org, data.model_dump(exclude_unset=True, exclude={"type", "slug"})))


@router.post("/{slug}/logo")
def upload_logo(slug: str, file: UploadFile = File(...), actor: Actor = Depends(require_user)) -> dict[str, Any]:
    org = service.load_admin(actor, slug)
    enforce("org_logo", str(actor.id), limit=10, window_seconds=3600)
    key, _, _, _ = store_image(file.file, max_side=512, square=True)
    return service.detail(actor, service.set_logo(actor, org, key))


@router.post("/{slug}/verification-request", response_model=Message)
def request_verification(slug: str, data: ReasonIn, actor: Actor = Depends(require_user)) -> Message:
    service.request_verification(actor, service.load_admin(actor, slug), data.reason)
    return Message(message="Verification requested. The platform team will review it.")


@router.put("/{slug}/verification", response_model=Message)
def set_verification(slug: str, data: VerificationIn, actor: Actor = Depends(require_user)) -> Message:
    service.set_verification(actor, service.get_org(actor.db, slug), data.status, data.reason)
    return Message(message=f"Organization marked {data.status}.")


# departments


@router.get("/{slug}/departments", response_model=list[DepartmentOut])
def departments(slug: str, actor: Actor = Depends(get_actor)) -> list[Any]:
    from sqlalchemy import select

    from app.models.org import Department

    org = service.get_org(actor.db, slug)
    return list(actor.db.scalars(select(Department).where(Department.org_id == org.id).order_by(Department.name)))


@router.post("/{slug}/departments", response_model=DepartmentOut, status_code=201)
def create_department(slug: str, data: DepartmentIn, actor: Actor = Depends(require_user)) -> Any:
    return service.create_department(actor, service.load_admin(actor, slug), data.name, data.description)


@router.patch("/{slug}/departments/{dept_id}", response_model=DepartmentOut)
def update_department(slug: str, dept_id: uuid.UUID, data: DepartmentIn, actor: Actor = Depends(require_user)) -> Any:
    return service.update_department(actor, service.load_admin(actor, slug), dept_id, data.name, data.description)


@router.delete("/{slug}/departments/{dept_id}", response_model=Message)
def delete_department(slug: str, dept_id: uuid.UUID, actor: Actor = Depends(require_user)) -> Message:
    service.delete_department(actor, service.load_admin(actor, slug), dept_id)
    return Message(message="Department removed.")


# membership


@router.post("/{slug}/membership/request", response_model=Message, status_code=201)
def request_membership(slug: str, data: NoteIn, actor: Actor = Depends(require_verified_user)) -> Message:
    enforce("org_membership_request", str(actor.id), limit=10, window_seconds=3600)
    service.request_membership(actor, service.get_org(actor.db, slug), data.note, data.department_id)
    return Message(message="Request sent. An administrator will review it.")


@router.post("/{slug}/membership/verify-email", response_model=Message, status_code=202)
def start_domain_verification(slug: str, data: DomainEmailIn, actor: Actor = Depends(require_verified_user)) -> Message:
    enforce("org_domain_verify", str(actor.id), limit=5, window_seconds=3600)
    service.start_domain_verification(actor, service.get_org(actor.db, slug), str(data.email))
    return Message(message="Check that inbox for a confirmation link.")


@router.post("/{slug}/membership/leave", response_model=Message)
def leave(slug: str, actor: Actor = Depends(require_user)) -> Message:
    service.leave(actor, service.get_org(actor.db, slug))
    return Message(message="You left the organization.")


@router.get("/{slug}/members", response_model=Page[RosterRow])
def roster(slug: str, params: PageParams = Depends(), status: str | None = Query(None, pattern="^(active|pending|rejected|removed)$"),
           role: str | None = Query(None, pattern="^(owner|admin|manager|member)$"), department_id: uuid.UUID | None = None,
           q: str | None = Query(None, max_length=60), actor: Actor = Depends(require_user)) -> dict:
    org = service.load_manager(actor, slug)
    items, total = service.roster(actor, org, params, status=status, role=role, department_id=department_id, q=q)
    return make_page(items, total, params)


@router.get("/{slug}/members.csv", response_class=Response)
def roster_csv(slug: str, actor: Actor = Depends(require_user)) -> Response:
    org = service.load_admin(actor, slug)
    return Response(service.roster_csv(actor, org), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{org.slug}-members.csv"', "Cache-Control": "no-store"})


@router.post("/{slug}/members/{membership_id}/review", response_model=Message)
def review(slug: str, membership_id: uuid.UUID, data: ReviewIn, actor: Actor = Depends(require_user)) -> Message:
    service.review_request(actor, service.load_admin(actor, slug), membership_id, data.approve, data.note)
    return Message(message="Request approved." if data.approve else "Request declined.")


@router.put("/{slug}/members/{membership_id}/role", response_model=Message)
def change_role(slug: str, membership_id: uuid.UUID, data: RoleIn, actor: Actor = Depends(require_user)) -> Message:
    service.change_role(actor, service.load_admin(actor, slug), membership_id, data.role)
    return Message(message="Role updated.")


@router.post("/{slug}/members/{membership_id}/remove", response_model=Message)
def remove_member(slug: str, membership_id: uuid.UUID, data: ReasonIn, actor: Actor = Depends(require_user)) -> Message:
    service.remove_member(actor, service.load_admin(actor, slug), membership_id, data.reason)
    return Message(message="Member removed.")


# invites


@router.get("/{slug}/invites", response_model=list[InviteOut])
def list_invites(slug: str, actor: Actor = Depends(require_user)) -> list[Any]:
    return service.list_invites(actor, service.load_admin(actor, slug))


@router.post("/{slug}/invites", response_model=InviteCreated, status_code=201)
def create_invite(slug: str, data: InviteIn, actor: Actor = Depends(require_user)) -> InviteCreated:
    org = service.load_admin(actor, slug)
    enforce("org_invite_create", str(actor.id), limit=50, window_seconds=3600)
    inv, link = service.create_invite(actor, org, email=str(data.email) if data.email else None, role=data.role,
                                      max_uses=data.max_uses, days=data.days)
    return InviteCreated(**InviteOut.model_validate(inv).model_dump(), link=link)


@router.delete("/{slug}/invites/{invite_id}", response_model=Message)
def revoke_invite(slug: str, invite_id: uuid.UUID, actor: Actor = Depends(require_user)) -> Message:
    service.revoke_invite(actor, service.load_admin(actor, slug), invite_id)
    return Message(message="Invite revoked.")


# dashboards


@router.get("/{slug}/admin/dashboard")
def admin_dashboard(slug: str, actor: Actor = Depends(require_user)) -> dict[str, Any]:
    return service.admin_dashboard(actor, service.load_manager(actor, slug))


@router.get("/{slug}/sponsor/dashboard")
def sponsor_dashboard(slug: str, actor: Actor = Depends(require_user)) -> dict[str, Any]:
    return service.sponsor_dashboard(actor, service.load_manager(actor, slug))


@router.get("/{slug}/subscription")
def subscription(slug: str, actor: Actor = Depends(require_user)) -> dict[str, Any]:
    return service.subscription_view(actor, service.load_admin(actor, slug))
