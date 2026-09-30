"""Calls into other bounded contexts (object storage, datasets, artifacts), imported lazily.

The data and storage contexts are separate modules; importing them lazily keeps the execution context
importable on its own and gives tests one place to substitute them (``monkeypatch.setattr(integrations,
"create_artifact_version", fake)``). When a dependency is missing the call fails explicitly with
``ExecutionUnavailable`` — execution never pretends inputs were staged or outputs were stored.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

from sqlalchemy.orm import Session

from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.errors import ExecutionUnavailable


def _missing(what: str, exc: ImportError) -> ExecutionUnavailable:
    return ExecutionUnavailable(f"The {what} service is not available in this deployment ({exc.name})")


def get_storage() -> Any:
    try:
        from aegis_api.lab.storage import get_storage as _get_storage
    except ImportError as exc:
        raise _missing("object storage", exc) from exc
    return _get_storage()


def object_key(organization_id: uuid.UUID | str, project_id: uuid.UUID | str, *parts: object) -> str:
    try:
        from aegis_api.lab.storage.keys import object_key as _object_key
    except ImportError as exc:
        raise _missing("object storage", exc) from exc
    return _object_key(organization_id, project_id, *parts)


def open_dataset_split(db: Session, actor: Actor, version_id: uuid.UUID | str, split: str | None) -> Iterator[bytes]:
    """Stream a dataset version split for a sandbox (evaluator-only splits are refused by the data service)."""
    try:
        from aegis_api.lab.data.datasets import open_dataset_split as _open
    except ImportError as exc:
        raise _missing("dataset", exc) from exc
    return _open(db, actor, version_id, split, for_evaluator=False)


def open_artifact_stream(db: Session, actor: Actor, version_id: uuid.UUID | str) -> Iterator[bytes]:
    try:
        from aegis_api.lab.data.artifacts import open_artifact_stream as _open
    except ImportError as exc:
        raise _missing("artifact", exc) from exc
    return _open(db, actor, version_id)


def create_artifact_version(db: Session, actor: Actor, **kwargs: Any) -> Any:
    try:
        from aegis_api.lab.data.artifacts import create_artifact_version as _create
    except ImportError as exc:
        raise _missing("artifact", exc) from exc
    return _create(db, actor, **kwargs)


def read_artifact_bytes(db: Session, actor: Actor, version_id: uuid.UUID | str, max_bytes: int) -> bytes:
    try:
        from aegis_api.lab.data.artifacts import read_artifact_bytes as _read
    except ImportError as exc:
        raise _missing("artifact", exc) from exc
    return _read(db, actor, version_id, max_bytes)
