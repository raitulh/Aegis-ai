"""Identifier helpers: time-ordered UUIDs, slugs, and short public ids."""

from __future__ import annotations

import os
import re
import secrets
import time
import unicodedata
import uuid

_SLUG_RE = re.compile(r"[^a-z0-9]+")
HANDLE_RE = re.compile(r"^[a-z0-9](?:[a-z0-9_-]{1,28}[a-z0-9])$")
SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,78}[a-z0-9])?$")
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def uuid7() -> uuid.UUID:
    """RFC 9562 UUIDv7: millisecond timestamp prefix keeps btree inserts local."""
    ts_ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand_a = int.from_bytes(os.urandom(2), "big") & 0xFFF
    rand_b = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)
    value = (ts_ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return uuid.UUID(int=value)


def slugify(text: str, max_length: int = 60) -> str:
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = _SLUG_RE.sub("-", normalized.lower()).strip("-")
    return (slug[:max_length].rstrip("-")) or "item"


def random_suffix(n: int = 4) -> str:
    return "".join(secrets.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(n))


def crockford_random(n: int) -> str:
    return "".join(secrets.choice(_CROCKFORD) for _ in range(n))


def is_valid_handle(handle: str) -> bool:
    return bool(HANDLE_RE.match(handle))
