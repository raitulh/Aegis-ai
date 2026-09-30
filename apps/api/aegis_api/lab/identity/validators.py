"""Input normalisation/validation for identity & organization settings (pure functions, no I/O)."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable

from aegis_api.errors import ValidationFailed
from aegis_api.models.enums import Role
from aegis_api.security.rbac import ROLE_PERMISSIONS

_LABEL = r"(?!-)[a-z0-9-]{1,63}(?<!-)"
_HOSTNAME_RE = re.compile(rf"^{_LABEL}(?:\.{_LABEL})+$")
_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,78}[a-z0-9])?$")
_SLUG_SANITIZE_RE = re.compile(r"[^a-z0-9]+")
# Docker/OCI image reference: [registry[:port]/]path[:tag][@sha256:digest]
_COMPONENT = r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*"
_IMAGE_RE = re.compile(
    rf"^(?:[a-z0-9]+(?:[.-][a-z0-9]+)*(?::[0-9]{{1,5}})?/)?{_COMPONENT}(?:/{_COMPONENT})*"
    r"(?::[A-Za-z0-9_][A-Za-z0-9_.-]{0,127})?(?:@sha256:[a-f0-9]{64})?$"
)
_IMAGE_PREFIX_RE = re.compile(r"^[a-z0-9][a-z0-9._/:@-]*\*$")

ASSIGNABLE_ROLES = frozenset(r for r in ROLE_PERMISSIONS if r != Role.OWNER)
MAX_EGRESS_HOSTS = 200
MAX_ALLOWED_IMAGES = 100
MAX_EMAIL_DOMAINS = 50


def slugify(value: str, fallback: str = "item", max_length: int = 80) -> str:
    slug = _SLUG_SANITIZE_RE.sub("-", value.lower()).strip("-")[:max_length].strip("-")
    return slug or fallback


def validate_slug(value: str) -> str:
    slug = value.strip().lower()
    if not _SLUG_RE.match(slug):
        raise ValidationFailed("Slug must be 1-80 lowercase letters, digits or hyphens (no leading/trailing hyphen)")
    return slug


def normalize_hostname(value: str, *, allow_suffix: bool) -> str:
    """A DNS hostname (``api.example.org``) or, when allowed, a leading-dot suffix (``.example.org``).

    IP literals, wildcards, ports, schemes, paths, single-label names and non-ASCII names are rejected.
    """
    raw = (value or "").strip().lower()
    suffix = raw.startswith(".")
    host = raw[1:] if suffix else raw
    if suffix and not allow_suffix:
        raise ValidationFailed(f"'{value}' must be a hostname, not a suffix")
    if not host or len(host) > 253 or any(ch in host for ch in "*/:@?#[] "):
        raise ValidationFailed(f"'{value}' is not a valid hostname (no wildcards, ports, schemes or paths)")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValidationFailed(f"'{value}' is an IP address; only hostnames are allowed")
    if not _HOSTNAME_RE.match(host) or host.rsplit(".", 1)[-1].isdigit():
        raise ValidationFailed(f"'{value}' is not a valid fully-qualified hostname")
    return f".{host}" if suffix else host


def normalize_hostnames(values: Iterable[str], *, allow_suffix: bool, limit: int) -> list[str]:
    result: list[str] = []
    for value in values:
        host = normalize_hostname(value, allow_suffix=allow_suffix)
        if host not in result:
            result.append(host)
    if len(result) > limit:
        raise ValidationFailed(f"At most {limit} entries are allowed")
    return result


def _image_permitted(image: str, platform_allowlist: list[str]) -> bool:
    """An org image entry is permitted when it is covered by the platform allowlist (subset rule)."""
    is_prefix = image.endswith("*")
    for entry in platform_allowlist:
        if entry.endswith("*"):
            base = entry[:-1]
            if (image[:-1] if is_prefix else image).startswith(base):
                return True
        elif not is_prefix and entry == image:
            return True
    return False


def normalize_allowed_images(values: Iterable[str], platform_allowlist: list[str]) -> list[str]:
    """Validate image references (exact refs or ``prefix*``) and require each to be allowed by the platform."""
    result: list[str] = []
    for value in values:
        image = (value or "").strip()
        if image.endswith("*"):
            if image.count("*") != 1 or not _IMAGE_PREFIX_RE.match(image):
                raise ValidationFailed(f"'{value}' is not a valid image prefix pattern")
        elif not _IMAGE_RE.match(image) or len(image) > 255:
            raise ValidationFailed(f"'{value}' is not a valid container image reference")
        if not _image_permitted(image, platform_allowlist):
            raise ValidationFailed(f"'{value}' is not permitted by the platform image allowlist")
        if image not in result:
            result.append(image)
    if len(result) > MAX_ALLOWED_IMAGES:
        raise ValidationFailed(f"At most {MAX_ALLOWED_IMAGES} images are allowed")
    return result


def validate_assignable_role(role: str) -> str:
    """A known role that can be granted through membership APIs (never ``owner``)."""
    if role == Role.OWNER:
        raise ValidationFailed("The owner role cannot be granted here")
    if role not in ASSIGNABLE_ROLES:
        raise ValidationFailed(f"Unknown role '{role}'. Allowed: {', '.join(sorted(ASSIGNABLE_ROLES))}")
    return role
