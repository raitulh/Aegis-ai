"""Local filesystem object storage (single node / development / on-prem volume).

* The root is resolved once; every key is validated (:func:`validate_key`) and the resolved target path
  must stay inside the root (``Path.resolve().is_relative_to(root)``), so traversal through crafted
  keys or planted symlinks is impossible.
* Writes stream into a private temp file in the destination directory while hashing and enforcing the
  size limit, are ``fsync``-ed, then atomically ``os.replace``-d into place (readers never observe a
  partial object; an aborted write leaves nothing behind).
* SHA-256 and content type are recorded as extended attributes when the filesystem supports them; the
  database remains the source of truth for integrity facts.
* ``presign_get`` returns an API URL carrying an HMAC-signed, expiring token (see ``signing.py``) served
  by ``GET /api/v1/storage/objects/{token}``.
"""

from __future__ import annotations

import contextlib
import io
import os
import stat as stat_mod
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

import structlog

from aegis_api.config import get_settings
from aegis_api.errors import PayloadTooLarge
from aegis_api.lab.storage.base import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_CONTENT_TYPE,
    DEFAULT_MAX_READ_BYTES,
    HashingLimitedReader,
    InvalidObjectKey,
    ObjectNotFound,
    ObjectStat,
    StorageUnavailable,
    StoredObject,
    read_bounded,
)
from aegis_api.lab.storage.keys import validate_key
from aegis_api.lab.storage.signing import safe_content_type, sign_download

log = structlog.get_logger("aegis.lab.storage")

_XATTR_SHA256 = "user.aegis.sha256"
_XATTR_CONTENT_TYPE = "user.aegis.content_type"
SIGNED_OBJECT_PATH = "/api/v1/storage/objects/"


