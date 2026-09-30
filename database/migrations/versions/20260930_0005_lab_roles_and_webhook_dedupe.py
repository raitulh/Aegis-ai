"""Widen role columns for lab roles; index webhook deliveries for event dedupe.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-30 16:00:00+00:00

* ``memberships.role``, ``invitations.role`` and ``api_keys.role`` were ``varchar(16)``; the lab role
  ``scientist_operator`` is 18 characters, so they become ``varchar(32)``.
* ``webhook_deliveries(organization_id, event_id)`` supports the event consumer's duplicate-event check
  (not unique: redeliveries reuse the event id).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE_TABLES = ("memberships", "invitations", "api_keys")


def upgrade() -> None:
    for table in ROLE_TABLES:
        op.alter_column(table, "role", type_=sa.String(length=32), existing_type=sa.String(length=16))
    op.create_index("ix_webhook_deliveries_org_event", "webhook_deliveries", ["organization_id", "event_id"])


def downgrade() -> None:
    op.drop_index("ix_webhook_deliveries_org_event", table_name="webhook_deliveries")
    for table in ROLE_TABLES:
        # Narrowing fails loudly if a lab role longer than 16 characters is stored — intentional.
        op.alter_column(table, "role", type_=sa.String(length=16), existing_type=sa.String(length=32))
