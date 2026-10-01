from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import Response

from app.core.deps import Actor, get_actor, require_user, require_verified_user
from app.core.pagination import Page, PageParams, make_page
from app.core.schemas import Message
from app.modules.competitions import content, service
from app.modules.competitions.schemas import (
    AnnouncementIn,
    AnnouncementOut,
    AwardCategoryIn,
    AwardCategoryOut,
    AwardWinnerIn,
    CompetitionCard,
    CompetitionDetail,
    CompetitionManage,
    CompetitionWrite,
    EvaluationAssetOut,
    JoinIn,
    OrganizerCompetitionRow,
    ParticipantOut,
    PublishCheck,
    ReasonIn,
    ScheduleItemIn,
    ScheduleItemOut,
    SponsorIn,
    StaffIn,
)

router = APIRouter(prefix="/competitions", tags=["competitions"])


@router.get("", response_model=Page[CompetitionCard])
def list_competitions(
    params: PageParams = Depends(),
    q: str | None = Query(None, max_length=100),
    status: str | None = Query(None, pattern="^(upcoming|active|registration_closed|completed|archived)$"),
    task_type: str | None = Query(None, max_length=32),
    event_type: str | None = Query(None, max_length=32),
    difficulty: str | None = Query(None, max_length=16),
    org: str | None = Query(None, max_length=80),
    prize: str | None = Query(None, pattern="^(yes|no)$"),
    participation: str | None = Query(None, pattern="^(individual|team)$"),
    tag: str | None = Query(None, max_length=48),
    sort: str = Query("relevance", pattern="^(relevance|start|end|popular|updated)$"),
    actor: Actor = Depends(get_actor),
) -> dict:
    items, total = service.list_competitions(actor, params, q=q, status=status, task_type=task_type, event_type=event_type,
                                             difficulty=difficulty, org=org, prize=prize, participation=participation,
                                             tag=tag, sort=sort)
    return make_page(items, total, params)


@router.get("/organizing", response_model=list[OrganizerCompetitionRow])
def organizing(actor: Actor = Depends(require_user)) -> list[OrganizerCompetitionRow]:
    pairs = service.organizer_competitions(actor)
    cards = {c.id: c for c in service.build_cards(actor.db, [p[0] for p in pairs])}
    health = service.submission_health(actor.db, [p[0].id for p in pairs])
    return [OrganizerCompetitionRow(card=cards[c.id], roles=roles, submissions_24h=health.get(c.id, {}).get("submissions_24h", 0),
                                    pending_submissions=health.get(c.id, {}).get("pending", 0),
                                    failed_submissions=health.get(c.id, {}).get("failed", 0),
                                    last_submission_at=health.get(c.id, {}).get("last")) for c, roles in pairs]


@router.post("", response_model=CompetitionDetail, status_code=201)
def create(data: CompetitionWrite, actor: Actor = Depends(require_verified_user)) -> CompetitionDetail:
    comp = service.create_competition(actor, data)
    return service.build_detail(actor, comp)


@router.get("/{slug}", response_model=CompetitionDetail)
def detail(slug: str, actor: Actor = Depends(get_actor)) -> CompetitionDetail:
    comp = service.load_visible(actor, slug)
    service.record_view(actor, comp)
    return service.build_detail(actor, comp)


@router.patch("/{slug}", response_model=CompetitionDetail)
def update(slug: str, data: CompetitionWrite, actor: Actor = Depends(require_user)) -> CompetitionDetail:
    comp = service.load_managed(actor, slug)
    service.update_competition(actor, comp, data)
    return service.build_detail(actor, comp)


@router.get("/{slug}/manage", response_model=CompetitionManage)
def manage(slug: str, actor: Actor = Depends(require_user)) -> CompetitionManage:
    return content.manage_view(actor, service.load_managed(actor, slug))


@router.get("/{slug}/publish-checks", response_model=list[PublishCheck])
def publish_checks(slug: str, actor: Actor = Depends(require_user)) -> list[PublishCheck]:
    return service.publish_checks(actor.db, service.load_managed(actor, slug))


