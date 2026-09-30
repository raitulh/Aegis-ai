"""Short-lived HS256 access tokens for the lab API.

Access tokens are paired with opaque, rotating refresh tokens (``aegis_api.lab.identity.tokens``).

Issuance: header ``{"alg": "HS256", "kid": JWT_ACTIVE_KID}``; claims ``iss``, ``aud``, ``sub`` (user id),
``org`` (organization id), ``role``, ``iat``, ``nbf``, ``exp``, ``jti``, ``typ="access"`` and, when the token
was minted from a refresh-token family, ``sid`` (the family id — lets the API reject access tokens of a
revoked sign-in immediately instead of waiting for expiry).

Verification is deliberately strict:

* only ``HS256`` is accepted (``none``, ``HS384/512`` and every asymmetric algorithm are rejected before
  any key material is touched — this rules out algorithm-confusion attacks);
* the ``kid`` must name a key in ``settings.effective_jwt_keys`` (supports zero-downtime key rotation via
  ``JWT_SECRETS_JSON``); an unknown ``kid`` is rejected;
* ``exp``, ``iat``, ``sub``, ``iss``, ``aud`` and ``jti`` are required; issuer/audience must match settings;
  a 30 second leeway absorbs clock skew.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from aegis_api.config import get_settings
from aegis_api.errors import ServiceUnavailable, Unauthorized

ALGORITHM = "HS256"
LEEWAY_SECONDS = 30
TOKEN_TYPE = "access"  # noqa: S105 - token type label, not a secret
REQUIRED_CLAIMS = ("exp", "iat", "sub", "iss", "aud", "jti")
# Claims the issuer controls; callers cannot override them through ``extra_claims``.
RESERVED_CLAIMS = frozenset({"iss", "aud", "sub", "org", "role", "iat", "nbf", "exp", "jti", "typ", "sid"})
MAX_TOKEN_LENGTH = 8192


@dataclass(frozen=True)
class IssuedAccessToken:
    token: str
    jti: str
    expires_in: int
    expires_at: datetime
    kid: str


@dataclass(frozen=True)
class AccessTokenClaims:
    user_id: uuid.UUID
    organization_id: uuid.UUID
    role: str | None
    jti: str
    issued_at: datetime
    expires_at: datetime
    session_id: uuid.UUID | None
    kid: str
    raw: dict[str, Any]


def issue_access_token(
    *,
    user_id: uuid.UUID,
    organization_id: uuid.UUID,
    role: str,
    session_id: uuid.UUID | None = None,
    ttl_seconds: int | None = None,
    extra_claims: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> IssuedAccessToken:
    """Sign an access token with the active key (``JWT_ACTIVE_KID``)."""
    settings = get_settings()
    kid = settings.jwt_active_kid
    secret = settings.effective_jwt_keys.get(kid)
    if not secret:
        raise ServiceUnavailable(
            "The JWT signing key for the active key id is not configured", code="jwt_not_configured"
        )
    ttl = int(ttl_seconds if ttl_seconds is not None else settings.jwt_access_ttl_seconds)
    if ttl <= 0:
        raise ValueError("ttl_seconds must be positive")
    issued = (now or datetime.now(UTC)).replace(microsecond=0)
    expires = issued + timedelta(seconds=ttl)
    jti = uuid.uuid4().hex
    claims: dict[str, Any] = {key: value for key, value in (extra_claims or {}).items() if key not in RESERVED_CLAIMS}
    claims.update(
        {
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "sub": str(user_id),
            "org": str(organization_id),
            "role": role,
            "iat": int(issued.timestamp()),
            "nbf": int(issued.timestamp()),
            "exp": int(expires.timestamp()),
            "jti": jti,
            "typ": TOKEN_TYPE,
        }
    )
    if session_id is not None:
        claims["sid"] = str(session_id)
    token = jwt.encode(claims, secret, algorithm=ALGORITHM, headers={"kid": kid, "typ": "JWT"})
    return IssuedAccessToken(token=token, jti=jti, expires_in=ttl, expires_at=expires, kid=kid)


def _invalid(message: str = "Invalid access token") -> Unauthorized:
    return Unauthorized(message, code="invalid_token")


def decode_access_token(token: str) -> AccessTokenClaims:
    """Verify an access token and return its claims. Raises ``Unauthorized`` on any failure."""
    settings = get_settings()
    if not token or len(token) > MAX_TOKEN_LENGTH:
        raise _invalid()
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise _invalid("Malformed access token") from exc
    if header.get("alg") != ALGORITHM:
        raise _invalid("Unsupported token algorithm")
    kid = header.get("kid")
    keys = settings.effective_jwt_keys
    if not isinstance(kid, str) or kid not in keys:
        raise _invalid("Unknown token signing key")
    try:
        claims = jwt.decode(
            token,
            keys[kid],
            algorithms=[ALGORITHM],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            leeway=LEEWAY_SECONDS,
            options={"require": list(REQUIRED_CLAIMS)},
        )
    except jwt.ExpiredSignatureError as exc:
        raise Unauthorized("Access token expired", code="token_expired") from exc
    except jwt.PyJWTError as exc:
        raise _invalid() from exc
    if claims.get("typ") != TOKEN_TYPE:
        raise _invalid("Not an access token")
    try:
        user_id = uuid.UUID(str(claims["sub"]))
        organization_id = uuid.UUID(str(claims["org"]))
        session_id = uuid.UUID(str(claims["sid"])) if claims.get("sid") else None
        issued_at = datetime.fromtimestamp(int(claims["iat"]), UTC)
        expires_at = datetime.fromtimestamp(int(claims["exp"]), UTC)
    except (KeyError, ValueError, TypeError) as exc:
        raise _invalid() from exc
    role = claims.get("role")
    return AccessTokenClaims(
        user_id=user_id,
        organization_id=organization_id,
        role=str(role) if role is not None else None,
        jti=str(claims["jti"]),
        issued_at=issued_at,
        expires_at=expires_at,
        session_id=session_id,
        kid=kid,
        raw=claims,
    )


def unverified_issuer(token: str) -> str | None:
    """The ``iss`` claim of a compact JWS WITHOUT verifying it (routing decision only, never trust)."""
    if not token or len(token) > MAX_TOKEN_LENGTH or token.count(".") != 2:
        return None
    payload_segment = token.split(".")[1]
    try:
        raw = base64.urlsafe_b64decode(payload_segment + "=" * (-len(payload_segment) % 4))
        payload = json.loads(raw)
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    issuer = payload.get("iss")
    return issuer if isinstance(issuer, str) else None


def looks_like_access_token(token: str) -> bool:
    """True when ``token`` is a JWT that *claims* to be issued by this API (verify it before trusting it)."""
    return unverified_issuer(token) == get_settings().jwt_issuer
