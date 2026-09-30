"""The acting identity for lab services.

Every lab service function takes ``(db, actor, ...)``. An :class:`Actor` is built from an authenticated
HTTP principal (user, API key, service account) or created by the runtime for agents, workflows and
system jobs. Agent and workflow actors can only ever hold a *subset* of the permissions of whoever
started them — they cannot grant themselves permissions or raise their own autonomy.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from typing import Literal

from aegis_api.errors import Forbidden
from aegis_api.security.context import Principal

ActorKind = Literal["user", "api_key", "service_account", "agent", "workflow", "system"]
HUMAN_AUTH_METHODS = frozenset({"session", "supabase", "jwt", "sso", "guest"})


@dataclass(frozen=True)
class Actor:
    kind: ActorKind
    organization_id: uuid.UUID
    permissions: frozenset[str]
    label: str
    user_id: uuid.UUID | None = None
    role: str | None = None
    api_key_id: uuid.UUID | None = None
    service_account_id: uuid.UUID | None = None
    agent_run_id: uuid.UUID | None = None
    agent_role: str | None = None
    workflow_run_id: uuid.UUID | None = None
    autonomy_level: str | None = None
    request_id: str | None = None
    trace_id: str | None = None
    auth_method: str | None = None
    # Restrict to these projects (service accounts / scoped keys). Empty = no extra restriction.
    project_ids: frozenset[str] = field(default_factory=frozenset)
    is_platform_admin: bool = False

    # -- construction --------------------------------------------------------------------------
    @classmethod
    def from_principal(cls, principal: Principal) -> Actor:
        method = principal.auth_method
        if method == "service_account":
            kind: ActorKind = "service_account"
        elif method == "api_key":
            kind = "api_key"
        else:
            kind = "user"
        project_ids = frozenset(getattr(principal, "project_ids", None) or ())
        return cls(
            kind=kind,
            organization_id=principal.organization_id,
            permissions=principal.permissions,
            label=principal.actor_label,
            user_id=principal.user_id if kind == "user" or principal.user_id.int != 0 else None,
            role=principal.role,
            api_key_id=principal.api_key_id,
            service_account_id=getattr(principal, "service_account_id", None),
            request_id=principal.request_id,
            auth_method=method,
            project_ids=project_ids,
            is_platform_admin=bool(getattr(principal, "is_platform_admin", False)),
        )

    @classmethod
    def system(
        cls, organization_id: uuid.UUID, label: str = "system", permissions: frozenset[str] | None = None
    ) -> Actor:
        """Platform-internal actor for jobs the platform itself controls (never used for agent actions)."""
        from aegis_api.security.rbac import ALL_LAB_PERMISSIONS

        return cls(
            kind="system",
            organization_id=organization_id,
            permissions=permissions if permissions is not None else ALL_LAB_PERMISSIONS,
            label=label,
        )

    def for_workflow(self, workflow_run_id: uuid.UUID) -> Actor:
        return replace(self, kind="workflow", workflow_run_id=workflow_run_id, label=f"workflow:{workflow_run_id}")

    def for_agent(
        self,
        *,
        agent_run_id: uuid.UUID,
        agent_role: str,
        agent_permissions: frozenset[str],
        autonomy_level: str | None,
    ) -> Actor:
        """Derive an agent actor. Permissions are the INTERSECTION of the parent actor and the agent
        version's permission ceiling — an agent can never exceed whoever started it."""
        return replace(
            self,
            kind="agent",
            permissions=self.permissions & agent_permissions,
            agent_run_id=agent_run_id,
            agent_role=agent_role,
            autonomy_level=autonomy_level,
            label=f"agent:{agent_role}:{agent_run_id}",
        )

    # -- checks --------------------------------------------------------------------------------
    @property
    def is_human(self) -> bool:
        return self.kind == "user" and (self.auth_method or "session") in HUMAN_AUTH_METHODS

    @property
    def actor_type(self) -> str:
        return self.kind

    def has(self, permission: str) -> bool:
        return permission in self.permissions

    def require(self, *permissions: str) -> None:
        missing = [p for p in permissions if p not in self.permissions]
        if missing:
            raise Forbidden(f"Missing required permission(s): {', '.join(missing)}")

    def require_human(self, action: str) -> None:
        if not self.is_human:
            raise Forbidden(f"'{action}' must be performed by a signed-in human user, not {self.kind}")

    def can_access_project(self, project_id: uuid.UUID | str) -> bool:
        return not self.project_ids or str(project_id) in self.project_ids

    def as_dict(self) -> dict[str, str | None]:
        """Compact, non-sensitive representation for events and audit payloads."""
        return {
            "kind": self.kind,
            "label": self.label,
            "user_id": str(self.user_id) if self.user_id else None,
            "agent_run_id": str(self.agent_run_id) if self.agent_run_id else None,
            "workflow_run_id": str(self.workflow_run_id) if self.workflow_run_id else None,
        }
