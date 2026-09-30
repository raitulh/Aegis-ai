"""OpenID Connect relying-party primitives (authorization code flow + PKCE S256) for enterprise SSO.

This module is protocol-only: no database access. The application flow (provider lookup, state storage,
account resolution, session creation) lives in ``aegis_api.lab.identity.sso``.

Security properties:

* every server-side request (discovery document, token endpoint, JWKS) targets a URL validated by
  ``validate_outbound_url`` (SSRF) and redirects are never followed;
* responses are size-capped and must be JSON objects;
* ID tokens are only accepted when signed with an asymmetric algorithm by a key from the provider's
  JWKS (``none`` and HMAC algorithms are rejected — no algorithm confusion with the client secret);
  ``iss`` must equal the discovery issuer, ``aud`` must contain the client id, ``exp``/``iat`` are
  required and the ``nonce`` must match the one bound to the login attempt;
* the PKCE verifier and the nonce are derived from the (random, single-use) ``state`` with a server-side
  HMAC key, so nothing secret has to be stored alongside the state and an attacker who observes the
  ``state`` in a URL still cannot compute the verifier.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

import httpx
import jwt

from aegis_api.config import get_settings
from aegis_api.errors import AppError, ServiceUnavailable, Unauthorized, ValidationFailed
from aegis_api.security.ssrf import validate_outbound_url

HTTP_TIMEOUT_SECONDS = 10.0
MAX_RESPONSE_BYTES = 512 * 1024
CACHE_TTL_SECONDS = 300.0
ID_TOKEN_LEEWAY_SECONDS = 60
ALLOWED_ID_TOKEN_ALGORITHMS = frozenset(
    {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "EdDSA"}
)


class OIDCProtocolError(AppError):
    """The identity provider returned an invalid or unexpected response."""

    status_code = 502
    code = "sso_provider_error"


@dataclass(frozen=True)
class DiscoveryDocument:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    token_endpoint_auth_methods: tuple[str, ...]
    raw: dict[str, Any]


# --- HTTP ------------------------------------------------------------------------------------------
def http_client() -> httpx.Client:
    """HTTP client used for every IdP call (tests replace this factory with a MockTransport client)."""
    return httpx.Client(
        timeout=HTTP_TIMEOUT_SECONDS,
        follow_redirects=False,
        headers={"Accept": "application/json", "User-Agent": "aegis-sso/1.0"},
    )


def validate_idp_url(url: str) -> str:
    """SSRF-validate a URL the server itself will call. HTTPS only in production."""
    schemes = frozenset({"https"}) if get_settings().is_production else frozenset({"https", "http"})
    return validate_outbound_url(url, allowed_schemes=schemes)


def validate_browser_url(url: str) -> str:
    """Validate a URL the *browser* is sent to (authorization endpoint): scheme and host only."""
    parts = urlsplit(url or "")
    allowed = {"https"} if get_settings().is_production else {"https", "http"}
    if parts.scheme.lower() not in allowed or not parts.hostname or parts.username or parts.password:
        raise OIDCProtocolError("The identity provider advertised an invalid authorization endpoint")
    return url


def _read_json(response: httpx.Response) -> dict[str, Any]:
    body = bytearray()
    for chunk in response.iter_bytes():
        body.extend(chunk)
        if len(body) > MAX_RESPONSE_BYTES:
            raise OIDCProtocolError("The identity provider response is too large")
    try:
        data = json.loads(bytes(body))
    except ValueError as exc:
        raise OIDCProtocolError("The identity provider returned malformed JSON") from exc
    if not isinstance(data, dict):
        raise OIDCProtocolError("The identity provider returned an unexpected document")
    return data


def _request_json(method: str, url: str, **kwargs: Any) -> tuple[int, dict[str, Any]]:
    target = validate_idp_url(url)
    try:
        with http_client() as client, client.stream(method, target, **kwargs) as response:
            if 300 <= response.status_code < 400:
                raise OIDCProtocolError("The identity provider responded with a redirect (not followed)")
            return response.status_code, _read_json(response)
    except httpx.HTTPError as exc:
        raise ServiceUnavailable("The identity provider is unreachable", code="sso_provider_unavailable") from exc


# --- caches ----------------------------------------------------------------------------------------
_cache_lock = threading.Lock()
_discovery_cache: dict[str, tuple[float, DiscoveryDocument]] = {}
_jwks_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def clear_caches() -> None:
    with _cache_lock:
        _discovery_cache.clear()
        _jwks_cache.clear()


def fetch_discovery(discovery_url: str, *, force: bool = False) -> DiscoveryDocument:
    """Fetch and validate an OpenID Provider configuration document (cached for 5 minutes)."""
    now = time.monotonic()
    if not force:
        with _cache_lock:
            cached = _discovery_cache.get(discovery_url)
        if cached and now - cached[0] < CACHE_TTL_SECONDS:
            return cached[1]
    status, data = _request_json("GET", discovery_url)
    if status != 200:
        raise OIDCProtocolError(f"The identity provider discovery document returned HTTP {status}")
    fields: dict[str, str] = {}
    for name in ("issuer", "authorization_endpoint", "token_endpoint", "jwks_uri"):
        value = data.get(name)
        if not isinstance(value, str) or not value:
            raise OIDCProtocolError(f"The discovery document is missing '{name}'")
        fields[name] = value
    validate_browser_url(fields["issuer"])
    validate_browser_url(fields["authorization_endpoint"])
    methods_raw = data.get("token_endpoint_auth_methods_supported")
    methods = (
        tuple(str(m) for m in methods_raw if isinstance(m, str))
        if isinstance(methods_raw, list)
        else ("client_secret_basic",)
    )
    doc = DiscoveryDocument(
        issuer=fields["issuer"],
        authorization_endpoint=fields["authorization_endpoint"],
        token_endpoint=fields["token_endpoint"],
        jwks_uri=fields["jwks_uri"],
        token_endpoint_auth_methods=methods,
        raw=data,
    )
    with _cache_lock:
        _discovery_cache[discovery_url] = (now, doc)
    return doc


def fetch_jwks(jwks_uri: str, *, force: bool = False) -> dict[str, Any]:
    now = time.monotonic()
    if not force:
        with _cache_lock:
            cached = _jwks_cache.get(jwks_uri)
        if cached and now - cached[0] < CACHE_TTL_SECONDS:
            return cached[1]
    status, data = _request_json("GET", jwks_uri)
    if status != 200 or not isinstance(data.get("keys"), list):
        raise OIDCProtocolError("The identity provider JWKS document is invalid")
    with _cache_lock:
        _jwks_cache[jwks_uri] = (now, data)
    return data


# --- state-bound secrets (PKCE + nonce) ---------------------------------------------------------------
def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _derive(state: str, purpose: str) -> str:
    key = hashlib.sha256(get_settings().effective_api_key_pepper + b":oidc-state").digest()
    return _b64url(hmac.new(key, f"{purpose}:{state}".encode(), hashlib.sha256).digest())


def pkce_verifier(state: str) -> str:
    """43-character PKCE code verifier bound to ``state`` (RFC 7636 §4.1)."""
    return _derive(state, "pkce")


def pkce_challenge(verifier: str) -> str:
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def nonce_for(state: str) -> str:
    return _derive(state, "nonce")


def build_authorization_url(
    discovery: DiscoveryDocument,
    *,
    client_id: str,
    redirect_uri: str,
    scopes: list[str],
    state: str,
) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(scopes),
        "state": state,
        "nonce": nonce_for(state),
        "code_challenge": pkce_challenge(pkce_verifier(state)),
        "code_challenge_method": "S256",
    }
    endpoint = discovery.authorization_endpoint
    separator = "&" if urlsplit(endpoint).query else "?"
    return f"{endpoint}{separator}{urlencode(params, quote_via=quote)}"


# --- code exchange ---------------------------------------------------------------------------------
def exchange_code(
    discovery: DiscoveryDocument,
    *,
    code: str,
    state: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str | None,
) -> dict[str, Any]:
    """Exchange the authorization code at the token endpoint (with the PKCE verifier)."""
    form: dict[str, str] = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": pkce_verifier(state),
    }
    headers: dict[str, str] = {"Content-Type": "application/x-www-form-urlencoded"}
    methods = discovery.token_endpoint_auth_methods
    if client_secret is None:
        form["client_id"] = client_id
    elif "client_secret_basic" in methods:
        # RFC 6749 §2.3.1: form-urlencode the id and secret before base64.
        raw = f"{quote(client_id, safe='')}:{quote(client_secret, safe='')}".encode()
        headers["Authorization"] = "Basic " + base64.b64encode(raw).decode()
    elif "client_secret_post" in methods:
        form["client_id"] = client_id
        form["client_secret"] = client_secret
    else:
        raise OIDCProtocolError("The identity provider supports no compatible client authentication method")
    status, data = _request_json("POST", discovery.token_endpoint, data=form, headers=headers)
    if status != 200:
        error = data.get("error") if isinstance(data.get("error"), str) else "token_request_failed"
        raise Unauthorized(f"The identity provider rejected the sign-in ({error})", code="sso_exchange_failed")
    if not isinstance(data.get("id_token"), str):
        raise OIDCProtocolError("The identity provider did not return an ID token")
    return data


def _signing_key(jwks: dict[str, Any], kid: str | None, alg: str) -> Any | None:
    candidates = []
    for jwk in jwks.get("keys", []):
        if not isinstance(jwk, dict):
            continue
        if jwk.get("use") not in (None, "sig"):
            continue
        if jwk.get("alg") not in (None, alg):
            continue
        if kid is not None and jwk.get("kid") != kid:
            continue
        candidates.append(jwk)
    if not candidates or (kid is None and len(candidates) > 1):
        return None
    try:
        return jwt.PyJWK(candidates[0], algorithm=alg).key
    except jwt.PyJWTError as exc:
        raise OIDCProtocolError("The identity provider published an unusable signing key") from exc


def validate_id_token(
    id_token: str, *, discovery: DiscoveryDocument, client_id: str, expected_nonce: str
) -> dict[str, Any]:
    """Verify an ID token's signature (JWKS) and claims. Returns the verified claims."""
    try:
        header = jwt.get_unverified_header(id_token)
    except jwt.PyJWTError as exc:
        raise Unauthorized("Malformed ID token", code="sso_invalid_token") from exc
    alg = header.get("alg")
    if not isinstance(alg, str) or alg not in ALLOWED_ID_TOKEN_ALGORITHMS:
        raise Unauthorized("Unsupported ID token algorithm", code="sso_invalid_token")
    kid = header.get("kid") if isinstance(header.get("kid"), str) else None
    key = _signing_key(fetch_jwks(discovery.jwks_uri), kid, alg)
    if key is None:  # the provider may have rotated keys since we cached its JWKS
        key = _signing_key(fetch_jwks(discovery.jwks_uri, force=True), kid, alg)
    if key is None:
        raise Unauthorized("ID token signing key not found", code="sso_invalid_token")
    try:
        claims: dict[str, Any] = jwt.decode(
            id_token,
            key,
            algorithms=[alg],
            audience=client_id,
            issuer=discovery.issuer,
            leeway=ID_TOKEN_LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise Unauthorized("ID token expired", code="sso_invalid_token") from exc
    except jwt.PyJWTError as exc:
        raise Unauthorized("Invalid ID token", code="sso_invalid_token") from exc
    nonce = claims.get("nonce")
    if not isinstance(nonce, str) or not hmac.compare_digest(nonce, expected_nonce):
        raise Unauthorized("ID token nonce mismatch", code="sso_invalid_token")
    audience = claims.get("aud")
    azp = claims.get("azp")
    if isinstance(audience, list) and len(audience) > 1 and azp is None:
        raise Unauthorized("ID token has multiple audiences but no authorized party", code="sso_invalid_token")
    if azp is not None and azp != client_id:
        raise Unauthorized("ID token authorized party mismatch", code="sso_invalid_token")
    if not isinstance(claims.get("sub"), str) or not claims["sub"]:
        raise Unauthorized("ID token has no subject", code="sso_invalid_token")
    return claims


def require_scopes(scopes: list[str]) -> list[str]:
    cleaned = []
    for scope in scopes:
        value = scope.strip()
        if not value or len(value) > 64 or any(c.isspace() or c in '"\\' for c in value):
            raise ValidationFailed(f"Invalid OIDC scope: {scope!r}")
        if value not in cleaned:
            cleaned.append(value)
    if "openid" not in cleaned:
        raise ValidationFailed("OIDC scopes must include 'openid'")
    return cleaned
