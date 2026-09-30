"""Enterprise SSO integration point: OpenID Connect (authorization code + PKCE).

Flow: ``authorization_url`` stores a signed, short-lived state (nonce + PKCE verifier) → the IdP redirects back
with ``code`` + ``state`` → ``complete`` exchanges the code at the discovered token endpoint, verifies the ID
token signature against the IdP JWKS (issuer, audience, nonce, expiry), requires a verified email in one of
the connection's allowed domains, then provisions/links the user and membership with the connection's
default role. SAML connections are modelled (``protocol='saml'``) but need an adapter (xmlsec) to be enabled.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWKClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.db.base import utcnow
from aegis_api.errors import FeatureDisabled, Forbidden, NotFound, Unauthorized, ValidationFailed
from aegis_api.models import Membership, SSOConnection, User
from aegis_api.models.enums import MembershipStatus
from aegis_api.security.crypto import decrypt_json, encrypt_json
from aegis_api.security.ssrf import validate_outbound_url
from aegis_api.services import secrets_service

STATE_TTL_SECONDS = 600


@dataclass(frozen=True)
class OIDCMetadata:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str


def _discover(issuer: str) -> OIDCMetadata:
    url = validate_outbound_url(
        issuer.rstrip("/") + "/.well-known/openid-configuration", allowed_schemes=frozenset({"https"})
    )
    response = httpx.get(url, timeout=10, follow_redirects=False)
    if response.status_code != 200:
        raise Unauthorized("SSO provider discovery failed")
    data = response.json()
    if data.get("issuer", "").rstrip("/") != issuer.rstrip("/"):
        raise Unauthorized("SSO provider issuer mismatch")
    for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        validate_outbound_url(data[key], allowed_schemes=frozenset({"https"}))
    return OIDCMetadata(
        issuer=data["issuer"],
        authorization_endpoint=data["authorization_endpoint"],
        token_endpoint=data["token_endpoint"],
        jwks_uri=data["jwks_uri"],
    )


def _redirect_uri() -> str:
    return get_settings().api_base_url.rstrip("/") + "/api/v1/auth/sso/callback"


def _connection(session: Session, connection_id: uuid.UUID) -> SSOConnection:
    if not get_settings().feature_enterprise_sso:
        raise FeatureDisabled("Enterprise SSO is disabled")
    conn = session.get(SSOConnection, connection_id)
    if conn is None or not conn.enabled:
        raise NotFound("SSO connection not found")
    if conn.protocol != "oidc":
        raise ValidationFailed("Only OIDC connections are supported by this deployment")
    return conn


def authorization_url(session: Session, connection_id: uuid.UUID) -> tuple[str, str]:
    """Return (redirect_url, sealed_state). The sealed state is stored in an HttpOnly cookie by the router."""
    conn = _connection(session, connection_id)
    meta = _discover(conn.issuer)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    sealed = encrypt_json({"c": str(conn.id), "s": state, "n": nonce, "v": verifier, "t": int(time.time())})
    params = {
        "response_type": "code",
        "client_id": conn.client_id,
        "redirect_uri": _redirect_uri(),
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{meta.authorization_endpoint}?{urlencode(params)}", sealed


def complete(session: Session, *, code: str, state: str, sealed_state: str | None) -> tuple[User, Membership]:
    if not sealed_state:
        raise Unauthorized("Missing SSO state")
    try:
        data: dict[str, Any] = decrypt_json(sealed_state)
    except (ValueError, json.JSONDecodeError) as exc:
        raise Unauthorized("Invalid SSO state") from exc
    if (
        not secrets.compare_digest(str(data.get("s", "")), state)
        or time.time() - int(data.get("t", 0)) > STATE_TTL_SECONDS
    ):
        raise Unauthorized("SSO state mismatch or expired")
    conn = _connection(session, uuid.UUID(str(data["c"])))
    meta = _discover(conn.issuer)
    secret = secrets_service.resolve_optional(session, conn.client_secret_id, conn.organization_id)
    response = httpx.post(
        meta.token_endpoint,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": _redirect_uri(),
            "client_id": conn.client_id,
            "code_verifier": str(data["v"]),
            **({"client_secret": secret} if secret else {}),
        },
        timeout=15,
        follow_redirects=False,
    )
    if response.status_code != 200 or "id_token" not in response.json():
        raise Unauthorized("SSO code exchange failed")
    id_token = response.json()["id_token"]
    try:
        key = PyJWKClient(meta.jwks_uri, cache_keys=True, timeout=10).get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            key.key,
            algorithms=["RS256", "ES256", "PS256", "EdDSA"],
            audience=conn.client_id,
            issuer=meta.issuer,
            options={"require": ["exp", "iat", "sub", "nonce"]},
        )
    except jwt.PyJWTError as exc:
        raise Unauthorized("Invalid ID token") from exc
    if not secrets.compare_digest(str(claims.get("nonce", "")), str(data["n"])):
        raise Unauthorized("ID token nonce mismatch")
    email = str(claims.get("email") or "").lower()
    if not email or not claims.get("email_verified", False):
        raise Forbidden("The identity provider did not assert a verified email")
    domain = email.rsplit("@", 1)[-1]
    if not conn.email_domains or domain not in {d.lower() for d in conn.email_domains}:
        raise Forbidden("Email domain is not allowed for this SSO connection")
    subject = f"oidc:{conn.id}:{claims['sub']}"
    user = session.scalar(select(User).where(User.auth_subject == subject))
    if user is None:
        if session.scalar(select(User.id).where(func.lower(User.email) == email)) is not None:
            # Never auto-link an existing account by email: an org-controlled IdP could otherwise assert any
            # address and take over a user who belongs to other organizations.
            raise Forbidden("An account with this email already exists; sign in and link SSO explicitly")
        user = User(
            email=email, full_name=claims.get("name"), auth_provider="sso", auth_subject=subject, email_verified=True
        )
        session.add(user)
        session.flush()
    user.last_active_at = utcnow()
    membership = session.scalar(
        select(Membership).where(Membership.user_id == user.id, Membership.organization_id == conn.organization_id)
    )
    if membership is None:
        membership = Membership(
            organization_id=conn.organization_id,
            user_id=user.id,
            role=conn.default_role,
            status=MembershipStatus.ACTIVE,
        )
        session.add(membership)
        session.flush()
    if membership.status == MembershipStatus.SUSPENDED:
        raise Forbidden("Your access to this workspace is suspended")
    if user.default_organization_id is None:
        user.default_organization_id = conn.organization_id
    return user, membership


def seal_config(value: dict[str, Any]) -> str:
    return encrypt_json(value)
