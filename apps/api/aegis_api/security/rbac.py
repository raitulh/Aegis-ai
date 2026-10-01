"""Role-based access control. Permissions are capabilities and are always enforced server-side.

Roles are bundles of capabilities. The frontend may hide controls a role lacks, but that is UX only — every
endpoint checks the capability it needs (``deps.require``).

Roles (rank in parentheses — an actor may only grant or change roles at or below their own rank, and only
owners may create or modify owners):

* ``viewer`` (0) — read-only access to the workspace.
* ``developer`` (1) — integrates agents: ingest traces/runtime events, request runtime decisions, run audits.
* ``analyst`` (1) — runs audits and red-team campaigns, triages findings, writes policies.
* ``ai_engineer`` (1) — analyst capabilities plus runtime integration and continuous-assurance schedules.
* ``security_engineer`` (2) — owns risk: accepts risk, approves remediations and runtime approvals, publishes
  runtime policies, reveals sensitive evidence, reads the audit log.
* ``auditor`` (2) — assessment lead (historical role, kept for compatibility): analyst + risk acceptance,
  remediation approval, evidence reveal/export and audit-log access.
* ``admin`` (3) — manages members, keys, providers, integrations and webhooks.
* ``owner`` (4) — everything, including billing and deleting the workspace.
* ``service_account`` (1) — for API keys only (never assignable to a person): machine ingestion and checks.
"""

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
        "runtime:read",
        "assurance:read",
        "usage:read",
    }
)
INGEST = frozenset({"traces:write", "monitoring:ingest", "runtime:ingest", "runtime:decide"})
DEVELOPER = READ | INGEST | {"audits:run", "tests:write", "findings:comment"}
ANALYST = READ | {
    "systems:write",
    "audits:run",
    "audits:cancel",
    "policies:write",
    "policies:compile",
    "findings:write",
    "findings:comment",
    "remediations:write",
    "redteam:run",
    "traces:write",
    "monitoring:ingest",
    "reports:generate",
    "export:data",
    "tests:write",
}
AI_ENGINEER = ANALYST | INGEST | {"assurance:manage"}
RISK_OWNER = frozenset(
    {
        "findings:accept_risk",
        "remediations:approve",
        "evidence:reveal",
        "evidence:export",
        "audit_logs:read",
        "monitoring:manage",
    }
)
AUDITOR = ANALYST | RISK_OWNER
SECURITY_ENGINEER = (
    ANALYST
    | INGEST
    | RISK_OWNER
    | {
        "runtime:manage",
        "approvals:decide",
        "policies:publish",
        "assurance:manage",
    }
)
ADMIN = (
    AUDITOR
    | SECURITY_ENGINEER
    | {
        "systems:delete",
        "providers:manage",
        "integrations:manage",
        "team:manage",
        "api_keys:manage",
        "webhooks:manage",
        "org:manage",
        "evidence:delete",
        "frameworks:manage",
        "jobs:read",
    }
)
OWNER = ADMIN | {"org:delete", "billing:manage"}
SERVICE_ACCOUNT = READ | INGEST | {"audits:run", "tests:write"}

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    Role.VIEWER: frozenset(READ),
    Role.DEVELOPER: frozenset(DEVELOPER),
    Role.ANALYST: frozenset(ANALYST),
    Role.AI_ENGINEER: frozenset(AI_ENGINEER),
    Role.SECURITY_ENGINEER: frozenset(SECURITY_ENGINEER),
    Role.AUDITOR: frozenset(AUDITOR),
    Role.ADMIN: frozenset(ADMIN),
    Role.OWNER: frozenset(OWNER),
    Role.SERVICE_ACCOUNT: frozenset(SERVICE_ACCOUNT),
}

ROLE_RANK: dict[str, int] = {
    Role.VIEWER: 0,
    Role.DEVELOPER: 1,
    Role.ANALYST: 1,
    Role.AI_ENGINEER: 1,
    Role.SERVICE_ACCOUNT: 1,
    Role.SECURITY_ENGINEER: 2,
    Role.AUDITOR: 2,
    Role.ADMIN: 3,
    Role.OWNER: 4,
}

# Roles a person can hold (service accounts are for API keys only).
ASSIGNABLE_ROLES: frozenset[str] = frozenset(r.value for r in Role if r != Role.SERVICE_ACCOUNT)

ROLE_DESCRIPTIONS: dict[str, str] = {
    Role.VIEWER: "Read-only access to systems, audits, findings, evidence and runtime activity.",
    Role.DEVELOPER: "Integrate agents: send traces and runtime events, request runtime decisions, run audits.",
    Role.ANALYST: "Run audits and red-team campaigns, triage findings, author policies.",
    Role.AI_ENGINEER: "Analyst capabilities plus runtime integration and continuous-assurance schedules.",
    Role.SECURITY_ENGINEER: "Own risk: accept risk, approve remediations and runtime actions, publish runtime policies.",
    Role.AUDITOR: "Assessment lead: analyst capabilities plus risk acceptance, evidence export and the audit log.",
    Role.ADMIN: "Manage members, API keys, providers, integrations and webhooks.",
    Role.OWNER: "Full control including billing and workspace deletion.",
    Role.SERVICE_ACCOUNT: "Machine identity for API keys: ingestion, runtime checks and audit runs.",
}

# API key scopes narrow the permissions of the key's role.
SCOPE_RULES: dict[str, tuple[str, ...]] = {
    "read": (":read",),
    "write": (":write", ":compile", ":generate", ":comment", "remediations:"),
    "run": ("audits:run", "audits:cancel", "redteam:run"),
    "ingest": ("traces:write", "monitoring:ingest", "runtime:ingest"),
    "runtime": ("runtime:ingest", "runtime:decide"),
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
    """Owners may assign any role. Admins may assign roles up to admin. Nobody else manages roles."""
    if target_role not in ROLE_RANK:
        return False
    if actor_role == Role.OWNER:
        return True
    if actor_role == Role.ADMIN:
        return ROLE_RANK[target_role] <= ROLE_RANK[Role.ADMIN]
    return False


def role_catalog() -> list[dict[str, object]]:
    return [
        {
            "role": role,
            "rank": ROLE_RANK[role],
            "assignable": role in ASSIGNABLE_ROLES,
            "description": ROLE_DESCRIPTIONS[role],
            "permissions": sorted(ROLE_PERMISSIONS[role]),
        }
        for role in sorted(ROLE_PERMISSIONS, key=lambda r: (ROLE_RANK[r], r))
    ]
