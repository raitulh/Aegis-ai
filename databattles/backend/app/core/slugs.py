from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ValidationFailed
from app.core.ids import SLUG_RE, random_suffix, slugify

RESERVED_SLUGS = {"new", "edit", "admin", "api", "settings", "manage", "create", "search", "me", "all"}


def unique_slug(db: Session, model: Any, text: str, *, requested: str | None = None, max_length: int = 60) -> str:
    if requested:
        slug = requested.strip().lower()
        if not SLUG_RE.match(slug) or slug in RESERVED_SLUGS:
            raise ValidationFailed(details={"fields": {"slug": "Use lowercase letters, numbers and hyphens."}})
        if db.scalar(select(model.id).where(model.slug == slug)):
            raise ValidationFailed(details={"fields": {"slug": "That URL is already taken."}})
        return slug
    base = slugify(text, max_length)
    if base in RESERVED_SLUGS:
        base = f"{base}-{random_suffix(3)}"
    slug = base
    while db.scalar(select(model.id).where(model.slug == slug)):
        slug = f"{base[: max_length - 5]}-{random_suffix(4)}"
    return slug
