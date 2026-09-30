"""The object storage contract shared by every backend.

Bytes (dataset versions, artifacts, execution outputs) live in object storage; the database only stores
keys, sizes and SHA-256 checksums. Every backend implements :class:`ObjectStorage`:

* writes are streaming (``put_stream``) with a hard ``max_bytes`` limit enforced while reading — a
  partially written object is removed and :class:`~aegis_api.errors.PayloadTooLarge` is raised;
* reads are streaming (``open_stream`` yields bounded chunks); ``get_bytes`` refuses objects larger
  than the caller's ``max_bytes`` so huge objects are never loaded into memory by accident;
* keys are validated (:mod:`aegis_api.lab.storage.keys`) before any backend call;
* downloads are handed to clients as short-lived signed URLs — storage credentials never leave the
  server.
"""

from __future__ import annotations

import hashlib
import io
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any, BinaryIO, Protocol, runtime_checkable

from aegis_api.errors import NotFound, PayloadTooLarge, ServiceUnavailable, ValidationFailed

DEFAULT_CHUNK_SIZE = 1024 * 1024
DEFAULT_MAX_READ_BYTES = 64 * 1024 * 1024
DEFAULT_CONTENT_TYPE = "application/octet-stream"


class StorageUnavailable(ServiceUnavailable):
    """The object storage backend is unavailable."""

    code = "storage_unavailable"


class ObjectNotFound(NotFound):
    """The stored object was not found."""

    code = "object_not_found"


class InvalidObjectKey(ValidationFailed):
    """The object key is not a valid storage key."""

    code = "invalid_object_key"


@dataclass(frozen=True)
class StoredObject:
    """Result of a completed write: the key and the integrity facts of the bytes actually stored."""

    key: str
    size: int
    sha256: str
    content_type: str = DEFAULT_CONTENT_TYPE


@dataclass(frozen=True)
class ObjectStat:
    key: str
    size: int
    content_type: str | None = None
    sha256: str | None = None  # only when the backend recorded it at write time
    etag: str | None = None
    last_modified: datetime | None = None


@runtime_checkable
class ObjectStorage(Protocol):
    """Backend-agnostic object storage (local filesystem, S3-compatible)."""

    backend: str

    def put_bytes(self, key: str, data: bytes, content_type: str = DEFAULT_CONTENT_TYPE) -> StoredObject: ...

    def put_stream(
        self,
        key: str,
        fileobj: BinaryIO,
        content_type: str = DEFAULT_CONTENT_TYPE,
        max_bytes: int | None = None,
    ) -> StoredObject: ...

    def open_stream(self, key: str, chunk_size: int = DEFAULT_CHUNK_SIZE) -> Iterator[bytes]: ...

    def get_bytes(self, key: str, max_bytes: int = DEFAULT_MAX_READ_BYTES) -> bytes: ...

    def stat(self, key: str) -> ObjectStat: ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> None: ...

    def copy(self, source_key: str, dest_key: str) -> ObjectStat: ...

    def presign_get(
        self,
        key: str,
        ttl_seconds: int | None = None,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> str: ...

    def health(self) -> bool: ...


class HashingLimitedReader:
    """File-like wrapper that hashes everything read and enforces a byte limit.

    It deliberately reports itself as non-seekable so transfer managers read it once, sequentially,
    which keeps the running SHA-256 exact.
    """

    def __init__(self, fileobj: BinaryIO, max_bytes: int | None) -> None:
        self._fileobj = fileobj
        self._max_bytes = max_bytes
        self._hash = hashlib.sha256()
        self.size = 0
        self.exceeded = False

    def read(self, size: int = -1) -> bytes:
        if self.exceeded:
            raise PayloadTooLarge(self._limit_message())
        if size is None or size < 0:
            size = DEFAULT_CHUNK_SIZE
        chunk = self._fileobj.read(size)
        if not chunk:
            return b""
        self.size += len(chunk)
        if self._max_bytes is not None and self.size > self._max_bytes:
            self.exceeded = True
            raise PayloadTooLarge(self._limit_message())
        self._hash.update(chunk)
        return chunk

    def seekable(self) -> bool:
        return False

    def readable(self) -> bool:
        return True

    @property
    def sha256(self) -> str:
        return self._hash.hexdigest()

    def _limit_message(self) -> str:
        return f"Object exceeds the maximum allowed size of {self._max_bytes} bytes"


class _ChunkReader(io.RawIOBase):
    def __init__(self, chunks: Iterator[bytes]) -> None:
        self._chunks = iter(chunks)
        self._pending = b""

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        view = memoryview(buffer).cast("B")
        while not self._pending:
            try:
                self._pending = next(self._chunks)
            except StopIteration:
                return 0
        size = min(len(view), len(self._pending))
        view[:size] = self._pending[:size]
        self._pending = self._pending[size:]
        return size

    def close(self) -> None:
        close = getattr(self._chunks, "close", None)
        if callable(close):
            close()
        super().close()


def stream_reader(chunks: Iterator[bytes], buffer_size: int = DEFAULT_CHUNK_SIZE) -> io.BufferedReader:
    """Adapt a chunk iterator (e.g. ``open_stream``) into a sequential, read-only binary file object."""
    return io.BufferedReader(_ChunkReader(chunks), buffer_size=buffer_size)


def read_bounded(chunks: Iterator[bytes], max_bytes: int) -> bytes:
    """Collect a stream into memory, refusing anything larger than ``max_bytes``."""
    buf = bytearray()
    for chunk in chunks:
        buf.extend(chunk)
        if len(buf) > max_bytes:
            close = getattr(chunks, "close", None)
            if callable(close):
                close()
            raise PayloadTooLarge(f"Object exceeds the {max_bytes} byte read limit")
    return bytes(buf)
