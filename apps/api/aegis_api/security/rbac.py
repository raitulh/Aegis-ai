"""Role-based access control. Permissions are always enforced server-side.

Two permission families share one model:

* the Aegis assurance permissions (``systems:read``, ``audits:run`` …), and
* the AI Scientist Evolution Lab permissions (``mission:create``, ``experiment:execute`` …).

Organization roles grant permissions organization-wide; project roles (``project_members``) can grant
additional permissions inside one project (see ``aegis_api.lab.core.access``). API keys and service
accounts are narrowed further by scopes. Approval decisions are never grantable to non-human
credentials (enforced in the approvals service, not only here).
"""

from __future__ import annotations

from aegis_api.models.enums import Role

# ---------------------------------------------------------------------------------------------
# Aegis assurance permissions
# ---------------------------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------------------------
# AI Scientist Evolution Lab permissions (catalog: permission → (category, description))
# ---------------------------------------------------------------------------------------------
LAB_PERMISSION_CATALOG: dict[str, tuple[str, str]] = {
    # hierarchy
    "workspace:read": ("organization", "View workspaces"),
    "workspace:create": ("organization", "Create workspaces"),
    "workspace:manage": ("organization", "Update/archive workspaces and their members"),
    "project:read": ("organization", "View projects"),
    "project:create": ("organization", "Create projects"),
    "project:manage": ("organization", "Update/archive projects and their members"),
    "team:manage": ("organization", "Manage teams"),
    "service_account:manage": ("organization", "Manage service accounts"),
    # missions & agents
    "mission:read": ("missions", "View missions, status and events"),
    "mission:create": ("missions", "Create missions"),
    "mission:update": ("missions", "Edit mission definitions (draft/planned)"),
    "mission:run": ("missions", "Plan, start, pause and resume missions"),
    "mission:cancel": ("missions", "Cancel missions"),
    "mission:approve_plan": ("missions", "Approve mission plans"),
    "agent:read": ("agents", "View agents and agent runs"),
    "agent:run": ("agents", "Start agent runs"),
    "agent:manage": ("agents", "Create/configure agents and prompt templates"),
    # research & knowledge
    "research:read": ("research", "View research tasks, sources and papers"),
    "research:run": ("research", "Launch literature and deep research"),
    "memory:read": ("knowledge", "Search and read scientific memory"),
    "memory:write": ("knowledge", "Write memory items and ingest documents"),
    "memory:review": ("knowledge", "Review/approve proposed memories"),
    # hypotheses & experiments
    "hypothesis:read": ("science", "View hypotheses"),
    "hypothesis:create": ("science", "Create hypotheses"),
    "hypothesis:update": ("science", "Critique, select and transition hypotheses"),
    "experiment:read": ("science", "View experiments, runs and metrics"),
    "experiment:create": ("science", "Design experiments and create versions"),
    "experiment:execute": ("science", "Execute experiments in the sandbox"),
    "experiment:cancel": ("science", "Cancel experiment runs"),
    "dataset:read": ("data", "View datasets"),
    "dataset:create": ("data", "Upload datasets and versions"),
    "artifact:read": ("data", "View artifact metadata"),
    "artifact:upload": ("data", "Upload artifacts"),
    "artifact:download": ("data", "Download artifact contents"),
    "execution:read": ("execution", "View compute jobs"),
    "execution:manage": ("execution", "Cancel/retry compute jobs"),
    # evaluation, failures, strategies
    "evaluation:read": ("evaluation", "View evaluation runs and evaluators"),
    "evaluation:run": ("evaluation", "Run evaluators"),
    "failure:read": ("evaluation", "View failures and lessons"),
    "failure:write": ("evaluation", "Record failures, lessons and recovery actions"),
    "strategy:read": ("evolution", "View strategies and evolution runs"),
    "strategy:create": ("evolution", "Create strategies and versions"),
    "strategy:evolve": ("evolution", "Run evolution and benchmarks"),
    "strategy:promote": ("evolution", "Promote strategy versions"),
    "strategy:rollback": ("evolution", "Roll back promoted strategies"),
    "benchmark:read": ("evolution", "View benchmark runs"),
    "benchmark:run": ("evolution", "Run internal benchmarks"),
    # verification & discoveries
    "claim:read": ("verification", "View claims and lineage"),
    "claim:create": ("verification", "Create/extract claims and link evidence"),
    "verification:read": ("verification", "View verifications and reproductions"),
    "verification:run": ("verification", "Run verification and reproduction"),
    "discovery:read": ("verification", "View discoveries"),
    "discovery:create": ("verification", "Register discovery candidates"),
    "discovery:approve": ("verification", "Approve discoveries (human reviewers only)"),
    "discovery:publish": ("verification", "Publish approved discoveries"),
    "report:read": ("verification", "View mission reports"),
    "report:generate": ("verification", "Generate mission reports"),
    # governance
    "approval:read": ("governance", "View approval requests"),
    "approval:decide": ("governance", "Approve or reject requests (human reviewers only)"),
    "policy:read": ("governance", "View governance policies"),
    "admin:policy": ("governance", "Create/modify governance policies"),
    "audit:read": ("governance", "Read the audit log"),
    # tools, MCP, models
    "tool:read": ("tools", "View tools and tool invocations"),
    "tool:invoke": ("tools", "Invoke tools through the broker"),
    "mcp:read": ("tools", "View MCP servers and tools"),
    "mcp:manage": ("tools", "Register/approve MCP servers and tools"),
    "model:read": ("models", "View model configuration"),
    "model:manage": ("models", "Configure model routing and prices"),
    # usage & billing & events
    "usage:read": ("billing", "View usage and costs"),
    "billing:view": ("billing", "View subscription and invoices"),
    "billing:manage": ("billing", "Manage subscription"),
    "event:read": ("platform", "Read and stream events"),
    "webhook:manage": ("platform", "Manage outbound webhooks"),
}
ALL_LAB_PERMISSIONS = frozenset(LAB_PERMISSION_CATALOG)

