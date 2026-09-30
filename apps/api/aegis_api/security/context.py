"""The authenticated principal and helpers for permission checks."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from aegis_api.errors import Forbidden
from aegis_api.security.rbac import permissions_for_role


@dataclass
class Principal:
    user_id: uuid.UUID
    organization_id: uuid.UUID
    role: str
    permissions: frozenset[str]
    email: str | None = None
    display_name: str | None = None
    auth_method: str = "session"  # session | jwt | supabase | api_key | service_account | guest | sso
    api_key_id: uuid.UUID | None = None
    service_account_id: uuid.UUID | None = None
    token_family_id: uuid.UUID | None = None
    is_guest: bool = False
    request_id: str | None = None
    scopes: list[str] = field(default_factory=list)
    # Service accounts may be restricted to specific projects (empty = all projects in the org).
    project_ids: frozenset[str] = frozenset()
    # For workflow principals: the identity that originally launched the automation (kept through nesting).
    delegated_actor_id: str | None = None

    @property
    def actor_type(self) -> str:
        if self.auth_method == "workflow":
            return "workflow"
        if self.service_account_id is not None:
            return "service_account"
        return "api_key" if self.auth_method in ("api_key", "service_account") else "user"

    @property
    def is_human(self) -> bool:
        return self.actor_type == "user" and not self.is_guest

    @property
    def actor_id(self) -> str:
        if self.auth_method == "workflow":
            return f"workflow:{self.user_id}"
        if self.service_account_id is not None:
            return f"service_account:{self.service_account_id}"
        if self.api_key_id is not None:
            return f"api_key:{self.api_key_id}"
        return str(self.user_id)

    @property
    def actor_label(self) -> str:
        if self.service_account_id is not None:
            return f"service_account:{self.service_account_id}"
        if self.auth_method == "api_key":
            return f"api_key:{self.api_key_id}"
        return self.email or str(self.user_id)

    def has(self, permission: str) -> bool:
        return permission in self.permissions

    def require(self, *permissions: str) -> None:
        missing = [p for p in permissions if p not in self.permissions]
        if missing:
            raise Forbidden(f"Missing required permission(s): {', '.join(missing)}")

    def require_human(self, action: str) -> None:
        if not self.is_human:
            raise Forbidden(f"'{action}' requires an interactive human user (not an API key, service account or guest)")

    def may_access_project(self, project_id: uuid.UUID | str) -> bool:
        return not self.project_ids or str(project_id) in self.project_ids

    def snapshot(self) -> dict[str, object]:
        """Serializable authorization snapshot carried by workflows started on this principal's behalf.
        Workflows can never exceed these permissions (no privilege escalation through automation)."""
        return {
            "user_id": str(self.user_id),
            "organization_id": str(self.organization_id),
            "role": self.role,
            "permissions": sorted(self.permissions),
            "actor_type": self.actor_type,
            "actor_id": self.delegated_actor_id or self.actor_id,
            "actor_label": self.actor_label,
            "project_ids": sorted(self.project_ids),
        }


def build_permissions(role: str, scopes: list[str] | None, custom: frozenset[str] | None = None) -> frozenset[str]:
    from aegis_api.security.rbac import apply_scopes

    base = custom if custom is not None else permissions_for_role(role)
    if scopes is not None:
        return apply_scopes(base, scopes)
    return base


def principal_from_snapshot(snapshot: dict[str, object], *, request_id: str | None = None) -> Principal:
    perms = snapshot.get("permissions") or []
    projects = snapshot.get("project_ids") or []
    return Principal(
        user_id=uuid.UUID(str(snapshot["user_id"])),
        organization_id=uuid.UUID(str(snapshot["organization_id"])),
        role=str(snapshot.get("role", "viewer")),
        permissions=frozenset(str(p) for p in perms) if isinstance(perms, list) else frozenset(),
        auth_method="workflow",
        request_id=request_id,
        project_ids=frozenset(str(p) for p in projects) if isinstance(projects, list) else frozenset(),
        delegated_actor_id=str(snapshot.get("actor_id") or snapshot["user_id"]),
    )
