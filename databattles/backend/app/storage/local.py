from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from app.storage.base import StoredObject, copy_with_limits


class LocalStorage:
    """Filesystem-backed storage for development and single-node deployments."""

    name = "local"

    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        if not key or key.startswith("/") or ".." in key.split("/") or "\\" in key:
            raise ValueError("invalid storage key")
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise ValueError("storage key escapes root")
        return path

    def save_stream(self, key: str, stream: BinaryIO, *, max_bytes: int, content_type: str) -> StoredObject:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        try:
            with open(tmp, "wb") as fh:
                size, sha, head = copy_with_limits(stream, fh, max_bytes)
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return StoredObject(key=key, size_bytes=size, sha256=sha, head=head)

    def open(self, key: str) -> BinaryIO:
        return open(self._path(key), "rb")

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def presigned_url(self, key: str, *, filename: str, content_type: str, ttl_seconds: int) -> str | None:
        return None  # served through the API's signed download route

    def list_keys(self, prefix: str) -> Iterator[tuple[str, datetime]]:
        base = self._path(prefix) if prefix else self.root
        if not base.exists():
            return
        for p in base.rglob("*"):
            if p.is_file():
                yield str(p.relative_to(self.root)), datetime.fromtimestamp(p.stat().st_mtime, UTC)

    def local_path(self, key: str) -> str | None:
        return str(self._path(key))
