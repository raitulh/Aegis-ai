"""AI system and provider management."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.errors import NotFound, ValidationFailed
from aegis_api.models import AISystem, Control, Provider, SystemEvent, SystemVersion
from aegis_api.models.enums import ConnectionStatus
from aegis_api.security.context import Principal
from aegis_api.security.ssrf import validate_outbound_url
from aegis_api.services import audit_log, model_gateway, secrets_service
from aegis_api.services.auth_service import slugify
from engines.providers.base import ProviderError, ProviderUnavailable

VERSION_FIELDS = ("model_name", "model_version", "prompt_version", "system_instructions", "config")


def _unique_slug(session: Session, org_id: uuid.UUID, name: str) -> str:
    base = slugify(name, "system")
    slug, i = base, 1
    while session.scalar(select(AISystem.id).where(AISystem.organization_id == org_id, AISystem.slug == slug)):
        i += 1
        slug = f"{base}-{i}"
    return slug


def create_system(session: Session, principal: Principal, data: Any) -> AISystem:
    from aegis_api.services import entitlements

    entitlements.check_quota(session, principal.organization_id, "systems")
    provider_id = uuid.UUID(data.provider_id) if data.provider_id else None
    if provider_id:
        provider = session.get(Provider, provider_id)
        if provider is None or provider.organization_id != principal.organization_id:
            raise ValidationFailed("Provider not found")
    if data.endpoint_url:
        validate_outbound_url(data.endpoint_url)
    auth_secret_id = None
    if data.endpoint_auth_secret:
        secret = secrets_service.create_secret(
            session,
            organization_id=principal.organization_id,
            name=f"endpoint:{data.name}",
            value=data.endpoint_auth_secret,
            kind="endpoint_auth",
            created_by_id=principal.user_id,
        )
        auth_secret_id = secret.id
    system = AISystem(
        organization_id=principal.organization_id,
        name=data.name,
        slug=_unique_slug(session, principal.organization_id, data.name),
        description=data.description,
        system_type=data.system_type,
        environment=data.environment,
        owner_id=principal.user_id,
        owner_name=data.owner_name or principal.display_name,
        business_purpose=data.business_purpose,
        risk_tier=data.risk_tier,
        provider_id=provider_id,
        model_name=data.model_name,
        model_version=data.model_version,
        endpoint_url=data.endpoint_url,
        auth_secret_id=auth_secret_id,
        system_instructions=data.system_instructions,
        data_classification=data.data_classification,
        config=data.config or {},
    )
    session.add(system)
    session.flush()
    _snapshot(session, system, ["created"], "System registered", principal)
    audit_log.record(
        session,
        organization_id=principal.organization_id,
        action="system.created",
        resource_type="ai_system",
        resource_id=system.id,
        principal=principal,
        after={"name": system.name},
    )
    return system


def update_system(
    session: Session, system: AISystem, data: Any, principal: Principal
) -> tuple[AISystem, dict[str, Any]]:
    changed: list[str] = []
    payload = data.model_dump(exclude_unset=True) if hasattr(data, "model_dump") else dict(data)
    change_note = payload.pop("change_note", None)
    for field, value in payload.items():
        if field == "provider_id" and value is not None:
            value = uuid.UUID(value)
            provider = session.get(Provider, value)
            if provider is None or provider.organization_id != system.organization_id:
                raise ValidationFailed("Provider not found")
        if field == "endpoint_url" and value:
            validate_outbound_url(value)
        if getattr(system, field, None) != value and value is not None:
            setattr(system, field, value)
            changed.append(field)
    impact: dict[str, Any] = {"changed_fields": changed}
    if any(f in changed for f in VERSION_FIELDS):
        system.version = _bump_version(system.version)
        _snapshot(session, system, changed, change_note or "Configuration updated", principal)
        impact = change_impact(session, system, changed)
        session.add(
            SystemEvent(
                organization_id=system.organization_id,
                system_id=system.id,
                type="config_changed",
                title=f"System updated to {system.version}",
                description=change_note,
                data={"changed": changed},
                occurred_at=utcnow(),
            )
        )
    system.updated_at = utcnow()
    if changed:
        from aegis_api.services import assurance_service

        assurance_service.maybe_trigger_on_change(session, principal, system, changed)
    audit_log.record(
        session,
        organization_id=system.organization_id,
        action="system.updated",
        resource_type="ai_system",
        resource_id=system.id,
        principal=principal,
        after={"changed": changed, "version": system.version},
    )
    return system, impact


def change_impact(session: Session, system: AISystem, changed: list[str]) -> dict[str, Any]:
    """Impact of a configuration change, computed from real data: the system's regression tests and the
    automated controls of the policies most recently audited against it. No estimates are invented."""
    from aegis_api.models import Audit, PolicyVersion, RegressionTest

    regression_tests = int(
        session.scalar(
            select(func.count(RegressionTest.id)).where(
                RegressionTest.system_id == system.id, RegressionTest.active.is_(True)
            )
        )
        or 0
    )
    last_audit = session.scalar(
        select(Audit).where(Audit.system_id == system.id).order_by(Audit.created_at.desc()).limit(1)
    )
    policy_ids = [uuid.UUID(p) for p in (last_audit.policy_version_ids if last_audit else [])]
    automated_controls = 0
    if policy_ids:
        automated_controls = int(
            session.scalar(
                select(func.count(Control.id)).where(
                    Control.policy_version_id.in_(select(PolicyVersion.id).where(PolicyVersion.id.in_(policy_ids))),
                    Control.automation == "automated",
                )
            )
            or 0
        )
    notes = []
    if "model_name" in changed or "model_version" in changed:
        notes.append("Model changed — a full re-audit is recommended.")
    if "system_instructions" in changed or "prompt_version" in changed:
        notes.append("Prompt changed — re-run regression tests.")
    if "config" in changed:
        notes.append("Configuration (guardrails/tools) changed — re-run affected controls.")
    return {
        "changed_fields": changed,
        "affected_controls": automated_controls,
        "affected_tests": regression_tests,
        "recommended_regression_tests": regression_tests,
        "notes": notes,
    }


def _bump_version(version: str) -> str:
    try:
        major, minor = version.lstrip("v").split(".")[:2]
        return f"v{major}.{int(minor) + 1}"
    except (ValueError, IndexError):
        return "v1.1"


def _snapshot(session: Session, system: AISystem, changed: list[str], summary: str, principal: Principal) -> None:
    session.add(
        SystemVersion(
            organization_id=system.organization_id,
            system_id=system.id,
            version=system.version,
            model_name=system.model_name,
            model_version=system.model_version,
            prompt_version=system.prompt_version,
            snapshot={
                "config": system.config,
                "system_instructions": system.system_instructions,
                "model_name": system.model_name,
            },
            changed_fields=changed,
            change_summary=summary,
            created_by_id=principal.user_id,
        )
    )


def delete_system(session: Session, system: AISystem, principal: Principal) -> None:
    system.deleted_at = utcnow()
    system.status = "archived"
    audit_log.record(
        session,
        organization_id=system.organization_id,
        action="system.deleted",
        resource_type="ai_system",
        resource_id=system.id,
        principal=principal,
    )


def get_system(session: Session, system_id: uuid.UUID, organization_id: uuid.UUID) -> AISystem:
    system = session.get(AISystem, system_id)
    if system is None or system.organization_id != organization_id or system.deleted_at is not None:
        raise NotFound("AI system not found")
    return system


# --- providers ---------------------------------------------------------------------------------


def create_provider(session: Session, principal: Principal, data: Any) -> Provider:
    if data.base_url:
        # Private targets (e.g. a self-hosted Ollama) follow ALLOW_PRIVATE_NETWORK_TARGETS.
        validate_outbound_url(data.base_url)
    secret_id = None
    if data.api_key:
        secret = secrets_service.create_secret(
            session,
            organization_id=principal.organization_id,
            name=f"provider:{data.kind}:{data.name}",
            value=data.api_key,
            kind="provider_key",
            created_by_id=principal.user_id,
        )
        secret_id = secret.id
    provider = Provider(
        organization_id=principal.organization_id,
        kind=data.kind,
        name=data.name,
        base_url=data.base_url,
        default_model=data.default_model,
        secret_id=secret_id,
        allow_data_processing=data.allow_data_processing,
        settings=data.settings or {},
        status=ConnectionStatus.UNKNOWN,
    )
    session.add(provider)
    session.flush()
    audit_log.record(
        session,
        organization_id=principal.organization_id,
        action="provider.created",
        resource_type="provider",
        resource_id=provider.id,
        principal=principal,
        after={"kind": provider.kind},
    )
    return provider


def test_provider(session: Session, provider: Provider) -> dict[str, Any]:
    try:
        instance = model_gateway.build_provider(session, provider)
        health = instance.health_check()
        meta = instance.metadata()
        provider.status = ConnectionStatus.CONNECTED if health.ok else ConnectionStatus.ERROR
        provider.last_error = None if health.ok else health.detail
        provider.last_checked_at = utcnow()
        return {
            "ok": health.ok,
            "status": provider.status,
            "detail": health.detail,
            "models": health.models[:50],
            "latency_ms": health.latency_ms,
            "data_leaves_organization": meta.data_leaves_organization,
        }
    except (ProviderUnavailable, ProviderError) as exc:
        provider.status = ConnectionStatus.ERROR
        provider.last_error = str(exc)
        provider.last_checked_at = utcnow()
        return {
            "ok": False,
            "status": ConnectionStatus.ERROR,
            "detail": str(exc),
            "models": [],
            "latency_ms": None,
            "data_leaves_organization": False,
        }
