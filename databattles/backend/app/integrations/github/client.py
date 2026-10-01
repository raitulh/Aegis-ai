"""Minimal GitHub REST client for public repository metadata.

* Uses conditional requests (ETag / If-None-Match) so unchanged resources don't consume rate limit.
* Surfaces rate limiting explicitly (``GitHubRateLimited`` with the reset time) so jobs back off
  instead of hammering the API.
* Never logs tokens. Tokens are optional: a configured server token raises the anonymous limit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.config import settings


class GitHubError(Exception):
    pass


class GitHubNotFound(GitHubError):
    pass


class GitHubRateLimited(GitHubError):
    def __init__(self, reset_at: datetime | None) -> None:
        super().__init__("GitHub rate limit reached")
        self.reset_at = reset_at


@dataclass
class GitHubResponse:
    status: int
    data: Any
    etag: str | None
    next_url: str | None


def _parse_next(link: str | None) -> str | None:
    if not link:
        return None
    for part in link.split(","):
        seg = part.split(";")
        if len(seg) >= 2 and 'rel="next"' in seg[1]:
            return seg[0].strip().strip("<>")
    return None


class GitHubClient:
    def __init__(self, token: str | None = None, *, transport: httpx.BaseTransport | None = None) -> None:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": f"{settings.APP_NAME}-oss-hub"}
        token = token or settings.GITHUB_API_TOKEN
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.Client(base_url=settings.GITHUB_API_BASE, headers=headers, timeout=15, transport=transport)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get(self, path: str, *, params: dict[str, Any] | None = None, etag: str | None = None) -> GitHubResponse:
        headers = {"If-None-Match": etag} if etag else {}
        try:
            resp = self._client.get(path, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise GitHubError(f"network error: {type(exc).__name__}") from None
        if resp.status_code == 304:
            return GitHubResponse(304, None, etag, None)
        if resp.status_code in (403, 429) and (resp.headers.get("x-ratelimit-remaining") == "0" or resp.status_code == 429
                                               or "rate limit" in resp.text.lower()):
            reset = resp.headers.get("x-ratelimit-reset")
            retry_after = resp.headers.get("retry-after")
            reset_at = None
            if reset and reset.isdigit():
                reset_at = datetime.fromtimestamp(int(reset), tz=UTC)
            elif retry_after and retry_after.isdigit():
                reset_at = datetime.fromtimestamp(datetime.now(UTC).timestamp() + int(retry_after), tz=UTC)
            raise GitHubRateLimited(reset_at)
        if resp.status_code in (404, 410, 451):
            raise GitHubNotFound(path)
        if resp.status_code >= 400:
            raise GitHubError(f"GitHub API returned {resp.status_code}")
        return GitHubResponse(resp.status_code, resp.json(), resp.headers.get("etag"), _parse_next(resp.headers.get("link")))

    def get_repo(self, full_name: str, etag: str | None = None) -> GitHubResponse:
        return self.get(f"/repos/{full_name}", etag=etag)

    def get_repo_by_id(self, repo_id: int) -> GitHubResponse:
        return self.get(f"/repositories/{repo_id}")

    def paginate(self, path: str, params: dict[str, Any], max_pages: int = 5) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        resp = self.get(path, params=params)
        pages = 1
        while True:
            out.extend(resp.data or [])
            if not resp.next_url or pages >= max_pages:
                break
            # next_url is absolute on api.github.com; only follow links on the configured API host.
            if not resp.next_url.startswith(settings.GITHUB_API_BASE):
                break
            resp = self.get(resp.next_url[len(settings.GITHUB_API_BASE):])
            pages += 1
        return out


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