@router.post("/{slug}/publish", response_model=CompetitionDetail)
def publish(slug: str, actor: Actor = Depends(require_user)) -> CompetitionDetail:
    comp = service.publish(actor, service.load_managed(actor, slug))
    return service.build_detail(actor, comp)


@router.post("/{slug}/archive", response_model=CompetitionDetail)
def archive(slug: str, data: ReasonIn, actor: Actor = Depends(require_user)) -> CompetitionDetail:
    comp = service.archive(actor, service.load_managed(actor, slug), data.reason)
    return service.build_detail(actor, comp)


@router.post("/{slug}/clone", response_model=CompetitionDetail, status_code=201)
def clone(slug: str, actor: Actor = Depends(require_user)) -> CompetitionDetail:
    return service.build_detail(actor, service.clone(actor, service.load_managed(actor, slug)))


@router.post("/{slug}/freeze", response_model=Message)
def freeze(slug: str, data: ReasonIn, actor: Actor = Depends(require_user)) -> Message:
    comp = service.get_by_slug(actor.db, slug)
    if comp is None:
        from app.core.errors import NotFound

        raise NotFound()
    service.set_frozen(actor, comp, True, data.reason)
    return Message(message="Competition frozen.")


@router.post("/{slug}/unfreeze", response_model=Message)
def unfreeze(slug: str, data: ReasonIn, actor: Actor = Depends(require_user)) -> Message:
    comp = service.get_by_slug(actor.db, slug)
    if comp is None:
        from app.core.errors import NotFound

        raise NotFound()
    service.set_frozen(actor, comp, False, data.reason)
    return Message(message="Competition unfrozen.")


@router.post("/{slug}/invite-code", response_model=dict)
def rotate_invite_code(slug: str, actor: Actor = Depends(require_user)) -> dict:
    return {"invite_code": service.rotate_invite_code(actor, service.load_managed(actor, slug))}


@router.post("/{slug}/join", response_model=CompetitionDetail)
def join(slug: str, data: JoinIn, actor: Actor = Depends(require_user)) -> CompetitionDetail:
    comp = service.load_visible(actor, slug)
    service.join(actor, comp, data.accept_rules, data.invite_code)
    return service.build_detail(actor, comp)


@router.post("/{slug}/withdraw", response_model=Message)
def withdraw(slug: str, actor: Actor = Depends(require_user)) -> Message:
    service.withdraw(actor, service.load_visible(actor, slug))
    return Message(message="You have left the competition.")


@router.get("/{slug}/participants", response_model=Page[ParticipantOut])
def participants(slug: str, params: PageParams = Depends(), actor: Actor = Depends(require_user)) -> dict:
    comp = service.load_managed(actor, slug)
    items, total = content.list_participants(actor, comp, params.page_size, params.offset)
    return make_page(items, total, params)


@router.post("/{slug}/staff", response_model=Message)
def add_staff(slug: str, data: StaffIn, actor: Actor = Depends(require_user)) -> Message:
    content.add_staff(actor, service.load_managed(actor, slug), data)
    return Message(message="Staff updated.")


@router.delete("/{slug}/staff/{user_id}/{role}", response_model=Message)
def remove_staff(slug: str, user_id: uuid.UUID, role: str, actor: Actor = Depends(require_user)) -> Message:
    content.remove_staff(actor, service.load_managed(actor, slug), user_id, role)
    return Message(message="Staff removed.")


@router.post("/{slug}/sponsors", response_model=Message)
def add_sponsor(slug: str, data: SponsorIn, actor: Actor = Depends(require_user)) -> Message:
    content.add_sponsor(actor, service.load_managed(actor, slug), data)
    return Message(message="Sponsor saved.")


@router.delete("/{slug}/sponsors/{org_id}", response_model=Message)
def remove_sponsor(slug: str, org_id: uuid.UUID, actor: Actor = Depends(require_user)) -> Message:
    content.remove_sponsor(actor, service.load_managed(actor, slug), org_id)
    return Message(message="Sponsor removed.")


@router.get("/{slug}/announcements", response_model=list[AnnouncementOut])
def announcements(slug: str, actor: Actor = Depends(get_actor)) -> list[AnnouncementOut]:
    return content.list_announcements(actor, service.load_visible(actor, slug))


