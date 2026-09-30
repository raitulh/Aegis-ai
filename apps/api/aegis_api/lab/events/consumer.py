"""Outbox consumer process: ``events`` → webhook deliveries → signed HTTP delivery.

Run with ``python -m aegis_api.lab.events.consumer`` (one or more replicas).

Per organization and pass:

1. A tenant (RLS-scoped) transaction takes the advisory lock ``event-consumer:<name>:<org>``; if another
   replica holds it the organization is skipped for this pass.
2. Events with ``id > offset`` whose type maps to a webhook event name are read in id order. Deliveries
   use ``event_id = event_uuid``; events that already have a delivery are skipped, so re-reading an
   event (after a crash, or inside the settle window) never produces a duplicate delivery.
3. The offset (``event_consumer_offsets``) advances in the *same* transaction, but only across events
   older than the settle window: identity ids are allocated at INSERT time while visibility happens at
   COMMIT, so a slow transaction can commit a lower id after a higher one was read. Keeping the offset
   behind the window lets such late events be picked up on the next pass.
4. Due deliveries are sent (``webhooks.deliver_due``) without holding any transaction open.
"""

from __future__ import annotations

import os
import signal
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from types import FrameType

import structlog
from sqlalchemy import func, select, union
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.db.session import admin_session_scope, session_scope
from aegis_api.lab.core.events import WEBHOOK_EVENT_NAMES
from aegis_api.lab.core.locks import try_advisory_xact_lock
from aegis_api.lab.events import webhooks
from aegis_api.lab.models import EventConsumerOffset, LabEvent
from aegis_api.lab.observability.metrics import (
    EVENT_CONSUMER_LAST_SUCCESS,
    EVENT_CONSUMER_RUNS,
    EVENTS_CONSUMED,
)
from aegis_api.models import Webhook, WebhookDelivery
from aegis_api.models.enums import DeliveryStatus

log = structlog.get_logger("aegis.lab.event_consumer")

DEFAULT_BATCH = 500
DEFAULT_SETTLE_SECONDS = 30.0
DEFAULT_DELIVER_LIMIT = 20


@dataclass
class OrgResult:
    organization_id: uuid.UUID
    locked: bool = True
    scanned: int = 0
    enqueued: int = 0
    duplicates: int = 0
    offset: int = 0
    delivered: int = 0
    failed_deliveries: int = 0
    error: str | None = None


@dataclass
class RunResult:
    organizations: list[OrgResult] = field(default_factory=list)

    @property
    def enqueued(self) -> int:
        return sum(r.enqueued for r in self.organizations)

    @property
    def duplicates(self) -> int:
        return sum(r.duplicates for r in self.organizations)

    @property
    def delivered(self) -> int:
        return sum(r.delivered for r in self.organizations)


