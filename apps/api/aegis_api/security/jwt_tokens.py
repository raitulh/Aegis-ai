"""Short-lived JWT access tokens (HS256) for API/SDK clients.

Access tokens carry identity (``sub``), the organization (``org``) and the refresh-token family (``sid``).
Authorization is *not* taken from token claims: permissions are re-derived from the live membership on every
request, and a revoked token family invalidates its access tokens immediately.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import jwt

from aegis_api.config import get_settings
from aegis_api.db.base import utcnow
from aegis_api.errors import Unauthorized

ALGORITHM = "HS256"
TOKEN_TYPE = "access"  # noqa: S105 - JWT "typ" claim value, not a secret


@dataclass(frozen=True)
class AccessClaims:
    user_id: uuid.UUID
    organization_id: uuid.UUID
    family_id: uuid.UUID
    jti: str
    expires_at: datetime


def issue_access_token(user_id: uuid.UUID, organization_id: uuid.UUID, family_id: uuid.UUID) -> tuple[str, datetime]:
    settings = get_settings()
    now = utcnow()
    expires = now + timedelta(minutes=settings.access_token_ttl_minutes)
    claims = {
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "sub": str(user_id),
        "org": str(organization_id),
        "sid": str(family_id),
        "jti": uuid.uuid4().hex,
        "typ": TOKEN_TYPE,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int(expires.timestamp()),
    }
    return jwt.encode(claims, settings.effective_jwt_key, algorithm=ALGORITHM), expires


def looks_like_platform_jwt(token: str) -> bool:
    """Cheap routing check (no trust implied): is this an unverified JWT issued by this platform?"""
    try:
        claims = jwt.decode(token, options={"verify_signature": False})
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        return False
    return header.get("alg") == ALGORITHM and claims.get("iss") == get_settings().jwt_issuer


def decode_access_token(token: str) -> AccessClaims:
    settings = get_settings()
    try:
        claims = jwt.decode(
            token,
            settings.effective_jwt_key,
            algorithms=[ALGORITHM],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "sub", "org", "sid", "jti", "typ"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise Unauthorized("Access token expired", code="token_expired") from exc
    except jwt.PyJWTError as exc:
        raise Unauthorized("Invalid access token", code="invalid_token") from exc
    if claims.get("typ") != TOKEN_TYPE:
        raise Unauthorized("Invalid token type", code="invalid_token")
    try:
        return AccessClaims(
            user_id=uuid.UUID(claims["sub"]),
            organization_id=uuid.UUID(claims["org"]),
            family_id=uuid.UUID(claims["sid"]),
            jti=str(claims["jti"]),
            expires_at=datetime.fromtimestamp(int(claims["exp"]), tz=utcnow().tzinfo),
        )
    except (ValueError, KeyError) as exc:
        raise Unauthorized("Malformed access token", code="invalid_token") from exc
