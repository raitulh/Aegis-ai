"""Verification of Supabase Auth access tokens (HS256 shared secret or asymmetric JWKS)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import jwt
from jwt import PyJWKClient

from aegis_api.config import get_settings
from aegis_api.errors import Unauthorized


@dataclass(frozen=True)
class SupabaseIdentity:
    subject: str
    email: str | None
    full_name: str | None
    claims: dict[str, Any]


@lru_cache(maxsize=2)
def _jwks_client(url: str) -> PyJWKClient:
    return PyJWKClient(url, cache_keys=True, lifespan=600, timeout=5)


def supabase_configured() -> bool:
    s = get_settings()
    return bool(s.effective_supabase_url and (s.supabase_jwt_secret or s.effective_supabase_url))


def verify_supabase_token(token: str) -> SupabaseIdentity:
    settings = get_settings()
    base = settings.effective_supabase_url
    if not base:
        raise Unauthorized("Supabase authentication is not configured")
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise Unauthorized("Malformed access token") from exc
    alg = header.get("alg", "")
    options: dict = {"require": ["exp", "sub"]}
    try:
        if alg == "HS256":
            if not settings.supabase_jwt_secret:
                raise Unauthorized("HS256 tokens require SUPABASE_JWT_SECRET")
            claims = jwt.decode(
                token,
                settings.supabase_jwt_secret,
                algorithms=["HS256"],
                audience=settings.supabase_jwt_audience,
                options=options,  # type: ignore[arg-type]
            )
        elif alg in {"ES256", "RS256", "EdDSA"}:
            signing_key = _jwks_client(f"{base}/auth/v1/.well-known/jwks.json").get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=[alg],
                audience=settings.supabase_jwt_audience,
                issuer=f"{base}/auth/v1",
                options=options,  # type: ignore[arg-type]
            )
        else:
            raise Unauthorized("Unsupported token algorithm")
    except jwt.ExpiredSignatureError as exc:
        raise Unauthorized("Session expired", code="session_expired") from exc
    except jwt.PyJWTError as exc:
        raise Unauthorized("Invalid access token") from exc
    metadata = claims.get("user_metadata") or {}
    return SupabaseIdentity(
        subject=str(claims["sub"]),
        email=claims.get("email"),
        full_name=metadata.get("full_name") or metadata.get("name"),
        claims=claims,
    )