class EventConsumer:
    """Reads the event outbox per organization and turns major events into webhook deliveries."""

    def __init__(
        self,
        name: str = "webhooks",
        *,
        settle_seconds: float = DEFAULT_SETTLE_SECONDS,
        deliver_limit: int = DEFAULT_DELIVER_LIMIT,
        deliver: bool = True,
    ) -> None:
        if not name or len(name) > 64:
            raise ValueError("consumer name must be 1..64 characters")
        self.name = name
        self.settle_seconds = max(0.0, settle_seconds)
        self.deliver_limit = deliver_limit
        self.deliver = deliver

    # -- discovery ----------------------------------------------------------------------------------
    def organization_ids(self) -> list[uuid.UUID]:
        """Organizations with active webhooks or due deliveries (owner session; ids only)."""
        with admin_session_scope() as db:
            with_hooks = select(Webhook.organization_id).where(Webhook.active.is_(True))
            with_due = select(WebhookDelivery.organization_id).where(
                WebhookDelivery.status.in_((DeliveryStatus.PENDING, DeliveryStatus.RETRYING))
            )
            return sorted(set(db.scalars(union(with_hooks, with_due)).all()))

    # -- one organization ---------------------------------------------------------------------------
    def _offset_row(self, db: Session, organization_id: uuid.UUID) -> EventConsumerOffset:
        row = db.get(EventConsumerOffset, (self.name, organization_id))
        if row is None:
            row = EventConsumerOffset(consumer=self.name, organization_id=organization_id, last_event_id=0)
            db.add(row)
            db.flush()
        return row

    def process_organization(self, organization_id: uuid.UUID, *, batch: int = DEFAULT_BATCH) -> OrgResult:
        result = OrgResult(organization_id=organization_id)
        with session_scope(organization_id) as db:
            if not try_advisory_xact_lock(db, f"event-consumer:{self.name}:{organization_id}"):
                result.locked = False
                return result
            offset = self._offset_row(db, organization_id)
            threshold = utcnow() - timedelta(seconds=self.settle_seconds)
            hooks = webhooks.active_webhooks(db, organization_id)
            new_offset = offset.last_event_id
            fully_read = True
            settled_prefix = True
            if hooks:
                oldest_hook = min(h.created_at for h in hooks)
                rows = db.scalars(
                    select(LabEvent)
                    .where(
                        LabEvent.organization_id == organization_id,
                        LabEvent.id > offset.last_event_id,
                        LabEvent.type.in_(list(WEBHOOK_EVENT_NAMES)),
                        LabEvent.created_at >= oldest_hook,
                    )
                    .order_by(LabEvent.id)
                    .limit(batch)
                ).all()
                fully_read = len(rows) < batch
                result.scanned = len(rows)
                existing = webhooks.already_enqueued(db, organization_id, (str(r.event_uuid) for r in rows))
                for event in rows:
                    if str(event.event_uuid) in existing:
                        result.duplicates += 1
                        EVENTS_CONSUMED.labels(self.name, "duplicate").inc()
                    else:
                        created = webhooks.enqueue_for_event(db, event, hooks)
                        result.enqueued += created
                        EVENTS_CONSUMED.labels(self.name, "enqueued" if created else "no_subscriber").inc()
                    if settled_prefix and event.created_at < threshold:
                        new_offset = event.id
                    else:
                        settled_prefix = False
            if fully_read and settled_prefix:
                # Everything relevant up to the settled tail has been handled: skip ahead over unrelated
                # events so later passes (and webhooks created later) never rescan history.
                tail = db.scalar(
                    select(func.max(LabEvent.id)).where(
                        LabEvent.organization_id == organization_id, LabEvent.created_at < threshold
                    )
                )
                if tail is not None:
                    new_offset = max(new_offset, int(tail))
            if new_offset > offset.last_event_id:
                offset.last_event_id = new_offset
                offset.updated_at = utcnow()
            result.offset = offset.last_event_id
        return result

    # -- a full pass --------------------------------------------------------------------------------
    def run_once(self, batch: int = DEFAULT_BATCH) -> RunResult:
        run = RunResult()
        for organization_id in self.organization_ids():
            try:
                org_result = self.process_organization(organization_id, batch=batch)
            except Exception as exc:  # one tenant's failure must not block the others
                log.exception("event_consumer_org_failed", organization_id=str(organization_id))
                org_result = OrgResult(organization_id=organization_id, error=type(exc).__name__)
            if self.deliver:
                try:
                    stats = webhooks.deliver_due(organization_id, limit=self.deliver_limit)
                    org_result.delivered = stats.succeeded
                    org_result.failed_deliveries = stats.failed
                except Exception as exc:
                    log.exception("webhook_delivery_pass_failed", organization_id=str(organization_id))
                    org_result.error = org_result.error or type(exc).__name__
            run.organizations.append(org_result)
        failed = any(r.error for r in run.organizations)
        EVENT_CONSUMER_RUNS.labels(self.name, "partial" if failed else "ok").inc()
        if not failed:
            EVENT_CONSUMER_LAST_SUCCESS.labels(self.name).set(time.time())
        return run


# --------------------------------------------------------------------------------------------------
# Process entrypoint
# --------------------------------------------------------------------------------------------------
def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


def main() -> int:
    """``python -m aegis_api.lab.events.consumer`` — loop until SIGTERM/SIGINT."""
    from prometheus_client import start_http_server

    from aegis_api.config import get_settings
    from aegis_api.lab.observability.db_metrics import install_db_metrics
    from aegis_api.lab.observability.metrics import REGISTRY, register_runtime_collectors
    from aegis_api.lab.observability.telemetry import configure_telemetry, shutdown_telemetry
    from aegis_api.logging import configure_logging

    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    configure_telemetry(service_name=f"{settings.otel_service_name}-event-consumer")
    install_db_metrics()
    register_runtime_collectors()
    # Reading the environment directly is limited to this process entrypoint (deployment knobs).
    interval = max(0.1, _env_float("EVENT_CONSUMER_INTERVAL_SECONDS", 2.0))
    settle = _env_float("EVENT_CONSUMER_SETTLE_SECONDS", DEFAULT_SETTLE_SECONDS)
    metrics_port = int(os.environ.get("METRICS_PORT", "9102"))
    start_http_server(metrics_port, registry=REGISTRY)

    stop = threading.Event()

    def _request_stop(signum: int, _frame: FrameType | None) -> None:
        log.info("event_consumer_stopping", signal=signal.Signals(signum).name)
        stop.set()

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    consumer = EventConsumer(name="webhooks", settle_seconds=settle)
    log.info("event_consumer_started", consumer=consumer.name, interval_seconds=interval, metrics_port=metrics_port)
    while not stop.is_set():
        try:
            result = consumer.run_once()
            if result.enqueued or result.delivered:
                log.info(
                    "event_consumer_pass",
                    organizations=len(result.organizations),
                    enqueued=result.enqueued,
                    duplicates=result.duplicates,
                    delivered=result.delivered,
                )
        except Exception:
            EVENT_CONSUMER_RUNS.labels(consumer.name, "error").inc()
            log.exception("event_consumer_pass_failed")
        stop.wait(interval)
    shutdown_telemetry()
    log.info("event_consumer_stopped", consumer=consumer.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