LAB_VIEWER = frozenset(p for p in ALL_LAB_PERMISSIONS if p.endswith(":read") and p not in {"audit:read"})
LAB_RESEARCHER = LAB_VIEWER | {
    "mission:create",
    "mission:update",
    "mission:run",
    "agent:run",
    "research:run",
    "memory:write",
    "hypothesis:create",
    "hypothesis:update",
    "experiment:create",
    "experiment:execute",
    "experiment:cancel",
    "dataset:create",
    "artifact:upload",
    "artifact:download",
    "evaluation:run",
    "failure:write",
    "strategy:create",
    "benchmark:run",
    "claim:create",
    "verification:run",
    "discovery:create",
    "report:generate",
    "tool:invoke",
}
LAB_SCIENTIST_OPERATOR = LAB_RESEARCHER | {
    "mission:cancel",
    "agent:manage",
    "execution:manage",
    "strategy:evolve",
}
LAB_RESEARCH_LEAD = LAB_SCIENTIST_OPERATOR | {
    "mission:approve_plan",
    "approval:decide",
    "strategy:promote",
    "strategy:rollback",
    "memory:review",
    "workspace:create",
    "project:create",
    "project:manage",
    "team:manage",
    "audit:read",
}
LAB_REVIEWER = LAB_VIEWER | {
    "approval:decide",
    "discovery:approve",
    "discovery:publish",
    "verification:run",
    "memory:review",
    "artifact:download",
    "audit:read",
}
LAB_BILLING = frozenset({"usage:read", "billing:view", "billing:manage", "project:read", "workspace:read"})
LAB_ADMIN = ALL_LAB_PERMISSIONS - {"billing:manage"}

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    Role.VIEWER: frozenset(READ | LAB_VIEWER),
    Role.ANALYST: frozenset(ANALYST | LAB_RESEARCHER),
    Role.AUDITOR: frozenset(AUDITOR | LAB_RESEARCHER | LAB_REVIEWER),
    Role.ADMIN: frozenset(ADMIN | LAB_ADMIN),
    Role.OWNER: frozenset(OWNER | ALL_LAB_PERMISSIONS),
    Role.RESEARCH_LEAD: frozenset(READ | LAB_RESEARCH_LEAD),
    Role.RESEARCHER: frozenset(READ | LAB_RESEARCHER),
    Role.SCIENTIST_OPERATOR: frozenset(READ | LAB_SCIENTIST_OPERATOR),
    Role.REVIEWER: frozenset(READ | LAB_REVIEWER),
    Role.BILLING_ADMIN: frozenset({"org:read"} | LAB_BILLING),
}

