"""Object storage abstraction: local filesystem, S3-compatible (S3, MinIO, R2, GCS interop, Azure gateway).

Usage::

    from aegis_api.lab.storage import get_storage
    from aegis_api.lab.storage.keys import object_key

    storage = get_storage()
    stored = storage.put_stream(object_key(org_id, project_id, "artifacts", aid, "v1", name), fh,
                                "text/csv", max_bytes=limit)
    for chunk in storage.open_stream(stored.key):
        ...

The backend is selected by ``OBJECT_STORAGE_BACKEND`` (``local`` | ``s3``) and built once per settings
object. ``configure_storage`` overrides it (tests, embedding).
"""

from __future__ import annotations

import threading

from aegis_api.config import Settings, get_settings
from aegis_api.lab.storage.base import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_CONTENT_TYPE,
    DEFAULT_MAX_READ_BYTES,
    InvalidObjectKey,
    ObjectNotFound,
    ObjectStat,
    ObjectStorage,
    StorageUnavailable,
    StoredObject,
)
from aegis_api.lab.storage.keys import object_key, split_relative_path, validate_key

__all__ = [
    "DEFAULT_CHUNK_SIZE",
    "DEFAULT_CONTENT_TYPE",
    "DEFAULT_MAX_READ_BYTES",
    "InvalidObjectKey",
    "ObjectNotFound",
    "ObjectStat",
    "ObjectStorage",
    "StorageUnavailable",
    "StoredObject",
    "build_storage",
    "configure_storage",
    "get_storage",
    "object_key",
    "split_relative_path",
    "validate_key",
]

_lock = threading.Lock()
_override: ObjectStorage | None = None
_cached: tuple[Settings, ObjectStorage] | None = None  # strong ref: identity check stays sound


def build_storage(settings: Settings | None = None) -> ObjectStorage:
    """Construct the configured backend (no caching)."""
    s = settings or get_settings()
    if s.object_storage_backend == "s3":
        from aegis_api.lab.storage.s3 import S3Storage

        return S3Storage.from_settings(s)
    from aegis_api.lab.storage.local import LocalFilesystemStorage

    return LocalFilesystemStorage(s.object_storage_local_dir)


def get_storage() -> ObjectStorage:
    """The process-wide object storage (rebuilt when the settings object changes)."""
    global _cached
    if _override is not None:
        return _override
    settings = get_settings()
    cached = _cached
    if cached is not None and cached[0] is settings:
        return cached[1]
    with _lock:
        if _cached is None or _cached[0] is not settings:
            _cached = (settings, build_storage(settings))
        return _cached[1]


def configure_storage(storage: ObjectStorage | None) -> None:
    """Install an explicit storage instance (``None`` restores settings-based selection)."""
    global _override, _cached
    with _lock:
        _override = storage
        _cached = None
