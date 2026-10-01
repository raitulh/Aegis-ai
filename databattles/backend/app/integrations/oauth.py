"""OAuth provider adapters (Google sign-in, GitHub sign-in / account linking).

Providers are optional: they are enabled only when client credentials are configured.
Access tokens obtained here never reach the browser.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlencode

import httpx

from app.core.config import settings
from app.core.errors import AppError


@dataclass
class OAuthProfile:
    provider: str
    provider_user_id: str
    email: str | None
    email_verified: bool
    name: str | None
    avatar_url: str | None
    login: str | None = None
    access_token: str | None = None
    scopes: str | None = None


class OAuthProvider(Protocol):
    name: str

    def authorize_url(self, state: str, redirect_uri: str, purpose: str) -> str: ...
    def exchange(self, code: str, redirect_uri: str) -> OAuthProfile: ...


class OAuthError(AppError):
    status_code = 400
    code = "oauth_failed"
    message = "Sign-in with the external provider failed. Please try again."


class GoogleOAuth:
    name = "google"

    def authorize_url(self, state: str, redirect_uri: str, purpose: str) -> str:
        params = {"client_id": settings.GOOGLE_CLIENT_ID, "redirect_uri": redirect_uri, "response_type": "code",
                  "scope": "openid email profile", "state": state, "prompt": "select_account"}
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)

    def exchange(self, code: str, redirect_uri: str) -> OAuthProfile:
        try:
            with httpx.Client(timeout=10) as client:
                tok = client.post("https://oauth2.googleapis.com/token", data={
                    "code": code, "client_id": settings.GOOGLE_CLIENT_ID, "client_secret": settings.GOOGLE_CLIENT_SECRET,
                    "redirect_uri": redirect_uri, "grant_type": "authorization_code"})
                tok.raise_for_status()
                access = tok.json()["access_token"]
                info = client.get("https://openidconnect.googleapis.com/v1/userinfo",
                                  headers={"Authorization": f"Bearer {access}"})
                info.raise_for_status()
                data = info.json()
        except (httpx.HTTPError, KeyError, ValueError):
            raise OAuthError() from None
        return OAuthProfile(provider="google", provider_user_id=str(data["sub"]), email=data.get("email"),
                            email_verified=bool(data.get("email_verified")), name=data.get("name"),
                            avatar_url=data.get("picture"))


class GitHubOAuth:
    name = "github"

    def authorize_url(self, state: str, redirect_uri: str, purpose: str) -> str:
        # Least privilege: public profile always; primary email only when signing in.
        scope = "read:user user:email" if purpose == "login" else "read:user"
        params = {"client_id": settings.GITHUB_CLIENT_ID, "redirect_uri": redirect_uri, "scope": scope,
                  "state": state, "allow_signup": "true"}
        return "https://github.com/login/oauth/authorize?" + urlencode(params)

    def exchange(self, code: str, redirect_uri: str) -> OAuthProfile:
        try:
            with httpx.Client(timeout=10, headers={"Accept": "application/json"}) as client:
                tok = client.post("https://github.com/login/oauth/access_token", data={
                    "code": code, "client_id": settings.GITHUB_CLIENT_ID, "client_secret": settings.GITHUB_CLIENT_SECRET,
                    "redirect_uri": redirect_uri})
                tok.raise_for_status()
                body = tok.json()
                access = body["access_token"]
                gh = {"Authorization": f"Bearer {access}", "Accept": "application/vnd.github+json"}
                user = client.get(f"{settings.GITHUB_API_BASE}/user", headers=gh)
                user.raise_for_status()
                data = user.json()
                email, verified = data.get("email"), False
                emails = client.get(f"{settings.GITHUB_API_BASE}/user/emails", headers=gh)
                if emails.status_code == 200:
                    primary = next((e for e in emails.json() if e.get("primary")), None)
                    if primary:
                        email, verified = primary["email"], bool(primary.get("verified"))
        except (httpx.HTTPError, KeyError, ValueError):
            raise OAuthError() from None
        return OAuthProfile(provider="github", provider_user_id=str(data["id"]), email=email, email_verified=verified,
                            name=data.get("name") or data.get("login"), avatar_url=data.get("avatar_url"),
                            login=data.get("login"), access_token=access, scopes=body.get("scope"))


def get_provider(name: str) -> OAuthProvider | None:
    if name == "google" and settings.google_enabled:
        return GoogleOAuth()
    if name == "github" and settings.github_oauth_enabled:
        return GitHubOAuth()
    return None


def enabled_providers() -> list[str]:
    return [n for n in ("google", "github") if get_provider(n) is not None]
