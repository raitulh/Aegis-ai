"""Execution environments: registered container images (optionally digest-pinned) with declared packages.

Experiments may only run in a registered environment. The platform ships one global built-in environment
based on ``EXECUTION_DEFAULT_IMAGE``; organizations register their own images (``environment:manage``).
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.errors import Conflict, NotFound, ServiceUnavailable, ValidationFailed
from aegis_api.models.lab import ExecutionEnvironment
from aegis_api.security.context import Principal
from aegis_api.services import audit_log
from aegis_api.services.lab.common import sha256_json
from engines.lab.experiments.validator import EnvironmentInfo, ResourceLimits

BUILTIN_NAME = "python-stdlib"
_IMAGE = re.compile(r"^[a-z0-9][a-z0-9._/:-]{0,300}(@sha256:[a-f0-9]{64})?$")
_PIN = re.compile(r"^[A-Za-z0-9_.\-\[\]]+==[A-Za-z0-9_.+\-]+$")


def seed_builtin(admin_db: Session) -> ExecutionEnvironment:
    """Reference-data seeding (admin session, at startup): the global built-in environment."""
    env = admin_db.scalar(
        select(ExecutionEnvironment).where(
            ExecutionEnvironment.organization_id.is_(None), ExecutionEnvironment.name == BUILTIN_NAME
        )
    )
    image = get_settings().execution_default_image
    if env is None:
        env = ExecutionEnvironment(
            organization_id=None,
            name=BUILTIN_NAME,
            description="Platform built-in: Python standard library only (no third-party packages).",
            image=image,
            image_digest=image.split("@", 1)[1] if "@" in image else None,
            packages={},
            runtime="cpu",
            status="active",
        )
        admin_db.add(env)
        admin_db.flush()
    elif env.image != image:
        env.image = image
        env.image_digest = image.split("@", 1)[1] if "@" in image else None
    return env


def ensure_builtin(db: Session) -> ExecutionEnvironment:
    """The global built-in environment (read-only from tenant sessions; seeded at startup)."""
    env = db.scalar(
        select(ExecutionEnvironment).where(
            ExecutionEnvironment.organization_id.is_(None), ExecutionEnvironment.name == BUILTIN_NAME
        )
    )
    if env is None:
        raise ServiceUnavailable(
            "The built-in execution environment is not seeded (start the API once, or run scripts/seed_demo.py)",
            code="environment_not_seeded",
        )
    return env


def list_environments(db: Session, organization_id: uuid.UUID) -> list[ExecutionEnvironment]:
    return list(
        db.scalars(
            select(ExecutionEnvironment)
            .where(
                or_(
                    ExecutionEnvironment.organization_id.is_(None),
                    ExecutionEnvironment.organization_id == organization_id,
                ),
                ExecutionEnvironment.status == "active",
            )
            .order_by(ExecutionEnvironment.organization_id.is_not(None), ExecutionEnvironment.name)
        ).all()
    )


def get_environment(db: Session, organization_id: uuid.UUID, env_id: uuid.UUID | str | None) -> ExecutionEnvironment:
    if env_id in (None, ""):
        return ensure_builtin(db)
    try:
        env = db.get(ExecutionEnvironment, uuid.UUID(str(env_id)))
    except ValueError as exc:
        raise NotFound("Environment not found") from exc
    if env is None or env.organization_id not in (None, organization_id) or env.status != "active":
        raise NotFound("Environment not found")
    return env


def resolve_for_spec(db: Session, organization_id: uuid.UUID, env_spec: Any) -> ExecutionEnvironment:
    """The environment an experiment runs in. Only *registered* images ever execute: an image named in a spec
    must match a registered environment (built-in or the organization's)."""
    if getattr(env_spec, "environment_id", None):
        return get_environment(db, organization_id, env_spec.environment_id)
    image = getattr(env_spec, "image", None)
    ensure_builtin(db)
    if image:
        for env in list_environments(db, organization_id):
            if env.image == image:
                return env
        raise ValidationFailed(f"image '{image}' is not a registered execution environment")
    return ensure_builtin(db)


def register(
    db: Session,
    principal: Principal,
    *,
    name: str,
    image: str,
    description: str | None,
    packages: dict[str, str] | list[str],
    runtime: str = "cpu",
) -> ExecutionEnvironment:
    principal.require("environment:manage")
    settings = get_settings()
    if not _IMAGE.match(image):
        raise ValidationFailed("image must be a valid reference (registry/name:tag or name@sha256:<digest>)")
    if settings.execution_require_digest_pinned_images and "@sha256:" not in image:
        raise ValidationFailed("This deployment requires digest-pinned images (name@sha256:<digest>)")
    if runtime not in ("cpu", "gpu"):
        raise ValidationFailed("runtime must be cpu or gpu")
    pins = [f"{k}=={v}" for k, v in packages.items()] if isinstance(packages, dict) else list(packages)
    bad = [p for p in pins if not _PIN.match(p)]
    if bad:
        raise ValidationFailed(f"packages must be pinned as name==version: {bad[:5]}")
    env = ExecutionEnvironment(
        organization_id=principal.organization_id,
        name=name[:120],
        description=description,
        image=image,
        image_digest=image.split("@", 1)[1] if "@" in image else None,
        packages={p.split("==")[0]: p.split("==")[1] for p in pins},
        lockfile_sha256=sha256_json(sorted(pins)),
        runtime=runtime,
        status="active",
    )
    db.add(env)
    try:
        db.flush()
    except IntegrityError as exc:
        raise Conflict("An environment with this name already exists") from exc
    audit_log.record(
        db,
        organization_id=principal.organization_id,
        action="lab.environment.registered",
        resource_type="environment",
        resource_id=env.id,
        principal=principal,
        after={"image": image, "packages": len(pins)},
    )
    return env


def validator_inputs(db: Session, organization_id: uuid.UUID) -> tuple[ResourceLimits, list[EnvironmentInfo]]:
    s = get_settings()
    ensure_builtin(db)
    envs = [
        EnvironmentInfo(environment_id=str(e.id), image=e.image, packages=dict(e.packages or {}))
        for e in list_environments(db, organization_id)
    ]
    limits = ResourceLimits(
        max_cpu=s.execution_max_cpu,
        max_memory_mb=s.execution_max_memory_mb,
        max_timeout_seconds=s.execution_max_timeout_seconds,
        max_gpu_count=s.execution_max_gpu_count,
        allowed_gpu_types=s.allowed_gpu_types,
        require_digest_pinned_images=s.execution_require_digest_pinned_images,
    )
    return limits, envs


def environment_dict(e: ExecutionEnvironment) -> dict[str, Any]:
    return {
        "id": str(e.id),
        "name": e.name,
        "description": e.description,
        "image": e.image,
        "image_digest": e.image_digest,
        "packages": e.packages,
        "runtime": e.runtime,
        "builtin": e.organization_id is None,
    }
