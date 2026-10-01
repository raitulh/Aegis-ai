from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.core.deps import Actor, get_actor, require_user, require_verified_user
from app.core.errors import AppError, NotFound
from app.core.pagination import Page, PageParams, make_page
from app.core.rate_limit import enforce, limit_by_ip
from app.core.schemas import Message, Schema
from app.core.security import make_signed_payload, new_token, read_signed_payload
from app.integrations.oauth import GitHubOAuth
from app.models.user import User
from app.modules.opensource import service

router = APIRouter(prefix="/opensource", tags=["open source"])

LINK_COOKIE = "db_ghlink"
_LINK_PATH = "/api/v1/opensource/github"


class RepoIn(Schema):
    url: str = Field(min_length=5, max_length=300)


@router.get("/overview")
def overview(actor: Actor = Depends(get_actor)) -> dict[str, Any]:
    return service.hub_overview(actor)


@router.get("/repos", response_model=Page[dict])
def repos(params: PageParams = Depends(), q: str | None = Query(None, max_length=80), language: str | None = Query(None, max_length=40),
          sort: str = Query("stars", pattern="^(stars|updated|name)$"), actor: Actor = Depends(get_actor)) -> dict:
    items, total = service.list_repos(actor, params, q=q, language=language, sort=sort)
    return make_page(items, total, params)


@router.post("/repos", status_code=201)
def register_repo(data: RepoIn, actor: Actor = Depends(require_verified_user)) -> dict[str, Any]:
    enforce("github_register", str(actor.id), limit=10, window_seconds=3600)
    return service.repo_card(service.register_repo(actor, data.url))


@router.post("/repos/{repo_id}/sync", response_model=Message, status_code=202)
def sync_repo(repo_id: uuid.UUID, actor: Actor = Depends(require_user)) -> Message:
    service.request_sync(actor, repo_id)
    return Message(message="Sync queued.")


@router.get("/issues", response_model=Page[dict])
def issues(params: PageParams = Depends(), q: str | None = Query(None, max_length=80), beginner: bool = False,
           language: str | None = Query(None, max_length=40), promoted: bool | None = None, repo_id: uuid.UUID | None = None,
           actor: Actor = Depends(get_actor)) -> dict:
    items, total = service.list_issues(actor, params, q=q, beginner=beginner, language=language, promoted=promoted, repo_id=repo_id)
    return make_page(items, total, params)


@router.post("/issues/{issue_id}/promote", response_model=Message)
def promote(issue_id: uuid.UUID, promoted: bool = Query(True), actor: Actor = Depends(require_user)) -> Message:
    service.promote_issue(actor, issue_id, promoted)
    return Message(message="Issue promoted." if promoted else "Issue unpromoted.")


# ----------------------------------------------------------------------------- GitHub account linking


@router.get("/github/account")
def github_account(actor: Actor = Depends(require_user)) -> dict[str, Any]:
    return service.account_status(actor)


@router.get("/github/connect", dependencies=[Depends(limit_by_ip("github_connect", 20, 60))])
def github_connect(actor: Actor = Depends(require_user)) -> RedirectResponse:
    if not settings.github_oauth_enabled:
        raise NotFound("GitHub integration is not configured on this deployment.", code="github_disabled")
    state = new_token(24)
    redirect_uri = f"{settings.WEB_BASE_URL}{_LINK_PATH}/callback"
    resp = RedirectResponse(GitHubOAuth().authorize_url(state, redirect_uri, "link"), status_code=302)
    cookie = make_signed_payload({"s": state, "u": str(actor.id)}, "github_link", 600)
    resp.set_cookie(LINK_COOKIE, cookie, max_age=600, httponly=True, secure=settings.COOKIE_SECURE, samesite="lax", path=_LINK_PATH)
    return resp


@router.get("/github/callback")
def github_callback(request: Request, code: str | None = Query(None, max_length=512), state: str | None = Query(None, max_length=128),
                    error: str | None = Query(None, max_length=64), actor: Actor = Depends(get_actor)) -> RedirectResponse:
    done = f"{settings.WEB_BASE_URL}/settings/integrations"
    if error or not code or not state:  # the user declined on GitHub's consent screen
        resp = RedirectResponse(f"{done}?github=access_denied", status_code=302)
        resp.delete_cookie(LINK_COOKIE, path=_LINK_PATH)
        return resp
    stored = read_signed_payload(request.cookies.get(LINK_COOKIE, ""), "github_link")
    if (not settings.github_oauth_enabled or stored is None or stored.get("s") != state or not actor.is_authenticated
            or stored.get("u") != str(actor.id)):
        return RedirectResponse(f"{done}?github=state_error", status_code=302)
    redirect_uri = f"{settings.WEB_BASE_URL}{_LINK_PATH}/callback"
    try:
        profile = GitHubOAuth().exchange(code, redirect_uri)
        user = actor.db.get(User, actor.id)
        assert user is not None
        service.link_account(actor.db, user, profile)
        target = f"{done}?github=connected"
    except AppError as exc:
        actor.db.rollback()
        target = f"{done}?github={exc.code}"
    resp = RedirectResponse(target, status_code=302)
    resp.delete_cookie(LINK_COOKIE, path=_LINK_PATH)
    return resp


@router.delete("/github/account", response_model=Message)
def github_disconnect(actor: Actor = Depends(require_user)) -> Message:
    service.unlink_account(actor)
    return Message(message="GitHub disconnected. Previously synced public contribution records remain on GitHub, "
                           "but are no longer attributed to your profile.")


# ----------------------------------------------------------------------------- webhooks


@router.post("/github/webhook", include_in_schema=True, summary="GitHub webhook receiver (signature required)")
async def github_webhook(request: Request, x_github_event: str = Header(..., max_length=40),
                         x_github_delivery: str = Header(..., max_length=80),
                         x_hub_signature_256: str | None = Header(None, max_length=100),
                         db: Session = Depends(get_db)) -> JSONResponse:
    body = await request.body()
    if not settings.GITHUB_WEBHOOK_SECRET:
        return JSONResponse({"error": {"code": "webhooks_disabled", "message": "Webhooks are not configured."}}, status_code=404)
    if not service.verify_signature(body, x_hub_signature_256):
        return JSONResponse({"error": {"code": "invalid_signature", "message": "Signature verification failed."}}, status_code=401)
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        return JSONResponse({"error": {"code": "invalid_payload", "message": "Invalid JSON."}}, status_code=400)
    from starlette.concurrency import run_in_threadpool

    status = await run_in_threadpool(service.handle_webhook, db, delivery_id=x_github_delivery, event=x_github_event,
                                     payload=payload if isinstance(payload, dict) else {})
    return JSONResponse({"status": status}, status_code=202 if status != "failed" else 500)