ROLE_RANK: dict[str, int] = {
    Role.VIEWER: 0,
    Role.BILLING_ADMIN: 0,
    Role.REVIEWER: 1,
    Role.ANALYST: 1,
    Role.RESEARCHER: 1,
    Role.SCIENTIST_OPERATOR: 2,
    Role.AUDITOR: 2,
    Role.RESEARCH_LEAD: 2,
    Role.ADMIN: 3,
    Role.OWNER: 4,
}

ROLE_DESCRIPTIONS: dict[str, str] = {
    Role.OWNER: "Full control of the organization, including billing and deletion.",
    Role.ADMIN: "Administers the organization: members, policies, integrations, MCP and models.",
    Role.AUDITOR: "Aegis assurance auditor; can review and approve lab work.",
    Role.ANALYST: "Aegis assurance analyst; researcher-level lab access.",
    Role.VIEWER: "Read-only access.",
    Role.RESEARCH_LEAD: "Leads research: approves plans, decides approvals, promotes strategies.",
    Role.RESEARCHER: "Creates missions, hypotheses and experiments; runs research and experiments.",
    Role.SCIENTIST_OPERATOR: "Operates the autonomous machinery: agents, evolution, compute jobs.",
    Role.REVIEWER: "Independent scientific reviewer: approves discoveries and decides approvals.",
    Role.BILLING_ADMIN: "Views usage and manages the subscription.",
}

# API key scopes narrow the permissions of the key's role.
SCOPE_RULES: dict[str, tuple[str, ...]] = {
    "read": (":read", "artifact:download", "billing:view"),
    "write": (
        ":write",
        ":compile",
        ":generate",
        "remediations:",
        ":create",
        ":update",
        ":upload",
    ),
    "run": (
        "audits:run",
        "audits:cancel",
        "redteam:run",
        "mission:run",
        "mission:cancel",
        "research:run",
        "experiment:execute",
        "experiment:cancel",
        "agent:run",
        "tool:invoke",
        "evaluation:run",
        "verification:run",
        "benchmark:run",
        "strategy:evolve",
    ),
    "ingest": ("traces:write", "monitoring:ingest", "memory:write"),
}
ALL_SCOPES = tuple(SCOPE_RULES)
# Never grantable to non-human credentials regardless of role or scopes.
HUMAN_ONLY_PERMISSIONS = frozenset(
    {
        "approval:decide",
        "discovery:approve",
        "discovery:publish",
        "strategy:promote",
        "mission:approve_plan",
        "memory:review",
        "admin:policy",
        "mcp:manage",
        "team:manage",
        "api_keys:manage",
        "service_account:manage",
        "billing:manage",
        "org:delete",
    }
)


def permissions_for_role(role: str) -> frozenset[str]:
    return ROLE_PERMISSIONS.get(role, frozenset())


def apply_scopes(permissions: frozenset[str], scopes: list[str]) -> frozenset[str]:
    if not scopes:
        return frozenset(p for p in permissions if p.endswith(":read") and p not in HUMAN_ONLY_PERMISSIONS)
    allowed: set[str] = set()
    for perm in permissions:
        if perm in HUMAN_ONLY_PERMISSIONS:
            continue
        for scope in scopes:
            for rule in SCOPE_RULES.get(scope, ()):
                if (rule.startswith(":") and perm.endswith(rule)) or (
                    not rule.startswith(":") and (perm.startswith(rule) or perm == rule)
                ):
                    allowed.add(perm)
    return frozenset(allowed)


def can_assign_role(actor_role: str, target_role: str) -> bool:
    """Admins may assign roles up to admin; only owners may create other owners."""
    if actor_role == Role.OWNER:
        return True
    if actor_role == Role.ADMIN:
        return ROLE_RANK.get(target_role, 99) <= ROLE_RANK[Role.ADMIN]
    return False
