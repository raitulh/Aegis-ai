"""Object keys: tenant-scoped, sanitized, and validated before every storage call.

Every key has the shape ``org/<organization_id>/proj/<project_id>/<part>/<part>…`` where each part is
``[A-Za-z0-9._-]{1,200}``, never ``.``/``..``, never starts with a dot, and the whole key is ASCII with no
empty segments, no leading/trailing slash, no backslashes and no percent-escapes. This makes keys safe to
map onto a filesystem path (after an additional containment check in the local backend) and prevents one
tenant from addressing another tenant's objects through a crafted key.
"""

from __future__ import annotations

import re
import uuid

from aegis_api.lab.storage.base import InvalidObjectKey

MAX_PART_LENGTH = 200
MAX_KEY_LENGTH = 1024
MAX_PATH_DEPTH = 32

_PART_RE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]{0,199}$")
_INVALID_CHARS_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _as_uuid(value: uuid.UUID | str, label: str) -> str:
    try:
        return str(value if isinstance(value, uuid.UUID) else uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise InvalidObjectKey(f"{label} must be a UUID") from exc


def sanitize_part(part: object) -> str:
    """Map an arbitrary value onto one safe key segment (or raise when nothing safe remains)."""
    text = str(part)
    cleaned = _INVALID_CHARS_RE.sub("_", text).lstrip(".")
    cleaned = cleaned[:MAX_PART_LENGTH]
    if not cleaned or set(cleaned) <= {".", "_"}:
        raise InvalidObjectKey(f"Invalid object key segment {text[:40]!r}")
    return cleaned


def object_key(organization_id: uuid.UUID | str, project_id: uuid.UUID | str, *parts: object) -> str:
    """Build a tenant-scoped key ``org/<org>/proj/<project>/<parts…>`` from sanitized parts.

    Each positional part becomes exactly one segment: slashes inside a part are sanitized away, so
    callers cannot introduce extra directories (use :func:`split_relative_path` for nested paths).
    """
    if not parts:
        raise InvalidObjectKey("An object key needs at least one part below the project prefix")
    segments = ["org", _as_uuid(organization_id, "organization_id"), "proj", _as_uuid(project_id, "project_id")]
    segments.extend(sanitize_part(p) for p in parts)
    return validate_key("/".join(segments))


def validate_key(
    key: str,
    *,
    organization_id: uuid.UUID | str | None = None,
    project_id: uuid.UUID | str | None = None,
) -> str:
    """Return ``key`` unchanged when it is a well-formed object key, else raise ``InvalidObjectKey``.

    With ``organization_id`` (and optionally ``project_id``) the key must also live under that tenant's
    prefix — used whenever a key arrives from outside the current service call.
    """
    if not isinstance(key, str) or not key:
        raise InvalidObjectKey("Object key is empty")
    if len(key) > MAX_KEY_LENGTH:
        raise InvalidObjectKey("Object key is too long")
    if not key.isascii():
        raise InvalidObjectKey("Object key must be ASCII")
    segments = key.split("/")
    if len(segments) > MAX_PATH_DEPTH:
        raise InvalidObjectKey("Object key is nested too deeply")
    for segment in segments:
        if not _PART_RE.match(segment) or set(segment) <= {"."}:
            raise InvalidObjectKey("Object key contains an invalid segment")
    if organization_id is not None:
        prefix = ["org", _as_uuid(organization_id, "organization_id")]
        if project_id is not None:
            prefix += ["proj", _as_uuid(project_id, "project_id")]
        if segments[: len(prefix)] != prefix or len(segments) <= len(prefix):
            raise InvalidObjectKey("Object key is outside the expected tenant prefix")
    return key


def split_relative_path(path: str, *, max_depth: int = 16) -> list[str]:
    """Validate a client/sandbox-relative path (e.g. ``results/metrics.json``) and return its segments.

    Rejects absolute paths, drive letters, backslashes, ``.``/``..`` segments, empty segments and any
    character outside ``[A-Za-z0-9._-]`` (so percent-escapes and unicode look-alikes never pass).
    """
    if not isinstance(path, str) or not path or len(path) > MAX_KEY_LENGTH:
        raise InvalidObjectKey("Invalid relative path")
    if "\\" in path or path.startswith("/") or re.match(r"^[A-Za-z]:", path) or "\x00" in path:
        raise InvalidObjectKey("Relative path must not be absolute or contain backslashes")
    segments = path.split("/")
    if len(segments) > max_depth:
        raise InvalidObjectKey("Relative path is nested too deeply")
    for segment in segments:
        if not _PART_RE.match(segment) or set(segment) <= {"."}:
            raise InvalidObjectKey("Relative path contains an invalid segment")
    return segments
