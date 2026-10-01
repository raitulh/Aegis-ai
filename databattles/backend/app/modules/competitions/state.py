"""Competition lifecycle state machine and time-derived status.

Stored lifecycle:  draft → published → finalized → archived
                   draft → archived, published → archived
Effective status (derived, never stored): draft | upcoming | active | ended | completed | archived

All comparisons use timezone-aware UTC. Boundaries: a competition accepts
submissions for starts_at <= now < ends_at (the end instant is exclusive).
"""

from __future__ import annotations

from datetime import datetime

from app.core.errors import Conflict
from app.core.time import utcnow
from app.models.competition import Competition
from app.models.enums import EffectiveStatus, Lifecycle

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    Lifecycle.draft: {Lifecycle.published, Lifecycle.archived},
    Lifecycle.published: {Lifecycle.finalized, Lifecycle.archived},
    Lifecycle.finalized: {Lifecycle.archived},
    Lifecycle.archived: set(),
}


def transition(comp: Competition, target: Lifecycle) -> None:
    if target not in ALLOWED_TRANSITIONS.get(comp.lifecycle, set()):
        raise Conflict(f"Cannot move a {comp.lifecycle} competition to {target}.", code="invalid_transition")
    comp.lifecycle = target


def effective_status(comp: Competition, now: datetime | None = None) -> EffectiveStatus:
    now = now or utcnow()
    if comp.lifecycle == Lifecycle.draft:
        return EffectiveStatus.draft
    if comp.lifecycle == Lifecycle.archived:
        return EffectiveStatus.archived
    if comp.lifecycle == Lifecycle.finalized:
        return EffectiveStatus.completed
    if comp.starts_at and now < comp.starts_at:
        return EffectiveStatus.upcoming
    if comp.ends_at and now >= comp.ends_at:
        return EffectiveStatus.ended
    return EffectiveStatus.active


def registration_open(comp: Competition, now: datetime | None = None) -> bool:
    now = now or utcnow()
    if comp.lifecycle != Lifecycle.published or comp.frozen:
        return False
    if comp.registration_opens_at and now < comp.registration_opens_at:
        return False
    if comp.registration_closes_at and now >= comp.registration_closes_at:
        return False
    return not (comp.ends_at and now >= comp.ends_at)


def submissions_open(comp: Competition, now: datetime | None = None) -> bool:
    now = now or utcnow()
    if comp.lifecycle != Lifecycle.published or comp.frozen:
        return False
    if comp.starts_at and now < comp.starts_at:
        return False
    return not (comp.ends_at and now >= comp.ends_at)


def teams_locked(comp: Competition, now: datetime | None = None) -> bool:
    now = now or utcnow()
    if comp.lifecycle in (Lifecycle.finalized, Lifecycle.archived):
        return True
    if comp.team_lock_at and now >= comp.team_lock_at:
        return True
    return bool(comp.ends_at and now >= comp.ends_at)
