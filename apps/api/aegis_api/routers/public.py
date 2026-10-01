"""Public website endpoints: contact / sales requests and the factual Trust Center data."""

from __future__ import annotations

import hashlib
from typing import Any

from fastapi import APIRouter, Depends, Request

from aegis_api.config import get_settings
from aegis_api.db.session import admin_session_scope
from aegis_api.models import ContactRequest
from aegis_api.ratelimit import client_ip, guest_rate_limit, public_rate_limit
from aegis_api.schemas.common import Message
from aegis_api.schemas.platform import ContactCreate

router = APIRouter(prefix="/api/v1/public", tags=["Public"], dependencies=[Depends(public_rate_limit)])


@router.post("/contact", response_model=Message, status_code=201, dependencies=[Depends(guest_rate_limit)])
def contact(body: ContactCreate, request: Request) -> Message:
    """Talk to sales, book a demo, request a security review or a private deployment."""
    from aegis_api.services import email_service

    with admin_session_scope() as session:
        session.add(
            ContactRequest(
                kind=body.kind,
                name=body.name,
                email=str(body.email).lower(),
                company=body.company,
                message=body.message,
                source_ip_hash=hashlib.sha256(client_ip(request).encode()).hexdigest(),
            )
        )
    inbox = get_settings().sales_inbox
    if inbox:
        email_service.send(
            to=inbox,
            subject=f"[Aegis] New {body.kind.replace('_', ' ')} request from {body.company or body.name}",
            body=f"{body.name} <{body.email}>\nCompany: {body.company or '-'}\n\n{body.message or ''}",
        )
    return Message(message="Thanks — we have your request and will reply by email.")


@router.get("/trust")
def trust() -> dict[str, Any]:
    """Facts the platform can state about itself, derived from configuration — no certifications are
    claimed. Compliance items are listed as roadmap until independently attested."""
    from aegis_api.security import signing

    settings = get_settings()
    return {
        "controls": [
            {
                "area": "Tenant isolation",
                "status": "implemented",
                "detail": "PostgreSQL Row Level Security behind application-level scoping; cross-tenant tests in CI.",
            },
            {
                "area": "Evidence integrity",
                "status": "implemented",
                "detail": "SHA-256 hash chains, append-only database triggers, Ed25519-signed export packages with a standalone verifier.",
            },
            {
                "area": "Secrets at rest",
                "status": "implemented",
                "detail": "Provider keys and webhook secrets encrypted with Fernet (AES-128-CBC + HMAC-SHA256); API keys stored as peppered HMAC hashes.",
            },
            {
                "area": "Outbound requests",
                "status": "implemented",
                "detail": "Connect-time SSRF guard with IP pinning; private networks blocked in production.",
            },
            {
                "area": "Audit logging",
                "status": "implemented",
                "detail": "Append-only organization audit log for security-relevant actions.",
            },
            {
                "area": "Transport security",
                "status": "deployment",
                "detail": "TLS is terminated by your reverse proxy; HSTS is sent in production.",
            },
        ],
        "compliance_roadmap": [
            {
                "framework": "SOC 2 Type II",
                "status": "not attested",
                "note": "Designed for; no audit has been performed.",
            },
            {"framework": "ISO/IEC 27001", "status": "not certified", "note": "Roadmap."},
            {
                "framework": "ISO/IEC 42001",
                "status": "not certified",
                "note": "Aegis maps your controls to it; Aegis itself is not certified.",
            },
        ],
        "evidence_signing_key_id": signing.key_id(),
        "data_handling": {
            "model_providers": "Data is sent to an external model provider only for providers your workspace explicitly configures and consents to.",
            "redaction": "Runtime payloads and evidence are stored redacted; raw sensitive values are encrypted and revealed only with a permission (audit-logged).",
            "retention": f"Runtime events default to {settings.retention_runtime_events_days} days (plan-dependent); evidence is never deleted by retention.",
        },
        "subprocessors": "Deployment-specific. Self-hosted deployments use none beyond the model providers you configure.",
    }
