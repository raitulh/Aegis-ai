"""Transactional email (password reset, email verification) over SMTP with STARTTLS.

Delivery never blocks the request or holds a database transaction: callers schedule
:func:`deliver` *after commit*, and it hands the message to a small background pool. When SMTP is not
configured the platform logs ``email_delivery_not_configured`` — the message body (which contains a
single-use token) is never logged.
"""

from __future__ import annotations

import smtplib
import ssl
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from email.message import EmailMessage as MimeMessage
from email.utils import formataddr, parseaddr

import structlog

from aegis_api.config import get_settings
from aegis_api.errors import ServiceUnavailable, ValidationFailed

log = structlog.get_logger("aegis.lab.email")

SMTP_TIMEOUT_SECONDS = 15
_executor: ThreadPoolExecutor | None = None
_lock = threading.Lock()


@dataclass(frozen=True)
class OutgoingEmail:
    to: str
    subject: str
    text: str


def email_delivery_configured() -> bool:
    return bool(get_settings().smtp_host)


def _recipient_domain(address: str) -> str:
    return address.rsplit("@", 1)[-1].lower() if "@" in address else "unknown"


def _build(message: OutgoingEmail) -> MimeMessage:
    settings = get_settings()
    for value in (message.to, message.subject):
        if "\r" in value or "\n" in value:
            raise ValidationFailed("Email headers must not contain line breaks")
    name, address = parseaddr(message.to)
    if not address or "@" not in address:
        raise ValidationFailed("Invalid recipient address")
    mime = MimeMessage()
    mime["From"] = settings.email_from
    mime["To"] = formataddr((name, address))
    mime["Subject"] = message.subject
    mime.set_content(message.text)
    return mime


def send_email(message: OutgoingEmail) -> None:
    """Send one message synchronously (SMTP + STARTTLS, or implicit TLS on port 465)."""
    settings = get_settings()
    if not settings.smtp_host:
        raise ServiceUnavailable("Email delivery is not configured", code="email_not_configured")
    mime = _build(message)
    context = ssl.create_default_context()
    smtp: smtplib.SMTP
    if settings.smtp_port == 465:
        smtp = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=SMTP_TIMEOUT_SECONDS, context=context)
    else:
        smtp = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=SMTP_TIMEOUT_SECONDS)
    with smtp:
        smtp.ehlo()
        if settings.smtp_port != 465 and settings.smtp_use_tls:
            smtp.starttls(context=context)
            smtp.ehlo()
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password or "")
        smtp.send_message(mime)


def _send_safely(message: OutgoingEmail, purpose: str) -> None:
    try:
        send_email(message)
        log.info("email_sent", purpose=purpose, recipient_domain=_recipient_domain(message.to))
    except Exception as exc:  # delivery failures must never surface the token or crash a worker
        log.warning(
            "email_delivery_failed",
            purpose=purpose,
            recipient_domain=_recipient_domain(message.to),
            error_type=type(exc).__name__,
        )


def _pool() -> ThreadPoolExecutor:
    global _executor
    with _lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="aegis-email")
    return _executor


def deliver(message: OutgoingEmail, *, purpose: str) -> None:
    """Queue a message for background delivery (or log that delivery is not configured)."""
    if not email_delivery_configured():
        log.warning("email_delivery_not_configured", purpose=purpose, recipient_domain=_recipient_domain(message.to))
        return
    _pool().submit(_send_safely, message, purpose)
