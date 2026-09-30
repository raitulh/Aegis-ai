"""Unit tests for lab JWT access tokens (issue/verify, expiry, audience/issuer, kid rotation, alg attacks)."""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from aegis_api.errors import Unauthorized

SECRET_A = "unit-test-secret-a-0123456789abcdef0123456789abcdef"
SECRET_B = "unit-test-secret-b-fedcba9876543210fedcba9876543210"


@pytest.fixture
def jwt_settings(monkeypatch):
    """Configure JWT keys for a test and restore the cached settings afterwards."""
    from aegis_api.config import get_settings

    def configure(*, active: str, keys: dict[str, str], secret: str | None = None) -> None:
        monkeypatch.setenv("JWT_ACTIVE_KID", active)
        monkeypatch.setenv("JWT_SECRETS_JSON", json.dumps(keys))
        if secret is None:
            monkeypatch.delenv("JWT_SECRET", raising=False)
        else:
            monkeypatch.setenv("JWT_SECRET", secret)
        monkeypatch.setenv("JWT_ISSUER", "aegis-lab-test")
        monkeypatch.setenv("JWT_AUDIENCE", "aegis-api-test")
        get_settings.cache_clear()

    configure(active="kid-a", keys={"kid-a": SECRET_A})
    yield configure
    get_settings.cache_clear()


def _claims(**overrides):
    now = int(datetime.now(UTC).timestamp())
    claims = {
        "iss": "aegis-lab-test",
        "aud": "aegis-api-test",
        "sub": str(uuid.uuid4()),
        "org": str(uuid.uuid4()),
        "role": "researcher",
        "iat": now,
        "nbf": now,
        "exp": now + 600,
        "jti": uuid.uuid4().hex,
        "typ": "access",
    }
    claims.update(overrides)
    return {k: v for k, v in claims.items() if v is not None}


