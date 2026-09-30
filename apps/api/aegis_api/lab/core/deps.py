"""FastAPI dependencies for lab routers: the acting identity, permission guards, rate-limit tiers."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Literal

import structlog
from fastapi import Depends, Request
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.db.session import session_scope
from aegis_api.deps import get_current_principal
from aegis_api.lab.core.actor import Actor
from aegis_api.ratelimit import get_limiter
from aegis_api.security.context import Principal

LabTier = Literal["research", "execution", "model", "download"]


def get_actor(request: Request, principal: Principal = Depends(get_current_principal)) -> Actor:
    actor = Actor.from_principal(principal)
    # Contextual ids for structured logs (only after successful authentication).
    structlog.contextvars.bind_contextvars(
        tenant_id=str(actor.organization_id), user_id=str(actor.user_id) if actor.user_id else None
    )
    trace_id = getattr(request.state, "trace_id", None)
    if trace_id:
        from dataclasses import replace

        actor = replace(actor, trace_id=trace_id)
    return actor


def require_actor(*permissions: str) -> Callable[..., Actor]:
    """Dependency: the actor, after checking organization-level permissions."""

    def _dep(actor: Actor = Depends(get_actor)) -> Actor:
        actor.require(*permissions)
        return actor

    return _dep


def _tier_limit(tier: LabTier) -> int:
    s = get_settings()
    return {
        "research": s.rate_limit_research_per_min,
        "execution": s.rate_limit_execution_per_min,
        "model": s.rate_limit_model_per_min,
        "download": s.rate_limit_download_per_min,
    }[tier]


def check_rate(tier: LabTier, identity: str) -> None:
    get_limiter().check_custom(f"lab-{tier}", identity, _tier_limit(tier))


def lab_rate_limit(tier: LabTier) -> Callable[..., None]:
    """Per-tenant AND per-credential limits for expensive operations."""

    def _dep(actor: Actor = Depends(get_actor)) -> None:
        check_rate(tier, f"org:{actor.organization_id}")
        credential = actor.api_key_id or actor.user_id
        check_rate(tier, f"cred:{credential}")

    return _dep


@contextmanager
def tenant_uow(actor_or_org: Actor | uuid.UUID, user_id: uuid.UUID | None = None) -> Iterator[Session]:
    """A short-lived RLS-scoped unit of work for workers/activities. Commits on success.

    Never hold one open across LLM calls, network calls, Temporal waits or experiment execution.
    """
    org_id = actor_or_org.organization_id if isinstance(actor_or_org, Actor) else actor_or_org
    uid = actor_or_org.user_id if isinstance(actor_or_org, Actor) else user_id
    with session_scope(org_id, uid) as session:
        yield session
