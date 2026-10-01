from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from app.core.deps import Actor, get_actor
from app.core.pagination import PageParams
from app.core.rate_limit import client_ip, enforce
from app.modules.search import service

router = APIRouter(prefix="/search", tags=["search"])


@router.get("")
def search(request: Request, q: str = Query("", max_length=200), type: str | None = Query(None, pattern="^(competition|dataset|project|course|organization|user|thread)$"),
           params: PageParams = Depends(), actor: Actor = Depends(get_actor)) -> dict[str, Any]:
    enforce("search", str(actor.id) if actor.id else client_ip(request), limit=60, window_seconds=60)
    return service.search(actor, q, params, entity_type=type)


@router.get("/suggest")
def suggest(request: Request, q: str = Query("", max_length=100), actor: Actor = Depends(get_actor)) -> list[dict[str, Any]]:
    enforce("search_suggest", str(actor.id) if actor.id else client_ip(request), limit=120, window_seconds=60)
    return service.suggest(actor, q)
