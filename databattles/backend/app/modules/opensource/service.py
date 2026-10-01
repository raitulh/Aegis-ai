"""Open-source hub: GitHub account linking, repository registration, sync and webhooks.

Design notes
------------
* Repositories are keyed by GitHub's immutable numeric id; ``full_name`` is refreshed on every sync and on
  ``repository.renamed``/``transferred`` webhooks.
* Contributions are *attributed* only when a merged PR's author GitHub id matches a GitHub account the user
  linked through OAuth — self-declared GitHub usernames never count.
* Webhooks: HMAC-SHA256 signature verified with the shared secret, deliveries de-duplicated by
  ``X-GitHub-Delivery``, and out-of-order events ignored when older than the stored ``updated_at``.
* Sync respects rate limits: a rate-limited job is rescheduled for the reset time instead of retrying hot.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.audit import record_audit
from app.core.config import settings
from app.core.deps import Actor
from app.core.errors import AppError, Conflict, Forbidden, NotFound, ValidationFailed
from app.core.pagination import PageParams
from app.core.permissions import can_edit_project
from app.core.schemas import user_mini
from app.core.security import encrypt_secret
from app.core.time import ensure_aware, utcnow
from app.integrations.github.client import (
    GitHubClient,
    GitHubError,
    GitHubNotFound,
    GitHubRateLimited,
    parse_dt,
)
from app.integrations.oauth import OAuthProfile
from app.jobs.queue import PermanentJobError, TransientJobError, enqueue, job_handler
from app.models.enums import NotificationKind
from app.models.github import GitHubAccount, GitHubIssue, GitHubPullRequest, GitHubRepository, WebhookDelivery
from app.models.project import Project
from app.models.user import User
from app.modules.notifications.service import notify

_REPO_URL_RE = re.compile(r"^(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9-]{1,39})/([A-Za-z0-9._-]{1,100}?)(?:\.git)?/?(?:[#?].*)?$")
_FULL_NAME_RE = re.compile(r"^([A-Za-z0-9-]{1,39})/([A-Za-z0-9._-]{1,100})$")
BEGINNER_LABELS = {"good first issue", "good-first-issue", "beginner", "beginner friendly", "first-timers-only", "easy", "starter"}
HELP_LABELS = {"help wanted", "help-wanted"}
SYNC_JOB = "github_sync_repo"


def parse_repo_url(url: str | None) -> str | None:
    if not url:
        return None
    url = url.strip()
    m = _REPO_URL_RE.match(url) or _FULL_NAME_RE.match(url)
    if not m:
        return None
    return f"{m.group(1)}/{m.group(2)}"


# ----------------------------------------------------------------------------- account linking


def link_account(db: Session, user: User, profile: OAuthProfile) -> GitHubAccount:
    gh_id = int(profile.provider_user_id)
    other = db.scalar(select(GitHubAccount).where(GitHubAccount.github_user_id == gh_id, GitHubAccount.user_id != user.id))
    if other:
        raise Conflict("That GitHub account is already linked to another profile.", code="github_linked_elsewhere")
    acct = db.scalar(select(GitHubAccount).where(GitHubAccount.user_id == user.id))
    if acct and acct.github_user_id != gh_id:
        raise Conflict("Disconnect your current GitHub account first.", code="github_already_linked")
    if acct is None:
        acct = GitHubAccount(user_id=user.id, github_user_id=gh_id, login=profile.login or "")
        db.add(acct)
    acct.login = (profile.login or acct.login)[:64]
    acct.avatar_url = profile.avatar_url
    acct.scopes = (profile.scopes or "")[:200]
    # Only public profile scope is requested; the token is kept encrypted for future least-privilege reads.
    acct.access_token_enc = encrypt_secret(profile.access_token) if profile.access_token else None
    acct.connected_at = utcnow()
    record_audit(db, user.id, "github.link", target_type="user", target_id=user.id, meta={"login": acct.login})
    db.flush()
    _refresh_project_verification(db, user.id)
    from app.modules.credentials.badges import evaluate_user_badges

    evaluate_user_badges(db, user.id, trigger="github_linked")
    db.commit()
    return acct


def unlink_account(actor: Actor) -> None:
    db = actor.db
    acct = db.scalar(select(GitHubAccount).where(GitHubAccount.user_id == actor.id))
    if acct is None:
        raise NotFound("No GitHub account is linked.")
    db.delete(acct)
    record_audit(db, actor.id, "github.unlink", target_type="user", target_id=actor.id)
    db.flush()
    _refresh_project_verification(db, actor.id)  # type: ignore[arg-type]
    db.commit()


def _refresh_project_verification(db: Session, user_id: uuid.UUID) -> None:
    from app.modules.projects.service import _maybe_verify_maintainer

    for p in db.scalars(select(Project).where(Project.owner_id == user_id, Project.repo_url.is_not(None))):
        _maybe_verify_maintainer(db, p)


def account_status(actor: Actor) -> dict[str, Any]:
    acct = actor.db.scalar(select(GitHubAccount).where(GitHubAccount.user_id == actor.id))
    return {
        "connected": acct is not None, "login": acct.login if acct else None, "avatar_url": acct.avatar_url if acct else None,
        "connected_at": acct.connected_at if acct else None, "scopes": acct.scopes if acct else None,
        "oauth_enabled": settings.github_oauth_enabled,
    }


# ----------------------------------------------------------------------------- repositories


def _apply_repo_payload(repo: GitHubRepository, data: dict[str, Any]) -> None:
    repo.full_name = data["full_name"][:200]
    repo.owner_login = (data.get("owner") or {}).get("login", repo.owner_login or "")[:100]
    repo.name = data.get("name", repo.name or "")[:100]
    repo.description = (data.get("description") or "")[:2000] or None
    repo.language = (data.get("language") or None) and data["language"][:40]
    repo.stars = data.get("stargazers_count")
    repo.forks = data.get("forks_count")
    repo.open_issues = data.get("open_issues_count")
    repo.topics = [t[:50] for t in (data.get("topics") or [])][:20]
    repo.html_url = data.get("html_url", repo.html_url or "")[:500]
    repo.homepage = (data.get("homepage") or None) and data["homepage"][:500]
    repo.license = ((data.get("license") or {}).get("spdx_id") or None)
    repo.is_private = bool(data.get("private"))
    repo.is_archived = bool(data.get("archived"))
    repo.default_branch = data.get("default_branch")
    repo.pushed_at = parse_dt(data.get("pushed_at"))


def register_repo(actor: Actor, url: str) -> GitHubRepository:
    db = actor.db
    full_name = parse_repo_url(url)
    if not full_name:
        raise ValidationFailed(details={"fields": {"url": "Enter a GitHub repository URL like https://github.com/owner/repo."}})
    existing = db.scalar(select(GitHubRepository).where(func.lower(GitHubRepository.full_name) == full_name.lower()))
    if existing:
        return existing
    try:
        with GitHubClient() as gh:
            resp = gh.get_repo(full_name)
    except GitHubNotFound:
        raise NotFound("That repository does not exist or is private.", code="github_repo_not_found") from None
    except GitHubRateLimited:
        raise AppError("GitHub is rate limiting requests right now. Try again in a few minutes.", code="github_rate_limited",
                       status_code=503) from None
    except GitHubError:
        raise AppError("GitHub could not be reached. Try again later.", code="github_unavailable", status_code=503) from None
    data = resp.data
    if data.get("private"):
        raise ValidationFailed("Only public repositories can be registered.", code="github_repo_private")
    repo = db.scalar(select(GitHubRepository).where(GitHubRepository.github_repo_id == int(data["id"])))
    if repo is None:
        repo = GitHubRepository(github_repo_id=int(data["id"]), registered_by=actor.id, source="github")
        db.add(repo)
    _apply_repo_payload(repo, data)
    repo.etag = resp.etag
    repo.sync_status = "pending"
    db.flush()
    enqueue(db, SYNC_JOB, {"repo_id": str(repo.id)}, idempotency_key=f"ghsync:{repo.id}:{utcnow():%Y%m%d%H%M}")
    record_audit(db, actor.id, "github.repo_register", target_type="github_repo", target_id=repo.id, meta={"full_name": repo.full_name})
    for p in db.scalars(select(Project).where(Project.repo_url.is_not(None), Project.github_repo_id.is_(None))):
        if (parse_repo_url(p.repo_url) or "").lower() == repo.full_name.lower():
            from app.modules.projects.service import _maybe_verify_maintainer

            _maybe_verify_maintainer(db, p)
    db.commit()
    return repo


def request_sync(actor: Actor, repo_id: uuid.UUID) -> GitHubRepository:
    db = actor.db
    repo = db.get(GitHubRepository, repo_id)
    if repo is None:
        raise NotFound()
    allowed = actor.is_moderator or repo.registered_by == actor.id
    if not allowed:
        for p in db.scalars(select(Project).where(Project.github_repo_id == repo.id)):
            if can_edit_project(actor, p):
                allowed = True
                break
    if not allowed:
        raise Forbidden()
    if repo.source == "seed":
        raise Conflict("Demo repositories are not synced with GitHub.", code="demo_repo")
    if repo.last_synced_at and utcnow() - ensure_aware(repo.last_synced_at) < timedelta(minutes=10):  # type: ignore[operator]
        raise Conflict("This repository was synced in the last 10 minutes.", code="sync_too_soon")
    enqueue(db, SYNC_JOB, {"repo_id": str(repo.id)}, idempotency_key=f"ghsync:{repo.id}:{utcnow():%Y%m%d%H%M}")
    db.commit()
    return repo


def _upsert_issue(db: Session, repo: GitHubRepository, data: dict[str, Any]) -> None:
    if "pull_request" in data:
        return  # the issues API also returns PRs
    labels = [str(lbl.get("name", ""))[:60] for lbl in (data.get("labels") or []) if isinstance(lbl, dict)][:20]
    lowered = {lbl.lower() for lbl in labels}
    updated = parse_dt(data.get("updated_at"))
    existing = db.scalar(select(GitHubIssue).where(GitHubIssue.github_issue_id == int(data["id"])))
    if existing and existing.gh_updated_at and updated and ensure_aware(existing.gh_updated_at) > updated:  # type: ignore[operator]
        return  # out-of-order delivery: stored state is newer
    values = dict(repo_id=repo.id, number=int(data["number"]), title=str(data.get("title", ""))[:300], state=data.get("state", "open"),
                  labels=labels, is_beginner_friendly=bool(lowered & (BEGINNER_LABELS | HELP_LABELS)),
                  html_url=str(data.get("html_url", ""))[:500], author_login=((data.get("user") or {}).get("login") or "")[:64] or None,
                  comments=int(data.get("comments") or 0), gh_created_at=parse_dt(data.get("created_at")), gh_updated_at=updated,
                  gh_closed_at=parse_dt(data.get("closed_at")))
    if existing:
        for k, v in values.items():
            setattr(existing, k, v)
    else:
        db.add(GitHubIssue(github_issue_id=int(data["id"]), **values))


def _upsert_pr(db: Session, repo: GitHubRepository, data: dict[str, Any]) -> bool:
    """Returns True when this call observed the PR becoming merged."""
    updated = parse_dt(data.get("updated_at"))
    existing = db.scalar(select(GitHubPullRequest).where(GitHubPullRequest.github_pr_id == int(data["id"])))
    if existing and existing.gh_updated_at and updated and ensure_aware(existing.gh_updated_at) > updated:  # type: ignore[operator]
        return False
    merged_at = parse_dt(data.get("merged_at"))
    user = data.get("user") or {}
    values = dict(repo_id=repo.id, number=int(data["number"]), title=str(data.get("title", ""))[:300], state=data.get("state", "open"),
                  merged=merged_at is not None, merged_at=merged_at, author_github_id=user.get("id"),
                  author_login=(user.get("login") or "")[:64] or None, html_url=str(data.get("html_url", ""))[:500],
                  additions=data.get("additions"), deletions=data.get("deletions"),
                  gh_created_at=parse_dt(data.get("created_at")), gh_updated_at=updated)
    newly_merged = bool(merged_at) and not (existing and existing.merged)
    if existing:
        for k, v in values.items():
            if v is not None or k in ("merged_at",):
                setattr(existing, k, v)
    else:
        db.add(GitHubPullRequest(github_pr_id=int(data["id"]), **values))
    if newly_merged and user.get("id"):
        acct = db.scalar(select(GitHubAccount).where(GitHubAccount.github_user_id == int(user["id"])))
        if acct:
            notify(db, acct.user_id, NotificationKind.contribution_merged, f"Merged: {values['title'][:120]}",
                   body=f"{repo.full_name} #{values['number']}", link=values["html_url"],
                   dedupe_key=f"prmerged:{data['id']}")
            from app.modules.credentials.badges import evaluate_user_badges

            db.flush()
            evaluate_user_badges(db, acct.user_id, trigger="pr_merged")
    return newly_merged


def sync_repository(db: Session, repo: GitHubRepository) -> dict[str, int]:
    stats = {"issues": 0, "pulls": 0, "merged_new": 0}
    with GitHubClient() as gh:
        resp = gh.get_repo_by_id(repo.github_repo_id)  # immutable id survives renames/transfers
        if resp.status == 200:
            if resp.data.get("private"):
                repo.is_private = True
                repo.sync_status = "gone"
                repo.sync_error = "Repository became private; hidden from the hub."
                return stats
            _apply_repo_payload(repo, resp.data)
            repo.etag = resp.etag
        since = repo.issues_synced_until
        params: dict[str, Any] = {"state": "all", "per_page": 100, "sort": "updated", "direction": "asc"}
        if since:
            params["since"] = ensure_aware(since).isoformat()  # type: ignore[union-attr]
        started = utcnow()
        for issue in gh.paginate(f"/repos/{repo.full_name}/issues", params, max_pages=5):
            _upsert_issue(db, repo, issue)
            stats["issues"] += 1
        for pr in gh.paginate(f"/repos/{repo.full_name}/pulls", {"state": "closed", "per_page": 100, "sort": "updated",
                                                                   "direction": "desc"}, max_pages=3):
            if since and (parse_dt(pr.get("updated_at")) or started) < ensure_aware(since):  # type: ignore[operator]
                break
            stats["merged_new"] += int(_upsert_pr(db, repo, pr))
            stats["pulls"] += 1
        repo.issues_synced_until = started
        contributors = gh.get(f"/repos/{repo.full_name}/contributors", params={"per_page": 1, "anon": "false"})
        if contributors.status == 200 and contributors.next_url and "page=" in contributors.next_url:
            m = re.search(r"[?&]page=(\d+)", contributors.next_url.split(",")[-1])
            repo.contributors_count = int(m.group(1)) if m else None
        elif contributors.status == 200:
            repo.contributors_count = len(contributors.data or [])
    repo.sync_status = "ok"
    repo.sync_error = None
    repo.last_synced_at = utcnow()
    return stats


@job_handler(SYNC_JOB)
def _sync_job(db: Session, payload: dict[str, Any]) -> None:
    repo = db.get(GitHubRepository, uuid.UUID(payload["repo_id"]))
    if repo is None or repo.source == "seed":
        return
    try:
        sync_repository(db, repo)
        db.commit()
    except GitHubRateLimited as exc:
        db.rollback()
        repo = db.get(GitHubRepository, uuid.UUID(payload["repo_id"]))
        if repo:
            repo.sync_status = "rate_limited"
            repo.sync_error = "GitHub rate limit reached; sync rescheduled."
        reset = exc.reset_at or utcnow() + timedelta(minutes=15)
        enqueue(db, SYNC_JOB, payload, idempotency_key=f"ghsync:{payload['repo_id']}:rl:{reset:%Y%m%d%H%M}", run_after=reset)
        db.commit()
    except GitHubNotFound:
        db.rollback()
        repo = db.get(GitHubRepository, uuid.UUID(payload["repo_id"]))
        if repo:
            repo.sync_status = "gone"
            repo.sync_error = "Repository no longer exists or is no longer public."
            db.commit()
        raise PermanentJobError("repository gone") from None
    except GitHubError as exc:
        db.rollback()
        repo = db.get(GitHubRepository, uuid.UUID(payload["repo_id"]))
        if repo:
            repo.sync_status = "failed"
            repo.sync_error = str(exc)[:500]
            db.commit()
        raise TransientJobError(str(exc)) from None


def schedule_stale_syncs(db: Session, older_than: timedelta = timedelta(hours=6), limit: int = 20) -> int:
    cutoff = utcnow() - older_than
    repos = db.scalars(select(GitHubRepository).where(
        GitHubRepository.source == "github", GitHubRepository.sync_status.in_(["ok", "pending", "failed"]),
        or_(GitHubRepository.last_synced_at.is_(None), GitHubRepository.last_synced_at < cutoff)).limit(limit)).all()
    for r in repos:
        enqueue(db, SYNC_JOB, {"repo_id": str(r.id)}, idempotency_key=f"ghsync:{r.id}:{utcnow():%Y%m%d%H}")
    db.commit()
    return len(repos)


# ----------------------------------------------------------------------------- webhooks


def verify_signature(body: bytes, signature_header: str | None) -> bool:
    secret = settings.GITHUB_WEBHOOK_SECRET
    if not secret or not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def handle_webhook(db: Session, *, delivery_id: str, event: str, payload: dict[str, Any]) -> str:
    """Returns the delivery status. Duplicate deliveries are acknowledged without re-processing."""
    action = str(payload.get("action") or "")[:40] or None
    result = db.execute(insert(WebhookDelivery).values(id=uuid.uuid4(), provider="github", delivery_id=delivery_id[:80],
                                                       event=event[:40], action=action, status="received")
                        .on_conflict_do_nothing(index_elements=["delivery_id"]).returning(WebhookDelivery.id))
    if result.first() is None:
        db.commit()
        return "duplicate"
    delivery = db.scalar(select(WebhookDelivery).where(WebhookDelivery.delivery_id == delivery_id[:80]))
    assert delivery is not None
    status = "ignored"
    try:
        repo_data = payload.get("repository") or {}
        repo = db.scalar(select(GitHubRepository).where(GitHubRepository.github_repo_id == int(repo_data.get("id") or 0)))
        if event == "ping":
            status = "processed"
        elif repo is None:
            status = "ignored"  # not a registered repository
        elif event == "issues" and payload.get("issue"):
            _upsert_issue(db, repo, payload["issue"])
            status = "processed"
        elif event == "pull_request" and payload.get("pull_request"):
            _upsert_pr(db, repo, payload["pull_request"])
            status = "processed"
        elif event == "repository":
            if action in ("renamed", "transferred", "edited", "archived", "unarchived", "publicized"):
                _apply_repo_payload(repo, repo_data)
                if action == "publicized":
                    repo.sync_status = "pending"
            elif action in ("deleted", "privatized"):
                repo.sync_status = "gone"
                repo.is_private = action == "privatized"
                repo.sync_error = f"Repository {action} on GitHub."
            status = "processed"
        delivery.status = status
        delivery.processed_at = utcnow()
        db.commit()
    except Exception as exc:  # noqa: BLE001 — record failure, let GitHub redeliver on demand
        db.rollback()
        delivery = db.scalar(select(WebhookDelivery).where(WebhookDelivery.delivery_id == delivery_id[:80]))
        if delivery:
            delivery.status = "failed"
            delivery.error = type(exc).__name__[:500]
            db.commit()
        status = "failed"
    return status


# ----------------------------------------------------------------------------- hub views


def repo_card(r: GitHubRepository, projects: dict[uuid.UUID, Project] | None = None) -> dict[str, Any]:
    p = (projects or {}).get(r.id)
    return {
        "id": r.id, "full_name": r.full_name, "name": r.name, "owner_login": r.owner_login, "description": r.description,
        "language": r.language, "stars": r.stars, "forks": r.forks, "open_issues": r.open_issues, "topics": list(r.topics or []),
        "html_url": r.html_url, "license": r.license, "is_archived": r.is_archived, "sync_status": r.sync_status,
        "last_synced_at": r.last_synced_at, "contributors_count": r.contributors_count, "is_demo": r.source == "seed",
        "project": {"slug": p.slug, "title": p.title} if p else None,
    }


def list_repos(actor: Actor, params: PageParams, *, q: str | None, language: str | None, sort: str) -> tuple[list[dict[str, Any]], int]:
    db = actor.db
    stmt = select(GitHubRepository).where(GitHubRepository.is_private.is_(False), GitHubRepository.sync_status != "gone")
    if q:
        like = f"%{q.strip()[:80]}%"
        stmt = stmt.where(or_(GitHubRepository.full_name.ilike(like), GitHubRepository.description.ilike(like)))
    if language:
        stmt = stmt.where(func.lower(GitHubRepository.language) == language.lower())
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    order = {"stars": GitHubRepository.stars.desc().nulls_last(), "updated": GitHubRepository.pushed_at.desc().nulls_last(),
             "name": func.lower(GitHubRepository.full_name)}[sort]
    rows = db.scalars(stmt.order_by(order).limit(params.page_size).offset(params.offset)).all()
    projects = {p.github_repo_id: p for p in db.scalars(select(Project).where(
        Project.github_repo_id.in_([r.id for r in rows]), Project.visibility == "public", Project.taken_down.is_(False)))}
    return [repo_card(r, projects) for r in rows], total  # type: ignore[arg-type]


def list_issues(actor: Actor, params: PageParams, *, q: str | None, beginner: bool, language: str | None, promoted: bool | None,
                repo_id: uuid.UUID | None) -> tuple[list[dict[str, Any]], int]:
    db = actor.db
    stmt = (select(GitHubIssue, GitHubRepository).join(GitHubRepository, GitHubRepository.id == GitHubIssue.repo_id)
            .where(GitHubIssue.state == "open", GitHubRepository.is_private.is_(False), GitHubRepository.sync_status != "gone",
                   GitHubRepository.is_archived.is_(False)))
    if beginner:
        stmt = stmt.where(GitHubIssue.is_beginner_friendly.is_(True))
    if promoted is not None:
        stmt = stmt.where(GitHubIssue.is_promoted.is_(promoted))
    if language:
        stmt = stmt.where(func.lower(GitHubRepository.language) == language.lower())
    if repo_id:
        stmt = stmt.where(GitHubRepository.id == repo_id)
    if q:
        stmt = stmt.where(GitHubIssue.title.ilike(f"%{q.strip()[:80]}%"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(stmt.order_by(GitHubIssue.is_promoted.desc(), GitHubIssue.gh_updated_at.desc().nulls_last())
                      .limit(params.page_size).offset(params.offset)).all()
    return [{"id": i.id, "number": i.number, "title": i.title, "labels": list(i.labels or []), "html_url": i.html_url,
             "is_beginner_friendly": i.is_beginner_friendly, "is_promoted": i.is_promoted, "comments": i.comments,
             "created_at": i.gh_created_at, "updated_at": i.gh_updated_at,
             "repo": {"id": r.id, "full_name": r.full_name, "language": r.language, "is_demo": r.source == "seed"}} for i, r in rows], total


def promote_issue(actor: Actor, issue_id: uuid.UUID, promoted: bool) -> None:
    db = actor.db
    issue = db.get(GitHubIssue, issue_id)
    if issue is None:
        raise NotFound()
    allowed = actor.is_moderator
    if not allowed:
        for p in db.scalars(select(Project).where(Project.github_repo_id == issue.repo_id)):
            if can_edit_project(actor, p):
                allowed = True
                break
    if not allowed:
        raise Forbidden("Only maintainers of the linked project can promote issues.")
    issue.is_promoted = promoted
    issue.promoted_by = actor.id if promoted else None
    record_audit(db, actor.id, "github.issue_promote" if promoted else "github.issue_unpromote", target_type="github_issue",
                 target_id=issue.id)
    db.commit()


def hub_overview(actor: Actor) -> dict[str, Any]:
    db = actor.db
    repo_count = db.scalar(select(func.count()).select_from(GitHubRepository).where(GitHubRepository.is_private.is_(False),
                                                                                     GitHubRepository.sync_status != "gone")) or 0
    open_issues = db.scalar(select(func.count()).select_from(GitHubIssue).where(GitHubIssue.state == "open")) or 0
    beginner = db.scalar(select(func.count()).select_from(GitHubIssue).where(GitHubIssue.state == "open",
                                                                              GitHubIssue.is_beginner_friendly.is_(True))) or 0
    merged_90 = db.scalar(select(func.count()).select_from(GitHubPullRequest).where(
        GitHubPullRequest.merged.is_(True), GitHubPullRequest.merged_at > utcnow() - timedelta(days=90))) or 0
    top = db.execute(select(User, func.count(GitHubPullRequest.id))
                     .join(GitHubAccount, GitHubAccount.user_id == User.id)
                     .join(GitHubPullRequest, GitHubPullRequest.author_github_id == GitHubAccount.github_user_id)
                     .where(GitHubPullRequest.merged.is_(True), User.status == "active",
                            GitHubPullRequest.merged_at > utcnow() - timedelta(days=365))
                     .group_by(User.id).order_by(func.count(GitHubPullRequest.id).desc()).limit(20)).all()
    languages = db.execute(select(GitHubRepository.language, func.count()).where(GitHubRepository.language.is_not(None),
                                                                                 GitHubRepository.is_private.is_(False))
                           .group_by(GitHubRepository.language).order_by(func.count().desc()).limit(12)).all()
    return {
        "stats": {"repositories": repo_count, "open_issues": open_issues, "beginner_issues": beginner, "merged_prs_90d": merged_90},
        "top_contributors": [{"user": user_mini(u), "merged_prs": n} for u, n in top if u.privacy_flag("show_contributions")][:10],
        "languages": [{"language": lang, "repositories": n} for lang, n in languages],
        "github": {"oauth_enabled": settings.github_oauth_enabled, "webhooks_enabled": bool(settings.GITHUB_WEBHOOK_SECRET)},
        "attribution_note": "Contributions count only for merged pull requests authored by a GitHub account the member linked "
                            "via GitHub sign-in, to repositories registered here.",
    }
