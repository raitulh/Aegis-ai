"""Role-based access control. Permissions are always enforced server-side.

Two permission families coexist:
* the assurance domain (``systems:read``, ``audits:run`` ...), and
* the AI Scientist Evolution Lab (``mission:create``, ``experiment:execute``, ``strategy:promote`` ...).

System roles are defined in code (immutable). Organizations may define *custom roles* whose permissions are a
subset of this catalogue; a custom role can never include a permission its creator does not hold.
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
ADMIN_ASSURANCE = AUDITOR | {
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

# ---------------------------------------------------------------------------------------------
# Scientist lab permission catalogue: key → (category, description)
# ---------------------------------------------------------------------------------------------
LAB_PERMISSIONS: dict[str, tuple[str, str]] = {
    "workspace:read": ("workspace", "View workspaces"),
    "workspace:manage": ("workspace", "Create/update workspaces and their members"),
    "project:read": ("project", "View projects"),
    "project:create": ("project", "Create projects"),
    "project:manage": ("project", "Update projects, members and budgets"),
    "project:access_all": ("project", "Access private projects without membership"),
    "mission:read": ("mission", "View missions, events and observability"),
    "mission:create": ("mission", "Create and edit missions"),
    "mission:run": ("mission", "Launch, pause and resume missions"),
    "mission:cancel": ("mission", "Cancel missions"),
    "mission:approve_plan": ("mission", "Approve mission plans"),
    "agent:read": ("agent", "View agents and agent runs"),
    "agent:manage": ("agent", "Create and version agent definitions"),
    "agent:run": ("agent", "Run agents"),
    "research:read": ("research", "View research tasks and sources"),
    "research:run": ("research", "Start research tasks (incl. Deep Research)"),
    "hypothesis:read": ("hypothesis", "View hypotheses"),
    "hypothesis:write": ("hypothesis", "Create and update hypotheses"),
    "experiment:read": ("experiment", "View experiments and runs"),
    "experiment:create": ("experiment", "Design and version experiments"),
    "experiment:execute": ("experiment", "Execute experiments in the sandbox"),
    "dataset:read": ("dataset", "View datasets"),
    "dataset:write": ("dataset", "Create dataset versions"),
    "artifact:read": ("artifact", "View artifact metadata"),
    "artifact:upload": ("artifact", "Upload artifacts/documents"),
    "artifact:download": ("artifact", "Download artifact content"),
    "knowledge:read": ("knowledge", "Search papers, documents and the knowledge graph"),
    "knowledge:ingest": ("knowledge", "Ingest documents and URLs"),
    "memory:read": ("memory", "Search scientific memory"),
    "memory:write": ("memory", "Propose memories"),
    "memory:review": ("memory", "Approve/reject memory promotions"),
    "failure:read": ("failure", "View failures and lessons"),
    "strategy:read": ("strategy", "View strategies"),
    "strategy:write": ("strategy", "Create strategies and versions"),
    "strategy:evolve": ("strategy", "Run evolution"),
    "strategy:promote": ("strategy", "Promote strategy versions"),
    "strategy:rollback": ("strategy", "Roll back promoted strategies"),
    "evaluation:read": ("evaluation", "View evaluations"),
    "evaluation:run": ("evaluation", "Run evaluations"),
    "verification:read": ("verification", "View claims and verifications"),
    "verification:run": ("verification", "Run verification"),
    "discovery:read": ("discovery", "View discoveries"),
    "discovery:approve": ("discovery", "Approve discoveries (human review)"),
    "discovery:publish": ("discovery", "Publish approved discoveries"),
    "approval:read": ("approval", "View approval requests"),
    "approval:decide": ("approval", "Approve or reject requests"),
    "tool:read": ("tool", "View tools and tool calls"),
    "tool:manage": ("tool", "Configure tools"),
    "mcp:read": ("mcp", "View MCP servers"),
    "mcp:manage": ("mcp", "Register and approve MCP servers/tools"),
    "model:read": ("model", "View the model catalogue"),
    "model:manage": ("model", "Configure model catalogue overrides"),
    "environment:manage": ("execution", "Register execution environments"),
    "policy:read": ("policy", "View lab policies"),
    "admin:policy": ("policy", "Create and version lab policies"),
    "usage:read": ("usage", "View usage and cost"),
    "billing:view": ("billing", "View billing"),
    "billing:manage": ("billing", "Manage billing"),
    "benchmark:read": ("benchmark", "View benchmark runs"),
    "benchmark:run": ("benchmark", "Run benchmarks"),
    "report:read": ("report", "View research reports"),
    "report:generate": ("report", "Generate research reports"),
    "service_account:manage": ("identity", "Manage service accounts"),
    "sso:manage": ("identity", "Configure enterprise SSO"),
    "audit:read": ("audit", "Read the audit log"),
    "webhook:manage": ("integration", "Manage outbound webhooks"),
}

LAB_READ = frozenset(k for k in LAB_PERMISSIONS if k.endswith(":read") and not k.startswith(("audit:", "usage:")))
REVIEWER = LAB_READ | {
    "approval:decide",
    "discovery:approve",
    "verification:run",
    "memory:review",
    "artifact:download",
    "evaluation:run",
}
RESEARCHER = LAB_READ | {
    "mission:create",
    "mission:run",
    "mission:cancel",
    "agent:run",
    "research:run",
    "hypothesis:write",
    "experiment:create",
    "experiment:execute",
    "dataset:write",
    "artifact:upload",
    "artifact:download",
    "knowledge:ingest",
    "memory:write",
    "strategy:write",
    "strategy:evolve",
    "evaluation:run",
    "verification:run",
    "report:generate",
    "benchmark:run",
    "usage:read",
}
SCIENTIST_OPERATOR = RESEARCHER | {
    "agent:manage",
    "model:manage",
    "environment:manage",
    "strategy:rollback",
    "tool:manage",
}
RESEARCH_LEAD = RESEARCHER | {
    "project:create",
    "project:manage",
    "mission:approve_plan",
    "approval:decide",
    "strategy:promote",
    "strategy:rollback",
    "discovery:approve",
    "memory:review",
    "agent:manage",
    "workspace:manage",
}
BILLING_ADMIN = frozenset(
    {"org:read", "team:read", "usage:read", "billing:view", "billing:manage", "project:read", "workspace:read"}
)
LAB_ADMIN = frozenset(LAB_PERMISSIONS)

ADMIN = ADMIN_ASSURANCE | LAB_ADMIN
OWNER = ADMIN | {"org:delete"}

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    Role.VIEWER: frozenset(READ | LAB_READ),
    Role.ANALYST: frozenset(ANALYST | LAB_READ),
    Role.AUDITOR: frozenset(AUDITOR | LAB_READ | {"audit:read"}),
    Role.ADMIN: frozenset(ADMIN),
    Role.OWNER: frozenset(OWNER),
    Role.REVIEWER: frozenset({"org:read", "team:read"} | REVIEWER),
    Role.RESEARCHER: frozenset({"org:read", "team:read"} | RESEARCHER),
    Role.SCIENTIST_OPERATOR: frozenset({"org:read", "team:read"} | SCIENTIST_OPERATOR),
    Role.RESEARCH_LEAD: frozenset({"org:read", "team:read"} | RESEARCH_LEAD),
    Role.BILLING_ADMIN: BILLING_ADMIN,
}

ROLE_RANK: dict[str, int] = {
    Role.VIEWER: 0,
    Role.BILLING_ADMIN: 0,
    Role.ANALYST: 1,
    Role.REVIEWER: 1,
    Role.RESEARCHER: 1,
    Role.AUDITOR: 2,
    Role.SCIENTIST_OPERATOR: 2,
    Role.RESEARCH_LEAD: 2,
    Role.ADMIN: 3,
    Role.OWNER: 4,
}
SYSTEM_ROLES = frozenset(ROLE_PERMISSIONS)

# API key scopes narrow the permissions of the key's role (never widen them).
SCOPE_RULES: dict[str, tuple[str, ...]] = {
    "read": (":read", ":view"),
    "write": (":write", ":compile", ":generate", "remediations:", ":create", ":update", ":upload", ":ingest"),
    "run": (
        "audits:run",
        "audits:cancel",
        "redteam:run",
        "mission:run",
        "mission:cancel",
        "research:run",
        "experiment:execute",
        "agent:run",
        "evaluation:run",
        "verification:run",
        "strategy:evolve",
        "benchmark:run",
    ),
    "ingest": ("traces:write", "monitoring:ingest", "knowledge:ingest"),
    "download": ("artifact:download",),
}
ALL_SCOPES = tuple(SCOPE_RULES)

# Permissions that only interactive humans may exercise (never API keys / service accounts / agents).
HUMAN_ONLY_PERMISSIONS = frozenset(
    {
        "approval:decide",
        "discovery:approve",
        "discovery:publish",
        "memory:review",
        "mission:approve_plan",
        "strategy:promote",
    }
)


def permissions_for_role(role: str) -> frozenset[str]:
    return ROLE_PERMISSIONS.get(role, frozenset())


def is_system_role(role: str) -> bool:
    return role in SYSTEM_ROLES


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
    return frozenset(allowed - HUMAN_ONLY_PERMISSIONS)


def can_assign_role(
    actor_role: str,
    target_role: str,
    *,
    target_permissions: frozenset[str] | None = None,
    actor_permissions: frozenset[str] | None = None,
) -> bool:
    """Owners may assign anything; admins up to admin. Custom roles are assignable only when every permission
    they carry is already held by the assigning actor (no privilege escalation)."""
    if target_role in SYSTEM_ROLES:
        if actor_role == Role.OWNER:
            return True
        if actor_role == Role.ADMIN:
            return ROLE_RANK.get(target_role, 99) <= ROLE_RANK[Role.ADMIN]
        return False
    if target_permissions is None or actor_permissions is None:
        return False
    return actor_role in (Role.OWNER, Role.ADMIN) and target_permissions <= actor_permissions


def validate_custom_permissions(requested: set[str], actor_permissions: frozenset[str]) -> list[str]:
    """Return problems with a custom role's permission set (unknown keys or escalation)."""
    known = set(LAB_PERMISSIONS) | ADMIN_ASSURANCE | {"org:delete"}
    problems = [f"unknown permission '{p}'" for p in sorted(requested - known)]
    problems += [f"cannot grant '{p}' (you do not hold it)" for p in sorted((requested & known) - actor_permissions)]
    return problems