@router.post("/{slug}/announcements", response_model=Message, status_code=201)
def create_announcement(slug: str, data: AnnouncementIn, actor: Actor = Depends(require_user)) -> Message:
    ann = content.create_announcement(actor, service.load_visible(actor, slug), data)
    return Message(message="Announcement published." if ann.status == "published" else "Sent for organizer review.")


@router.post("/{slug}/announcements/read", response_model=Message)
def read_announcements(slug: str, actor: Actor = Depends(require_user)) -> Message:
    content.mark_announcements_read(actor, service.load_visible(actor, slug))
    return Message(message="ok")


@router.post("/{slug}/announcements/{ann_id}/review", response_model=Message)
def review_announcement(slug: str, ann_id: uuid.UUID, approve: bool = Query(...), actor: Actor = Depends(require_user)) -> Message:
    content.review_announcement(actor, service.load_visible(actor, slug), ann_id, approve)
    return Message(message="Announcement reviewed.")


@router.patch("/{slug}/announcements/{ann_id}", response_model=Message)
def update_announcement(slug: str, ann_id: uuid.UUID, pinned: bool | None = Query(None), delete: bool = Query(False),
                        actor: Actor = Depends(require_user)) -> Message:
    content.update_announcement(actor, service.load_managed(actor, slug), ann_id, pinned, delete)
    return Message(message="Announcement updated.")


@router.post("/{slug}/schedule", response_model=ScheduleItemOut, status_code=201)
def add_schedule(slug: str, data: ScheduleItemIn, actor: Actor = Depends(require_user)) -> ScheduleItemOut:
    return ScheduleItemOut.model_validate(content.add_schedule_item(actor, service.load_managed(actor, slug), data))


@router.delete("/{slug}/schedule/{item_id}", response_model=Message)
def delete_schedule(slug: str, item_id: uuid.UUID, actor: Actor = Depends(require_user)) -> Message:
    content.delete_schedule_item(actor, service.load_managed(actor, slug), item_id)
    return Message(message="Removed.")


@router.post("/{slug}/awards", response_model=AwardCategoryOut, status_code=201)
def add_award(slug: str, data: AwardCategoryIn, actor: Actor = Depends(require_user)) -> AwardCategoryOut:
    a = content.add_award_category(actor, service.load_managed(actor, slug), data)
    return AwardCategoryOut(id=a.id, name=a.name, description=a.description, position=a.position, winner_team_id=None, awarded_at=None)


@router.put("/{slug}/awards/{award_id}/winner", response_model=Message)
def set_award_winner(slug: str, award_id: uuid.UUID, data: AwardWinnerIn, actor: Actor = Depends(require_user)) -> Message:
    content.set_award_winner(actor, service.load_managed(actor, slug), award_id, data.team_id)
    return Message(message="Award updated.")


@router.delete("/{slug}/awards/{award_id}", response_model=Message)
def delete_award(slug: str, award_id: uuid.UUID, actor: Actor = Depends(require_user)) -> Message:
    content.delete_award_category(actor, service.load_managed(actor, slug), award_id)
    return Message(message="Award removed.")


@router.post("/{slug}/ground-truth", response_model=EvaluationAssetOut, status_code=201)
def upload_ground_truth(slug: str, file: UploadFile = File(...), actor: Actor = Depends(require_user)) -> EvaluationAssetOut:
    comp = service.load_managed(actor, slug)
    asset = content.upload_ground_truth(actor, comp, file.filename or "", file.file)
    return EvaluationAssetOut(id=asset.id, sha256_prefix=asset.sha256[:12], row_count=asset.row_count,
                              public_count=asset.public_count, private_count=asset.private_count, created_at=asset.created_at)


@router.get("/{slug}/participants.csv", response_class=Response)
def participants_csv(slug: str, actor: Actor = Depends(require_user)) -> Response:
    import csv
    import io

    comp = service.load_managed(actor, slug)
    rows, _ = content.list_participants(actor, comp, 100_000, 0)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["handle", "display_name", "team", "joined_at_utc", "status", "submissions"])
    for r in rows:
        w.writerow([r.user.handle, r.user.display_name, r.team_name or "", r.joined_at.isoformat(), r.status, r.submission_count])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{comp.slug}-participants.csv"'})
