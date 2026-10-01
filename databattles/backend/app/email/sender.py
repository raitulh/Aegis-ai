"""Email delivery adapters and the outbox-based sending pipeline."""

from __future__ import annotations

import logging
import smtplib
import ssl
import uuid
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Any, Protocol

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.ids import uuid7
from app.core.time import utcnow
from app.email.templates import render
from app.jobs.queue import TransientJobError, enqueue, job_handler
from app.models.community import EmailOutbox

logger = logging.getLogger("databattles.email")


class EmailSender(Protocol):
    def send(self, to: str, subject: str, text: str, html: str) -> str: ...


class ConsoleEmailSender:
    """Development sender: logs a one-line summary; bodies remain in the dev mailbox (outbox)."""

    def send(self, to: str, subject: str, text: str, html: str) -> str:
        message_id = f"console-{uuid.uuid4().hex[:12]}"
        masked = to[:2] + "***@" + to.split("@")[-1] if "@" in to else "***"
        logger.info("email_sent", extra={"to": masked, "subject": subject, "message_id": message_id})
        return message_id


class SmtpEmailSender:
    def send(self, to: str, subject: str, text: str, html: str) -> str:
        msg = EmailMessage()
        msg["From"] = settings.EMAIL_FROM
        msg["To"] = to
        msg["Subject"] = subject
        msg["Message-ID"] = make_msgid(domain=settings.EMAIL_FROM.split("@")[-1].strip(">"))
        msg.set_content(text)
        msg.add_alternative(html, subtype="html")
        try:
            with smtplib.SMTP(settings.SMTP_HOST or "localhost", settings.SMTP_PORT, timeout=20) as smtp:
                if settings.SMTP_STARTTLS:
                    smtp.starttls(context=ssl.create_default_context())
                if settings.SMTP_USERNAME:
                    smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD or "")
                smtp.send_message(msg)
        except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, TimeoutError, OSError) as exc:
            raise TransientJobError(f"smtp transient failure: {type(exc).__name__}") from exc
        return str(msg["Message-ID"])


def get_sender() -> EmailSender:
    return SmtpEmailSender() if settings.EMAIL_BACKEND == "smtp" else ConsoleEmailSender()


def queue_email(db: Session, to: str, template: str, context: dict[str, Any], *, dedupe_key: str | None = None,
                unsubscribe_url: str | None = None) -> None:
    """Render now, persist to the outbox, send in the background. Duplicate dedupe keys are ignored."""
    tpl, subject, text, html = render(template, context, unsubscribe_url=unsubscribe_url)
    outbox_id = uuid7()
    stmt = insert(EmailOutbox).values(
        id=outbox_id, to_email=to, template=tpl.name, template_version=tpl.version, subject=subject,
        text_body=text, html_body=html, status="queued", dedupe_key=dedupe_key,
    )
    if dedupe_key:
        stmt = stmt.on_conflict_do_nothing(index_elements=["dedupe_key"])
    result = db.execute(stmt.returning(EmailOutbox.id))
    if result.first() is not None:
        enqueue(db, "send_email", {"outbox_id": str(outbox_id)}, idempotency_key=f"email:{outbox_id}", max_attempts=5)


@job_handler("send_email")
def _send_email_job(db: Session, payload: dict[str, Any]) -> None:
    row = db.get(EmailOutbox, uuid.UUID(payload["outbox_id"]))
    if row is None or row.status == "sent":
        return
    row.attempts += 1
    try:
        message_id = get_sender().send(row.to_email, row.subject, row.text_body or "", row.html_body or "")
    except TransientJobError:
        row.status = "retrying"
        db.commit()
        raise
    except Exception as exc:  # permanent provider rejection
        row.status = "failed"
        row.error = f"{type(exc).__name__}"[:500]
        db.commit()
        logger.error("email_failed", extra={"outbox_id": str(row.id), "error": type(exc).__name__})
        return
    row.status = "sent"
    row.provider_message_id = message_id
    row.sent_at = utcnow()
    if not settings.EMAIL_STORE_BODIES:
        row.text_body = None
        row.html_body = None
