"""Event consumer: delivers signed outbound webhooks for major lab events (with retry/backoff).

Deliveries are rows in ``webhook_deliveries`` written in the same transaction as the event, so nothing is lost
if this process is down; it drains them when it comes back. A distributed lock keeps one active deliverer.

    python -m aegis_api.processes.event_consumer
"""

from __future__ import annotations

import structlog

from aegis_api.db.session import session_scope
from aegis_api.infrastructure.locks import distributed_lock
from aegis_api.processes.common import organization_ids, setup
from aegis_api.services import webhook_service

log = structlog.get_logger("aegis.event_consumer")
POLL_SECONDS = 5.0


def deliver_webhooks_once(limit_per_org: int = 50) -> int:
    delivered = 0
    with distributed_lock("webhook-delivery", ttl_seconds=120) as acquired:
        if not acquired:
            return 0
        for org_id in organization_ids():
            try:
                with session_scope(org_id) as db:
                    delivered += webhook_service.deliver_pending(db, limit=limit_per_org)
            except Exception:
                log.exception("webhook_delivery_failed", organization_id=str(org_id))
    return delivered


def run_forever() -> None:
    stopper = setup("event-consumer")
    while not stopper.stopped:
        try:
            count = deliver_webhooks_once()
            if count:
                log.info("webhooks_delivered", count=count)
        except Exception:
            log.exception("event_consumer_iteration_failed")
        stopper.wait(POLL_SECONDS)


if __name__ == "__main__":
    run_forever()
