"""Object storage abstraction.

Large bytes (datasets, submissions, media) live in object storage; the relational
database stores only metadata and random, server-generated keys. Keys are never
derived from user input, which rules out path traversal by construction.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import BinaryIO, Protocol

from app.core.errors import PayloadTooLarge

CHUNK = 1024 * 1024

# Prefix conventions. PRIVATE_PREFIX objects are never served to clients.
PREFIX_DATASETS = "datasets"
PREFIX_SUBMISSIONS = "submissions"
PREFIX_MEDIA = "media"
PREFIX_TMP = "tmp"
PRIVATE_PREFIX = "private"


@dataclass
class StoredObject:
    key: str
    size_bytes: int
    sha256: str
    head: bytes  # first bytes, for sniffing/preview without re-reading


class StorageBackend(Protocol):
    name: str

    def save_stream(self, key: str, stream: BinaryIO, *, max_bytes: int, content_type: str) -> StoredObject: ...
    def open(self, key: str) -> BinaryIO: ...
    def delete(self, key: str) -> None: ...
    def exists(self, key: str) -> bool: ...
    def presigned_url(self, key: str, *, filename: str, content_type: str, ttl_seconds: int) -> str | None: ...
    def list_keys(self, prefix: str) -> Iterator[tuple[str, datetime]]: ...
    def local_path(self, key: str) -> str | None: ...


def new_key(prefix: str, extension: str = "") -> str:
    now = datetime.now(UTC)
    ext = extension if extension.startswith(".") or not extension else "." + extension
    return f"{prefix}/{now:%Y/%m}/{secrets.token_hex(16)}{ext.lower()}"


def copy_with_limits(src: BinaryIO, dst: BinaryIO, max_bytes: int, head_size: int = 256 * 1024) -> tuple[int, str, bytes]:
    """Stream src→dst computing sha256; abort as soon as max_bytes is exceeded."""
    digest = hashlib.sha256()
    size = 0
    head = bytearray()
    while True:
        chunk = src.read(CHUNK)
        if not chunk:
            break
        size += len(chunk)
        if size > max_bytes:
            raise PayloadTooLarge(f"File exceeds the {max_bytes // (1024 * 1024)} MB limit.", code="file_too_large")
        digest.update(chunk)
        if len(head) < head_size:
            head.extend(chunk[: head_size - len(head)])
        dst.write(chunk)
    return size, digest.hexdigest(), bytes(head)
