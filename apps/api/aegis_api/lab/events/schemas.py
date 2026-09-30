"""API contract for the event log and outbound webhooks."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from aegis_api.schemas.common import ORMModel


class EventOut(ORMModel):
    id: int = Field(description="Monotonic event id (also the SSE event id used for Last-Event-ID resume)")
    event_uuid: str = Field(description="Globally unique event id (webhook payload `id`)")
    type: str
    created_at: datetime
    workspace_id: str | None = None
    project_id: str | None = None
    mission_id: str | None = None
    subject_type: str | None = None
    subject_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    actor: dict[str, Any] = Field(default_factory=dict, description="Who caused the event (kind, label, ids)")
    trace_id: str | None = None


def _clean_events(value: list[str] | None) -> list[str] | None:
    if value is None:
        return None
    out: list[str] = []
    for item in value:
        name = item.strip()
        if name and name not in out:
            out.append(name)
    return out


class WebhookCreate(BaseModel):
    url: str = Field(min_length=1, max_length=1000, description="HTTPS endpoint (validated against SSRF rules)")
    description: str | None = Field(default=None, max_length=300)
    events: list[str] = Field(
        default_factory=list,
        max_length=100,
        description="Event names to deliver (see GET /webhooks/event-types). Empty = every event.",
    )
    active: bool = True

    @field_validator("events")
    @classmethod
    def _dedupe(cls, value: list[str]) -> list[str]:
        return _clean_events(value) or []


class WebhookUpdate(BaseModel):
    url: str | None = Field(default=None, min_length=1, max_length=1000)
    description: str | None = Field(default=None, max_length=300)
    events: list[str] | None = Field(default=None, max_length=100)
    active: bool | None = None

    @field_validator("events")
    @classmethod
    def _dedupe(cls, value: list[str] | None) -> list[str] | None:
        return _clean_events(value)


class WebhookOut(ORMModel):
    id: str
    url: str
    description: str | None = None
    events: list[str] = Field(default_factory=list)
    active: bool
    failure_count: int = 0
    last_delivery_at: datetime | None = None
    secret_last4: str | None = Field(default=None, description="Last characters of the signing secret")
    created_at: datetime
    updated_at: datetime


class WebhookWithSecretOut(WebhookOut):
    secret: str = Field(
        description=(
            "Signing secret — returned only once. Verify `Aegis-Signature: t=<unix>,v1=<hex>` where "
            "v1 = HMAC-SHA256(secret, f'{t}.{raw_body}')."
        )
    )


class WebhookDeliveryOut(ORMModel):
    id: str
    webhook_id: str
    event_type: str
    event_id: str
    status: str = Field(description="pending | retrying | succeeded | failed")
    attempts: int
    next_attempt_at: datetime | None = None
    response_status: int | None = None
    response_excerpt: str | None = None
    last_error: str | None = None
    delivered_at: datetime | None = None
    created_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class WebhookEventTypesOut(BaseModel):
    events: list[str]