def _b64(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")


def _expect_invalid(token: str, code: str = "invalid_token") -> Unauthorized:
    from aegis_api.security.jwt_tokens import decode_access_token

    with pytest.raises(Unauthorized) as exc:
        decode_access_token(token)
    assert exc.value.code == code
    return exc.value


def test_issue_and_verify_roundtrip(jwt_settings):
    from aegis_api.security.jwt_tokens import decode_access_token, issue_access_token, looks_like_access_token

    user_id, org_id, family = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    issued = issue_access_token(user_id=user_id, organization_id=org_id, role="admin", session_id=family)
    header = jwt.get_unverified_header(issued.token)
    assert header["alg"] == "HS256"
    assert header["kid"] == "kid-a"
    assert looks_like_access_token(issued.token)

    claims = decode_access_token(issued.token)
    assert claims.user_id == user_id
    assert claims.organization_id == org_id
    assert claims.role == "admin"
    assert claims.session_id == family
    assert claims.kid == "kid-a"
    assert claims.jti == issued.jti
    assert claims.raw["iss"] == "aegis-lab-test"
    assert claims.raw["aud"] == "aegis-api-test"
    assert claims.raw["typ"] == "access"
    assert claims.raw["nbf"] == claims.raw["iat"]
    assert claims.raw["exp"] - claims.raw["iat"] == issued.expires_in == 900


def test_extra_claims_cannot_override_reserved_claims(jwt_settings):
    from aegis_api.security.jwt_tokens import decode_access_token, issue_access_token

    org_id = uuid.uuid4()
    issued = issue_access_token(
        user_id=uuid.uuid4(),
        organization_id=org_id,
        role="viewer",
        extra_claims={"org": str(uuid.uuid4()), "role": "owner", "amr": ["pwd"]},
    )
    claims = decode_access_token(issued.token)
    assert claims.organization_id == org_id
    assert claims.role == "viewer"
    assert claims.raw["amr"] == ["pwd"]


def test_expired_token_rejected(jwt_settings):
    from aegis_api.security.jwt_tokens import issue_access_token

    issued = issue_access_token(
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        role="viewer",
        ttl_seconds=60,
        now=datetime.now(UTC) - timedelta(minutes=10),
    )
    _expect_invalid(issued.token, code="token_expired")


def test_clock_skew_within_leeway_accepted(jwt_settings):
    from aegis_api.security.jwt_tokens import decode_access_token, issue_access_token

    issued = issue_access_token(
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        role="viewer",
        ttl_seconds=60,
        now=datetime.now(UTC) - timedelta(seconds=70),  # expired 10s ago; leeway is 30s
    )
    assert decode_access_token(issued.token).role == "viewer"


def test_wrong_audience_and_issuer_rejected(jwt_settings):
    from aegis_api.security.jwt_tokens import looks_like_access_token

    wrong_aud = jwt.encode(_claims(aud="another-api"), SECRET_A, algorithm="HS256", headers={"kid": "kid-a"})
    _expect_invalid(wrong_aud)
    wrong_iss = jwt.encode(_claims(iss="evil-issuer"), SECRET_A, algorithm="HS256", headers={"kid": "kid-a"})
    _expect_invalid(wrong_iss)
    assert not looks_like_access_token(wrong_iss)


def test_unknown_or_missing_kid_rejected(jwt_settings):
    unknown = jwt.encode(_claims(), SECRET_A, algorithm="HS256", headers={"kid": "kid-unknown"})
    _expect_invalid(unknown)
    missing = jwt.encode(_claims(), SECRET_A, algorithm="HS256")
    _expect_invalid(missing)


def test_alg_none_rejected(jwt_settings):
    token = f"{_b64({'alg': 'none', 'typ': 'JWT', 'kid': 'kid-a'})}.{_b64(_claims())}."
    _expect_invalid(token)


def test_hs512_header_rejected_even_with_correct_key(jwt_settings):
    token = jwt.encode(_claims(), SECRET_A, algorithm="HS512", headers={"kid": "kid-a"})
    _expect_invalid(token)


def test_rs256_token_rejected(jwt_settings):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode(_claims(), private_key, algorithm="RS256", headers={"kid": "kid-a"})
    _expect_invalid(token)


def test_tampered_signature_and_payload_rejected(jwt_settings):
    from aegis_api.security.jwt_tokens import issue_access_token

    issued = issue_access_token(user_id=uuid.uuid4(), organization_id=uuid.uuid4(), role="viewer")
    header, payload, signature = issued.token.split(".")
    flipped = signature[:-2] + ("AA" if signature[-2:] != "AA" else "BB")
    _expect_invalid(f"{header}.{payload}.{flipped}")

    body = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    body["role"] = "owner"
    _expect_invalid(f"{header}.{_b64(body)}.{signature}")


def test_token_signed_with_other_secret_rejected(jwt_settings):
    token = jwt.encode(_claims(), SECRET_B, algorithm="HS256", headers={"kid": "kid-a"})
    _expect_invalid(token)


def test_required_claims_and_type_enforced(jwt_settings):
    for missing in ("exp", "iat", "sub", "jti"):
        token = jwt.encode(_claims(**{missing: None}), SECRET_A, algorithm="HS256", headers={"kid": "kid-a"})
        _expect_invalid(token)
    refresh_typed = jwt.encode(_claims(typ="refresh"), SECRET_A, algorithm="HS256", headers={"kid": "kid-a"})
    _expect_invalid(refresh_typed)
    bad_sub = jwt.encode(_claims(sub="not-a-uuid"), SECRET_A, algorithm="HS256", headers={"kid": "kid-a"})
    _expect_invalid(bad_sub)


def test_key_rotation_keeps_old_kid_valid_while_published(jwt_settings):
    from aegis_api.security.jwt_tokens import decode_access_token, issue_access_token

    old = issue_access_token(user_id=uuid.uuid4(), organization_id=uuid.uuid4(), role="viewer")
    assert jwt.get_unverified_header(old.token)["kid"] == "kid-a"

    # Rotate: kid-b becomes active, kid-a stays published for verification.
    jwt_settings(active="kid-b", keys={"kid-a": SECRET_A}, secret=SECRET_B)
    new = issue_access_token(user_id=uuid.uuid4(), organization_id=uuid.uuid4(), role="viewer")
    assert jwt.get_unverified_header(new.token)["kid"] == "kid-b"
    assert decode_access_token(old.token).kid == "kid-a"
    assert decode_access_token(new.token).kid == "kid-b"

    # Retire kid-a: its tokens stop verifying; kid-b keeps working.
    jwt_settings(active="kid-b", keys={}, secret=SECRET_B)
    _expect_invalid(old.token)
    assert decode_access_token(new.token).kid == "kid-b"


def test_unverified_issuer_handles_garbage(jwt_settings):
    from aegis_api.security.jwt_tokens import looks_like_access_token, unverified_issuer

    assert unverified_issuer("") is None
    assert unverified_issuer("aeg_live_abc") is None
    assert unverified_issuer("a.b.c") is None
    assert unverified_issuer("x." + _b64({"iss": 5}) + ".y") is None
    assert unverified_issuer("x." + _b64({"iss": "aegis-lab-test"}) + ".y") == "aegis-lab-test"
    assert not looks_like_access_token("x" * 10000)


def test_malformed_tokens_rejected(jwt_settings):
    for token in ("", "not-a-jwt", "a.b.c", "x" * 9000):
        _expect_invalid(token)
