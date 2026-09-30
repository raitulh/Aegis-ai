"""Outbound email with pluggable backends.

* ``smtp``   — real delivery (STARTTLS, explicit timeout).
* ``outbox`` — development: messages are written as ``.eml`` files under ``var/outbox`` (never logged, so
  secrets such as reset links do not end up in log aggregation).
* ``memory`` — tests: messages are kept in-process for assertions.
"""

from __future__ import annotations

import smtplib
import ssl
import threading
import uuid
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path

import structlog

from aegis_api.config import get_settings

log = structlog.get_logger("aegis.email")
_MEMORY: list[EmailMessage] = []
_LOCK = threading.Lock()


@dataclass(frozen=True)
class Email:
    to: str
    subject: str
    text: str


def _build(email: Email) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = get_settings().email_from
    msg["To"] = email.to
    msg["Subject"] = email.subject
    msg["Message-ID"] = f"<{uuid.uuid4().hex}@aegis.local>"
    msg.set_content(email.text)
    return msg


def send(email: Email) -> None:
    settings = get_settings()
    backend = settings.effective_email_backend
    msg = _build(email)
    if backend == "smtp":
        context = ssl.create_default_context()
        with smtplib.SMTP(settings.smtp_host or "", settings.smtp_port, timeout=15) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls(context=context)
            if settings.smtp_username and settings.smtp_password:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(msg)
        log.info("email_sent", backend="smtp", subject=email.subject)
        return
    if backend == "memory":
        with _LOCK:
            _MEMORY.append(msg)
        return
    outbox = Path("var/outbox")
    outbox.mkdir(parents=True, exist_ok=True)
    path = outbox / f"{msg['Message-ID'].strip('<>').split('@')[0]}.eml"
    path.write_bytes(bytes(msg))
    log.info("email_written_to_outbox", subject=email.subject, file=str(path))


def sent_messages() -> list[EmailMessage]:
    with _LOCK:
        return list(_MEMORY)


def clear() -> None:
    with _LOCK:
        _MEMORY.clear()
