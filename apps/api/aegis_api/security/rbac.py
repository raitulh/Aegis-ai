"""Role-based access control. Permissions are always enforced server-side."""

from __future__ import annotations

from aegis_api.models.enums import Role

READ = frozenset(
    {
        "org:read",
        "team:read",
        "systems:read",
        "providers:read",
        "audits:read",
        "policies:read",
        "findings:read",
        "evidence:read",
        "traces:read",
        "monitoring:read",
        "reports:read",
        "frameworks:read",
        "integrations:read",
    }
)
ANALYST = READ | {
    "systems:write",
    "audits:run",
    "audits:cancel",
    "policies:write",
    "policies:compile",
    "findings:write",
    "remediations:write",
    "redteam:run",
    "traces:write",
    "monitoring:ingest",
    "reports:generate",
    "export:data",
    "tests:write",
}
AUDITOR = ANALYST | {
    "findings:accept_risk",
    "remediations:approve",
    "evidence:reveal",
    "audit_logs:read",
    "monitoring:manage",
}
ADMIN = AUDITOR | {
    "systems:delete",
    "providers:manage",
    "integrations:manage",
    "team:manage",
    "api_keys:manage",
    "webhooks:manage",
    "org:manage",
    "evidence:delete",
    "frameworks:manage",
}
OWNER = ADMIN | {"org:delete", "billing:manage"}

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    Role.VIEWER: frozenset(READ),
    Role.ANALYST: frozenset(ANALYST),
    Role.AUDITOR: frozenset(AUDITOR),
    Role.ADMIN: frozenset(ADMIN),
    Role.OWNER: frozenset(OWNER),
}

ROLE_RANK: dict[str, int] = {Role.VIEWER: 0, Role.ANALYST: 1, Role.AUDITOR: 2, Role.ADMIN: 3, Role.OWNER: 4}

# API key scopes narrow the permissions of the key's role.
SCOPE_RULES: dict[str, tuple[str, ...]] = {
    "read": (":read",),
    "write": (":write", ":compile", ":generate", "remediations:"),
    "run": ("audits:run", "audits:cancel", "redteam:run"),
    "ingest": ("traces:write", "monitoring:ingest"),
}
ALL_SCOPES = tuple(SCOPE_RULES)


def permissions_for_role(role: str) -> frozenset[str]:
    return ROLE_PERMISSIONS.get(role, frozenset())


def apply_scopes(permissions: frozenset[str], scopes: list[str]) -> frozenset[str]:
    if not scopes:
        return frozenset(p for p in permissions if p.endswith(":read"))
    allowed: set[str] = set()
    for perm in permissions:
        for scope in scopes:
            for rule in SCOPE_RULES.get(scope, ()):
                if (rule.startswith(":") and perm.endswith(rule)) or (
                    not rule.startswith(":") and perm.startswith(rule)
                ):
                    allowed.add(perm)
    return frozenset(allowed)


def can_assign_role(actor_role: str, target_role: str) -> bool:
    """Admins may assign up to auditor/admin; only owners may create other owners."""
    if actor_role == Role.OWNER:
        return True
    if actor_role == Role.ADMIN:
        return ROLE_RANK.get(target_role, 99) <= ROLE_RANK[Role.ADMIN]
    return False
