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
    auth_method: str = "session"  # session | supabase | api_key | guest
    api_key_id: uuid.UUID | None = None
    is_guest: bool = False
    request_id: str | None = None
    scopes: list[str] = field(default_factory=list)

    @property
    def actor_type(self) -> str:
        return "api_key" if self.auth_method == "api_key" else "user"

    @property
    def actor_label(self) -> str:
        if self.auth_method == "api_key":
            return f"api_key:{self.api_key_id}"
        return self.email or str(self.user_id)

    def has(self, permission: str) -> bool:
        return permission in self.permissions

    def require(self, *permissions: str) -> None:
        missing = [p for p in permissions if p not in self.permissions]
        if missing:
            raise Forbidden(f"Missing required permission(s): {', '.join(missing)}")


def build_permissions(role: str, scopes: list[str] | None) -> frozenset[str]:
    from aegis_api.security.rbac import apply_scopes

    base = permissions_for_role(role)
    if scopes is not None:
        return apply_scopes(base, scopes)
    return base