class LocalFilesystemStorage:
    backend = "local"

    def __init__(self, root: str | os.PathLike[str], *, public_base_url: str | None = None) -> None:
        resolved = Path(root).expanduser().resolve()
        try:
            resolved.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError as exc:
            raise StorageUnavailable("Object storage directory is not accessible") from exc
        self.root = resolved
        self._public_base_url = public_base_url

    # -- paths -------------------------------------------------------------------------------------
    def _contained(self, path: Path) -> Path:
        resolved = path.resolve()
        if resolved == self.root or not resolved.is_relative_to(self.root):
            raise InvalidObjectKey("Object key resolves outside the storage root")
        return resolved

    def path_for(self, key: str) -> Path:
        """Absolute filesystem path of ``key`` (validated and contained in the root)."""
        validate_key(key)
        return self._contained(self.root.joinpath(*key.split("/")))

    # -- writes ------------------------------------------------------------------------------------
    def put_bytes(self, key: str, data: bytes, content_type: str = DEFAULT_CONTENT_TYPE) -> StoredObject:
        return self.put_stream(key, io.BytesIO(data), content_type, max_bytes=None)

    def put_stream(
        self,
        key: str,
        fileobj: BinaryIO,
        content_type: str = DEFAULT_CONTENT_TYPE,
        max_bytes: int | None = None,
    ) -> StoredObject:
        path = self.path_for(key)
        ctype = safe_content_type(content_type)
        reader = HashingLimitedReader(fileobj, max_bytes)
        fd, tmp_name = self._mkstemp(path.parent)
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as out:
                while chunk := reader.read(DEFAULT_CHUNK_SIZE):
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            self._set_xattrs(tmp, reader.sha256, ctype)
            tmp.replace(path)
        except PayloadTooLarge:
            tmp.unlink(missing_ok=True)
            raise
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            log.error("local_storage_write_failed", key=key, error=type(exc).__name__)
            raise StorageUnavailable("Failed to write object to storage") from exc
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        self._fsync_dir(path.parent)
        return StoredObject(key=key, size=reader.size, sha256=reader.sha256, content_type=ctype)

    def _mkstemp(self, directory: Path) -> tuple[int, str]:
        # A concurrent delete may prune an empty parent between mkdir and mkstemp: retry once.
        for attempt in range(2):
            try:
                directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                self._contained(directory / "_")
                return tempfile.mkstemp(prefix=".upload-", suffix=".part", dir=directory)
            except FileNotFoundError:
                if attempt:
                    raise StorageUnavailable("Failed to create object directory") from None
            except OSError as exc:
                raise StorageUnavailable("Failed to create object directory") from exc
        raise StorageUnavailable("Failed to create object directory")  # pragma: no cover

    @staticmethod
    def _set_xattrs(path: Path, sha256: str, content_type: str) -> None:
        setxattr = getattr(os, "setxattr", None)
        if setxattr is None:
            return
        with contextlib.suppress(OSError):
            setxattr(path, _XATTR_SHA256, sha256.encode("ascii"))
            setxattr(path, _XATTR_CONTENT_TYPE, content_type.encode("ascii"))

    @staticmethod
    def _get_xattr(path: Path, name: str) -> str | None:
        getxattr = getattr(os, "getxattr", None)
        if getxattr is None:
            return None
        try:
            return bytes(getxattr(path, name)).decode("ascii")
        except (OSError, UnicodeDecodeError):
            return None

    @staticmethod
    def _fsync_dir(directory: Path) -> None:
        try:
            fd = os.open(directory, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(fd)
        except OSError:
            pass
        finally:
            os.close(fd)

    # -- reads -------------------------------------------------------------------------------------
    def open_stream(self, key: str, chunk_size: int = DEFAULT_CHUNK_SIZE) -> Iterator[bytes]:
        """Open eagerly (so a missing object raises now) and stream it in ``chunk_size`` chunks."""
        path = self.path_for(key)
        try:
            handle = path.open("rb")
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError) as exc:
            raise ObjectNotFound() from exc
        except OSError as exc:
            raise StorageUnavailable("Failed to read object from storage") from exc
        if not stat_mod.S_ISREG(os.fstat(handle.fileno()).st_mode):
            handle.close()
            raise ObjectNotFound()
        return self._iter_file(handle, max(4096, chunk_size))

    @staticmethod
    def _iter_file(handle: BinaryIO, chunk_size: int) -> Iterator[bytes]:
        try:
            while chunk := handle.read(chunk_size):
                yield chunk
        finally:
            handle.close()

    def get_bytes(self, key: str, max_bytes: int = DEFAULT_MAX_READ_BYTES) -> bytes:
        info = self.stat(key)
        if info.size > max_bytes:
            raise PayloadTooLarge(f"Object is {info.size} bytes, above the {max_bytes} byte read limit")
        return read_bounded(self.open_stream(key), max_bytes)

    def stat(self, key: str) -> ObjectStat:
        path = self.path_for(key)
        try:
            st = path.stat()
        except (FileNotFoundError, NotADirectoryError) as exc:
            raise ObjectNotFound() from exc
        except OSError as exc:
            raise StorageUnavailable("Failed to stat object") from exc
        if not stat_mod.S_ISREG(st.st_mode):
            raise ObjectNotFound()
        return ObjectStat(
            key=key,
            size=st.st_size,
            content_type=self._get_xattr(path, _XATTR_CONTENT_TYPE),
            sha256=self._get_xattr(path, _XATTR_SHA256),
            etag=None,
            last_modified=datetime.fromtimestamp(st.st_mtime, tz=UTC),
        )

    def exists(self, key: str) -> bool:
        try:
            return self.path_for(key).is_file()
        except OSError:
            return False

    # -- lifecycle ---------------------------------------------------------------------------------
    def delete(self, key: str) -> None:
        path = self.path_for(key)
        try:
            path.unlink(missing_ok=True)
        except IsADirectoryError as exc:
            raise ObjectNotFound() from exc
        except OSError as exc:
            raise StorageUnavailable("Failed to delete object") from exc
        parent = path.parent
        while parent != self.root and parent.is_relative_to(self.root):
            try:
                parent.rmdir()  # only succeeds when empty
            except OSError:
                break
            parent = parent.parent

    def copy(self, source_key: str, dest_key: str) -> ObjectStat:
        source_path = self.path_for(source_key)
        content_type = self._get_xattr(source_path, _XATTR_CONTENT_TYPE) or DEFAULT_CONTENT_TYPE
        try:
            handle = source_path.open("rb")
        except (FileNotFoundError, IsADirectoryError) as exc:
            raise ObjectNotFound() from exc
        with handle:
            stored = self.put_stream(dest_key, handle, content_type)
        return ObjectStat(key=dest_key, size=stored.size, content_type=content_type, sha256=stored.sha256)

    def presign_get(
        self,
        key: str,
        ttl_seconds: int | None = None,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> str:
        validate_key(key)
        token = sign_download(key, ttl_seconds=ttl_seconds, filename=filename, content_type=content_type)
        base = (self._public_base_url or get_settings().api_base_url).rstrip("/")
        return f"{base}{SIGNED_OBJECT_PATH}{token}"

    def health(self) -> bool:
        try:
            fd, name = tempfile.mkstemp(prefix=".health-", dir=self.root)
            os.close(fd)
            Path(name).unlink(missing_ok=True)
            return True
        except OSError:
            return False
