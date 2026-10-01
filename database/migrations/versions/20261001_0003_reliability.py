"""Reliability & correctness: audit leases, finding occurrences, job ledger, idempotency keys,
hardened immutability triggers.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-01 06:00:00+00:00

* ``audits`` gain a lease (``lease_owner`` / ``heartbeat_at`` / ``attempts``) so exactly one worker can
  claim a queued audit and lost workers can be detected.
* ``finding_occurrences`` records every (finding, audit/run) observation. A finding keeps the audit that
  first detected it in ``audit_id``; ``last_audit_id`` / ``last_seen_at`` track the latest observation.
* ``job_runs`` is the background-job ledger (idempotency key, attempts, classified failures).
* ``idempotency_keys`` stores responses for ``Idempotency-Key`` request replay.
* ``usage_events`` (append-only, idempotent per source), ``subscriptions`` and ``billing_events`` form the
  usage ledger and billing-provider bookkeeping.
* The evidence / audit-log purge path now also requires a role that is not a member of ``aegis_app``:
  the RLS-bound application role can no longer bypass immutability by setting the session GUC.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_PREDICATE = "(organization_id = aegis.current_org_id() OR aegis.is_org_member(organization_id))"

# Only the owner / maintenance role (superuser or a role outside aegis_app) may use the purge GUC.
PURGE_ALLOWED = (
    "coalesce(current_setting('aegis.allow_evidence_delete', true), '') = 'on' "
    "AND ((SELECT rolsuper FROM pg_roles WHERE rolname = current_user) "
    "OR NOT pg_has_role(current_user, 'aegis_app', 'MEMBER'))"
)


def _ts(name: str, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=nullable)


def _tenant_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_tenant_isolation ON {table} USING {TENANT_PREDICATE} WITH CHECK {TENANT_PREDICATE}"
    )


def upgrade() -> None:
    # --- roles: new role keys (security_engineer, ai_engineer, developer, service_account) ----------
    for table in ("memberships", "invitations", "api_keys"):
        op.alter_column(table, "role", type_=sa.String(length=32), existing_type=sa.String(length=16))

    # --- audit lease ----------------------------------------------------------------------------
    op.add_column("audits", sa.Column("lease_owner", sa.String(length=160), nullable=True))
    op.add_column("audits", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("audits", sa.Column("attempts", sa.Integer(), server_default="0", nullable=False))
    op.create_index("ix_audits_status_heartbeat", "audits", ["status", "heartbeat_at"])
    for table in ("redteam_runs", "regression_runs"):
        op.add_column(table, sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))

    # --- finding scoping ------------------------------------------------------------------------
    op.add_column("findings", sa.Column("last_audit_id", sa.Uuid(), nullable=True))
    op.add_column("findings", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_findings_last_audit_id_audits", "findings", "audits", ["last_audit_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index("ix_findings_org_system_fingerprint", "findings", ["organization_id", "system_id", "fingerprint"])
    op.execute("UPDATE findings SET last_audit_id = audit_id, last_seen_at = updated_at")

    op.create_table(
        "finding_occurrences",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("finding_id", sa.Uuid(), nullable=False),
        sa.Column("system_id", sa.Uuid(), nullable=False),
        sa.Column("audit_id", sa.Uuid(), nullable=True),
        sa.Column("source_type", sa.String(length=24), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("risk_level", sa.String(length=16), nullable=True),
        sa.Column("occurrences", sa.Integer(), server_default="1", nullable=False),
        sa.Column("sample_size", sa.Integer(), server_default="1", nullable=False),
        sa.Column("system_version", sa.String(length=40), nullable=True),
        _ts("observed_at"),
        _ts("created_at"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name="fk_finding_occurrences_organization_id_organizations", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["finding_id"], ["findings.id"], name="fk_finding_occurrences_finding_id_findings", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["system_id"], ["ai_systems.id"], name="fk_finding_occurrences_system_id_ai_systems", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["audit_id"], ["audits.id"], name="fk_finding_occurrences_audit_id_audits", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_finding_occurrences"),
        sa.UniqueConstraint("finding_id", "source_type", "source_id", name="uq_finding_occurrences_source"),
    )
    op.create_index("ix_finding_occurrences_org", "finding_occurrences", ["organization_id"])
    op.create_index("ix_finding_occurrences_audit", "finding_occurrences", ["audit_id"])
    op.create_index("ix_finding_occurrences_finding", "finding_occurrences", ["finding_id", "observed_at"])
    op.execute(
        """
        INSERT INTO finding_occurrences (organization_id, finding_id, system_id, audit_id, source_type, source_id,
                                         severity, risk_level, occurrences, sample_size, system_version,
                                         observed_at, created_at)
        SELECT organization_id, id, system_id, audit_id, 'audit', audit_id, severity, risk_level, occurrences,
               sample_size, system_version, created_at, created_at
        FROM findings WHERE audit_id IS NOT NULL
        ON CONFLICT DO NOTHING
        """
    )
    _tenant_rls("finding_occurrences")

    # --- job ledger -----------------------------------------------------------------------------
    op.create_table(
        "job_runs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("job", sa.String(length=120), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("args", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("status", sa.String(length=16), server_default="queued", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column("worker", sa.String(length=160), nullable=True),
        sa.Column("error_class", sa.String(length=24), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name="fk_job_runs_organization_id_organizations", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_job_runs"),
        sa.UniqueConstraint("idempotency_key", name="uq_job_runs_idempotency_key"),
    )
    op.create_index("ix_job_runs_status_next", "job_runs", ["status", "next_attempt_at"])
    op.create_index("ix_job_runs_org_created", "job_runs", ["organization_id", "created_at"])
    op.execute("ALTER TABLE job_runs ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY job_runs_tenant_read ON job_runs FOR SELECT USING {TENANT_PREDICATE}")

    # --- idempotency keys -----------------------------------------------------------------------
    op.create_table(
        "idempotency_keys",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("method", sa.String(length=8), nullable=False),
        sa.Column("path", sa.String(length=300), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        _ts("created_at"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name="fk_idempotency_keys_organization_id_organizations", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_idempotency_keys"),
        sa.UniqueConstraint("organization_id", "key", name="uq_idempotency_keys_org_key"),
    )
    op.create_index("ix_idempotency_keys_expires", "idempotency_keys", ["expires_at"])
    _tenant_rls("idempotency_keys")

    # --- usage ledger & billing ------------------------------------------------------------------
    op.create_table(
        "usage_events",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("metric", sa.String(length=48), nullable=False),
        sa.Column("quantity", sa.BigInteger(), server_default="1", nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=80), nullable=False),
        _ts("occurred_at"),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        _ts("created_at"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name="fk_usage_events_organization_id_organizations", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_usage_events"),
        sa.UniqueConstraint("organization_id", "metric", "source_type", "source_id", name="uq_usage_events_source"),
    )
    op.create_index("ix_usage_events_org_metric_time", "usage_events", ["organization_id", "metric", "occurred_at"])
    op.create_index("ix_usage_events_organization_id", "usage_events", ["organization_id"])
    op.create_index("ix_usage_events_created_at", "usage_events", ["created_at"])
    _tenant_rls("usage_events")
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION aegis.protect_usage_ledger() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' AND {PURGE_ALLOWED} THEN
            RETURN OLD;
          END IF;
          RAISE EXCEPTION 'usage ledger is append-only' USING ERRCODE = 'insufficient_privilege';
        END
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER usage_events_append_only BEFORE UPDATE OR DELETE ON usage_events "
        "FOR EACH ROW EXECUTE FUNCTION aegis.protect_usage_ledger()"
    )

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("plan", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="active", nullable=False),
        sa.Column("provider", sa.String(length=24), server_default="none", nullable=False),
        sa.Column("provider_customer_id", sa.String(length=120), nullable=True),
        sa.Column("provider_subscription_id", sa.String(length=120), nullable=True),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_at_period_end", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("overrides", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name="fk_subscriptions_organization_id_organizations", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_subscriptions"),
        sa.UniqueConstraint("organization_id", name="uq_subscriptions_org"),
    )
    op.create_index("ix_subscriptions_organization_id", "subscriptions", ["organization_id"])
    op.create_index("ix_subscriptions_created_at", "subscriptions", ["created_at"])
    _tenant_rls("subscriptions")

    op.create_table(
        "billing_events",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("provider", sa.String(length=24), nullable=False),
        sa.Column("provider_event_id", sa.String(length=120), nullable=False),
        sa.Column("type", sa.String(length=80), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.String(length=500), nullable=True),
        _ts("created_at"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name="fk_billing_events_organization_id_organizations", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_billing_events"),
        sa.UniqueConstraint("provider", "provider_event_id", name="uq_billing_events_provider_event"),
    )
    op.create_index("ix_billing_events_organization_id", "billing_events", ["organization_id"])
    op.create_index("ix_billing_events_created_at", "billing_events", ["created_at"])
    op.execute("ALTER TABLE billing_events ENABLE ROW LEVEL SECURITY")

    # --- immutability hardening -----------------------------------------------------------------
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION aegis.protect_evidence() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            IF {PURGE_ALLOWED} THEN
              RETURN OLD;
            END IF;
            RAISE EXCEPTION 'evidence is append-only (use soft delete / retention purge)'
              USING ERRCODE = 'insufficient_privilege';
          END IF;
          IF NEW.kind IS DISTINCT FROM OLD.kind
             OR NEW.audit_id IS DISTINCT FROM OLD.audit_id
             OR NEW.organization_id IS DISTINCT FROM OLD.organization_id
             OR NEW.seq IS DISTINCT FROM OLD.seq
             OR NEW.title IS DISTINCT FROM OLD.title
             OR NEW.created_at IS DISTINCT FROM OLD.created_at
             OR NEW.content_hash IS DISTINCT FROM OLD.content_hash
             OR NEW.chain_hash IS DISTINCT FROM OLD.chain_hash
             OR NEW.prev_hash IS DISTINCT FROM OLD.prev_hash THEN
            RAISE EXCEPTION 'evidence identity fields are immutable' USING ERRCODE = 'insufficient_privilege';
          END IF;
          IF (NEW.content IS DISTINCT FROM OLD.content OR NEW.sensitive_ciphertext IS DISTINCT FROM OLD.sensitive_ciphertext)
             AND NOT (OLD.purged_at IS NULL AND NEW.purged_at IS NOT NULL AND {PURGE_ALLOWED}) THEN
            RAISE EXCEPTION 'evidence content is immutable' USING ERRCODE = 'insufficient_privilege';
          END IF;
          RETURN NEW;
        END
        $$;
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION aegis.protect_audit_log() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' AND {PURGE_ALLOWED} THEN
            RETURN OLD;
          END IF;
          RAISE EXCEPTION 'audit log is append-only' USING ERRCODE = 'insufficient_privilege';
        END
        $$;
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO aegis_app")


def downgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION aegis.protect_audit_log() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' AND coalesce(current_setting('aegis.allow_evidence_delete', true), '') = 'on' THEN
            RETURN OLD;
          END IF;
          RAISE EXCEPTION 'audit log is append-only' USING ERRCODE = 'insufficient_privilege';
        END
        $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION aegis.protect_evidence() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            IF coalesce(current_setting('aegis.allow_evidence_delete', true), '') = 'on' THEN
              RETURN OLD;
            END IF;
            RAISE EXCEPTION 'evidence is append-only (use soft delete / retention purge)'
              USING ERRCODE = 'insufficient_privilege';
          END IF;
          IF NEW.kind IS DISTINCT FROM OLD.kind
             OR NEW.audit_id IS DISTINCT FROM OLD.audit_id
             OR NEW.created_at IS DISTINCT FROM OLD.created_at
             OR NEW.content_hash IS DISTINCT FROM OLD.content_hash
             OR NEW.chain_hash IS DISTINCT FROM OLD.chain_hash
             OR NEW.prev_hash IS DISTINCT FROM OLD.prev_hash THEN
            RAISE EXCEPTION 'evidence identity fields are immutable' USING ERRCODE = 'insufficient_privilege';
          END IF;
          IF (NEW.content IS DISTINCT FROM OLD.content OR NEW.sensitive_ciphertext IS DISTINCT FROM OLD.sensitive_ciphertext)
             AND NOT (OLD.purged_at IS NULL AND NEW.purged_at IS NOT NULL) THEN
            RAISE EXCEPTION 'evidence content is immutable' USING ERRCODE = 'insufficient_privilege';
          END IF;
          RETURN NEW;
        END
        $$;
        """
    )
    op.drop_table("billing_events")
    op.drop_table("subscriptions")
    op.execute("DROP TRIGGER IF EXISTS usage_events_append_only ON usage_events")
    op.drop_table("usage_events")
    op.execute("DROP FUNCTION IF EXISTS aegis.protect_usage_ledger()")
    op.drop_table("idempotency_keys")
    op.drop_table("job_runs")
    op.drop_table("finding_occurrences")
    op.drop_index("ix_findings_org_system_fingerprint", table_name="findings")
    op.drop_constraint("fk_findings_last_audit_id_audits", "findings", type_="foreignkey")
    op.drop_column("findings", "last_seen_at")
    op.drop_column("findings", "last_audit_id")
    for table in ("regression_runs", "redteam_runs"):
        op.drop_column(table, "heartbeat_at")
    op.drop_index("ix_audits_status_heartbeat", table_name="audits")
    op.drop_column("audits", "attempts")
    op.drop_column("audits", "heartbeat_at")
    op.drop_column("audits", "lease_owner")
    for table in ("memberships", "invitations", "api_keys"):
        # Map roles that do not fit the old width back to their nearest legacy equivalent.
        op.execute(
            f"UPDATE {table} SET role = CASE role WHEN 'security_engineer' THEN 'auditor' "
            "WHEN 'ai_engineer' THEN 'analyst' WHEN 'developer' THEN 'analyst' "
            "WHEN 'service_account' THEN 'analyst' ELSE role END"
        )
        op.alter_column(table, "role", type_=sa.String(length=16), existing_type=sa.String(length=32))
