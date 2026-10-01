"""Transactional email over SMTP (optional). Returns False — never pretends — when not configured."""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

import structlog

from aegis_api.config import get_settings

log = structlog.get_logger("aegis.email")


def configured() -> bool:
    return bool(get_settings().smtp_host)


def send(*, to: str, subject: str, body: str) -> bool:
    settings = get_settings()
    if not settings.smtp_host:
        return False
    message = EmailMessage()
    message["From"] = settings.email_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls(context=ssl.create_default_context())
            if settings.smtp_username and settings.smtp_password:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)
        return True
    except (OSError, smtplib.SMTPException):
        log.warning("email_send_failed", exc_info=True)
        return False
