"""Security hardening: RLS tenant isolation, immutability triggers, app role, search & vector indexes.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-27 08:10:00+00:00

Design notes
------------
* The API/worker connect as a non-owner role that is a member of ``aegis_app``; RLS policies apply to it.
  Migrations, seeding and cross-tenant maintenance use the owner connection (``DATABASE_ADMIN_URL``).
* ``app.current_org_id`` / ``app.current_user_id`` are set per transaction by the API (see
  ``aegis_api.db.session``). On Supabase, policies additionally honour the PostgREST JWT subject so a
  direct PostgREST client can only see organizations it is an active member of.
* Identity tables needed *before* a tenant is known (users, auth_sessions, auth_tokens, api_keys) are
  not RLS-protected; the application scopes every query on them explicitly.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES: tuple[str, ...] = (
    "agent_traces",
    "ai_systems",
    "alerts",
    "audit_events",
    "audit_logs",
    "audit_runs",
    "audits",
    "claims",
    "control_assessments",
    "control_mappings",
    "controls",
    "document_chunks",
    "evaluations",
    "evidence",
    "evidence_links",
    "finding_events",
    "findings",
    "integrations",
    "invitations",
    "knowledge_chunks",
    "knowledge_documents",
    "model_calls",
    "model_versions",
    "models",
    "monitoring_events",
    "monitors",
    "notifications",
    "policies",
    "policy_documents",
    "policy_requirements",
    "policy_versions",
    "providers",
    "redteam_probes",
    "redteam_runs",
    "regression_runs",
    "regression_tests",
    "remediations",
    "reports",
    "risk_snapshots",
    "saved_filters",
    "secrets",
    "system_events",
    "system_versions",
    "test_cases",
    "test_results",
    "test_suites",
    "tool_calls",
    "trace_events",
    "webhook_deliveries",
    "webhooks",
)

# Tables with nullable organization_id where NULL means "global reference data" (readable by all tenants).
GLOBAL_READABLE_TABLES: tuple[str, ...] = ("frameworks", "feature_flags")

FTS_INDEXES: dict[str, str] = {
    "ix_fts_ai_systems": "ai_systems USING gin (to_tsvector('simple', coalesce(name,'') || ' ' || coalesce(description,'') || ' ' || coalesce(business_purpose,'')))",
    "ix_fts_findings": "findings USING gin (to_tsvector('simple', coalesce(title,'') || ' ' || coalesce(description,'')))",
    "ix_fts_policies": "policies USING gin (to_tsvector('simple', coalesce(name,'') || ' ' || coalesce(description,'')))",
    "ix_fts_controls": "controls USING gin (to_tsvector('simple', coalesce(control_id,'') || ' ' || coalesce(name,'') || ' ' || coalesce(description,'')))",
    "ix_fts_audits": "audits USING gin (to_tsvector('simple', coalesce(name,'')))",
    "ix_fts_evidence": "evidence USING gin (to_tsvector('simple', coalesce(title,'')))",
}

TENANT_PREDICATE = "(organization_id = aegis.current_org_id() OR aegis.is_org_member(organization_id))"


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS aegis")

    # --- context helpers ----------------------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION aegis.current_org_id() RETURNS uuid
        LANGUAGE sql STABLE AS $$
          SELECT nullif(current_setting('app.current_org_id', true), '')::uuid
        $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION aegis.current_user_id() RETURNS uuid
        LANGUAGE sql STABLE AS $$
          SELECT nullif(current_setting('app.current_user_id', true), '')::uuid
        $$;
        """
    )
    # Supabase/PostgREST compatible JWT subject (mirrors auth.uid() without depending on the auth schema).
    op.execute(
        """
        CREATE OR REPLACE FUNCTION aegis.jwt_subject() RETURNS text
        LANGUAGE sql STABLE AS $$
          SELECT coalesce(
            nullif(current_setting('request.jwt.claim.sub', true), ''),
            (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub')
          )
        $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION aegis.is_org_member(org uuid) RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
          SELECT CASE
            WHEN aegis.jwt_subject() IS NULL THEN false
            ELSE EXISTS (
              SELECT 1 FROM memberships m JOIN users u ON u.id = m.user_id
              WHERE m.organization_id = org AND m.status = 'active' AND u.auth_subject = aegis.jwt_subject()
            )
          END
        $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION aegis.user_has_membership(org uuid) RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
          SELECT aegis.current_user_id() IS NOT NULL AND EXISTS (
            SELECT 1 FROM memberships m
            WHERE m.organization_id = org AND m.user_id = aegis.current_user_id() AND m.status <> 'suspended'
          )
        $$;
        """
    )

    # --- tenant isolation ---------------------------------------------------------------------
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {table} "
            f"USING {TENANT_PREDICATE} WITH CHECK {TENANT_PREDICATE}"
        )

    for table in GLOBAL_READABLE_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_read ON {table} FOR SELECT "
            f"USING (organization_id IS NULL OR {TENANT_PREDICATE})"
        )
        op.execute(
            f"CREATE POLICY {table}_write ON {table} FOR ALL "
            f"USING {TENANT_PREDICATE} WITH CHECK {TENANT_PREDICATE}"
        )

    op.execute("ALTER TABLE memberships ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY memberships_isolation ON memberships "
        "USING (organization_id = aegis.current_org_id() OR user_id = aegis.current_user_id() "
        "OR aegis.is_org_member(organization_id)) "
        "WITH CHECK (organization_id = aegis.current_org_id())"
    )
    op.execute("ALTER TABLE organizations ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY organizations_isolation ON organizations "
        "USING (id = aegis.current_org_id() OR aegis.user_has_membership(id) OR aegis.is_org_member(id)) "
        "WITH CHECK (id = aegis.current_org_id())"
    )

    # --- immutability ---------------------------------------------------------------------------
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
    op.execute(
        "CREATE TRIGGER evidence_immutable BEFORE UPDATE OR DELETE ON evidence "
        "FOR EACH ROW EXECUTE FUNCTION aegis.protect_evidence()"
    )
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
        "CREATE TRIGGER audit_logs_append_only BEFORE UPDATE OR DELETE ON audit_logs "
        "FOR EACH ROW EXECUTE FUNCTION aegis.protect_audit_log()"
    )

    # --- search -------------------------------------------------------------------------------
    for name, definition in FTS_INDEXES.items():
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {definition}")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_vec_document_chunks ON document_chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_vec_knowledge_chunks ON knowledge_chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )

    # --- application role -----------------------------------------------------------------------
    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aegis_app') THEN
            CREATE ROLE aegis_app NOLOGIN;
          END IF;
        EXCEPTION WHEN insufficient_privilege THEN
          RAISE NOTICE 'Could not create role aegis_app (insufficient privilege); skipping.';
        END
        $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aegis_app') THEN
            GRANT USAGE ON SCHEMA public TO aegis_app;
            GRANT USAGE ON SCHEMA aegis TO aegis_app;
            GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO aegis_app;
            GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO aegis_app;
            GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA aegis TO aegis_app;
            ALTER DEFAULT PRIVILEGES IN SCHEMA public
              GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO aegis_app;
          END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_vec_knowledge_chunks")
    op.execute("DROP INDEX IF EXISTS ix_vec_document_chunks")
    for name in FTS_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {name}")
    op.execute("DROP TRIGGER IF EXISTS audit_logs_append_only ON audit_logs")
    op.execute("DROP TRIGGER IF EXISTS evidence_immutable ON evidence")
    op.execute("DROP POLICY IF EXISTS organizations_isolation ON organizations")
    op.execute("ALTER TABLE organizations DISABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS memberships_isolation ON memberships")
    op.execute("ALTER TABLE memberships DISABLE ROW LEVEL SECURITY")
    for table in GLOBAL_READABLE_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_read ON {table}")
        op.execute(f"DROP POLICY IF EXISTS {table}_write ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP SCHEMA IF EXISTS aegis CASCADE")
