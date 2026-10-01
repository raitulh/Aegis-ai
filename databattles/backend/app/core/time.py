"""Time helpers. All persisted timestamps are timezone-aware UTC."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, available_timezones

_VALID_TZ: set[str] | None = None


def utcnow() -> datetime:
    return datetime.now(UTC)


def ensure_aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def is_valid_timezone(name: str) -> bool:
    global _VALID_TZ
    if _VALID_TZ is None:
        _VALID_TZ = set(available_timezones())
    return name in _VALID_TZ or name == "UTC"


def to_zone(dt: datetime, tz: str) -> datetime:
    return dt.astimezone(ZoneInfo(tz))


def utc_day_start(dt: datetime | None = None) -> datetime:
    dt = dt or utcnow()
    return dt.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
