"""RBAC: lab role matrices, scope narrowing and human-only permissions."""

from __future__ import annotations

from aegis_api.models.enums import Role
from aegis_api.security.rbac import (
    ALL_LAB_PERMISSIONS,
    HUMAN_ONLY_PERMISSIONS,
    LAB_PERMISSION_CATALOG,
    ROLE_PERMISSIONS,
    apply_scopes,
    can_assign_role,
    permissions_for_role,
)


def test_every_role_defined_and_catalog_consistent():
    for role in Role:
        assert role.value in ROLE_PERMISSIONS
    for perm in ALL_LAB_PERMISSIONS:
        category, description = LAB_PERMISSION_CATALOG[perm]
        assert ":" in perm and category and description


def test_spec_permissions_exist():
    for perm in (
        "mission:create",
        "mission:run",
        "experiment:create",
        "experiment:execute",
        "strategy:promote",
        "discovery:approve",
        "artifact:download",
        "billing:view",
        "admin:policy",
    ):
        assert perm in ALL_LAB_PERMISSIONS


def test_viewer_is_read_only():
    viewer = permissions_for_role(Role.VIEWER)
    lab = viewer & ALL_LAB_PERMISSIONS
    assert lab and all(p.endswith(":read") for p in lab)


def test_researcher_cannot_approve_or_promote():
    researcher = permissions_for_role(Role.RESEARCHER)
    for perm in ("approval:decide", "discovery:approve", "strategy:promote", "admin:policy", "mcp:manage"):
        assert perm not in researcher
    assert "experiment:execute" in researcher and "mission:run" in researcher


def test_reviewer_can_approve_discoveries_but_not_execute():
    reviewer = permissions_for_role(Role.REVIEWER)
    assert "discovery:approve" in reviewer and "approval:decide" in reviewer
    assert "experiment:execute" not in reviewer and "mission:create" not in reviewer


def test_billing_admin_scope():
    billing = permissions_for_role(Role.BILLING_ADMIN)
    assert {"billing:view", "billing:manage", "usage:read"} <= billing
    assert "mission:read" not in billing


def test_owner_has_everything_admin_lacks_billing_manage():
    assert permissions_for_role(Role.OWNER) >= ALL_LAB_PERMISSIONS
    assert "billing:manage" not in permissions_for_role(Role.ADMIN) & ALL_LAB_PERMISSIONS


def test_scopes_never_grant_human_only_permissions():
    owner = permissions_for_role(Role.OWNER)
    scoped = apply_scopes(owner, ["read", "write", "run", "ingest"])
    assert not (scoped & HUMAN_ONLY_PERMISSIONS)
    assert "experiment:execute" in scoped and "mission:create" in scoped


def test_read_scope_is_read_only():
    scoped = apply_scopes(permissions_for_role(Role.OWNER), ["read"])
    assert scoped and all(p.endswith(":read") or p == "artifact:download" for p in scoped)


def test_empty_scopes_default_to_read():
    scoped = apply_scopes(permissions_for_role(Role.RESEARCHER), [])
    assert scoped and all(p.endswith(":read") for p in scoped)


def test_role_assignment_rules():
    assert can_assign_role(Role.OWNER, Role.OWNER)
    assert not can_assign_role(Role.ADMIN, Role.OWNER)
    assert can_assign_role(Role.ADMIN, Role.RESEARCH_LEAD)
    assert not can_assign_role(Role.RESEARCHER, Role.VIEWER)
