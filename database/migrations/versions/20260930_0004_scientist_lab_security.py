"""Scientist Lab security: RLS tenant isolation, append-only/immutable versions, search & vector indexes.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-30 12:30:00+00:00

Design notes
------------
* Every lab table carrying a NOT NULL ``organization_id`` gets the same tenant-isolation policy as the
  Aegis core tables (see 0002). ``refresh_tokens`` is identity-layer data (like ``auth_sessions``) and is
  only accessed through the owner connection, so it is deliberately left without RLS.
* Catalog tables whose ``organization_id`` is NULL for built-in rows (roles, prompt templates, evaluators,
  benchmark suites, model configs) are globally readable but tenant-writable only.
* Version/ledger tables are append-only. A single switch, ``SET LOCAL aegis.allow_evidence_delete = on``,
  permits DELETE for audited retention purges and tenant offboarding (same switch as the evidence table).
* Tables with a mutable lifecycle but immutable content use ``aegis.protect_columns(<mutable cols…>)``:
  any UPDATE touching a column not listed is rejected.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_PREDICATE = "(organization_id = aegis.current_org_id() OR aegis.is_org_member(organization_id))"

LAB_TENANT_TABLES: tuple[str, ...] = (
    "agent_messages",
    "agent_runs",
    "agent_steps",
    "agent_versions",
    "agents",
    "approvals",
    "artifact_versions",
    "artifacts",
    "benchmark_runs",
    "claim_evidence",
    "code_snapshots",
    "compute_jobs",
    "compute_usage",
    "dataset_versions",
    "datasets",
    "discoveries",
    "discovery_versions",
    "embeddings",
    "evaluation_runs",
    "event_consumer_offsets",
    "events",
    "evolution_runs",
    "execution_environments",
    "experiment_comparisons",
    "experiment_metrics",
    "experiment_runs",
    "experiment_versions",
    "experiments",
    "failures",
    "governance_policies",
    "governance_policy_versions",
    "graph_edges",
    "graph_nodes",
    "hypotheses",
    "hypothesis_critiques",
    "hypothesis_evidence",
    "idempotency_keys",
    "identity_providers",
    "invoice_references",
    "lessons",
    "mcp_servers",
    "mcp_tools",
    "memories",
    "memory_links",
    "mission_reports",
    "mission_versions",
    "missions",
    "model_usage",
    "organization_settings",
    "project_members",
    "projects",
    "reproductions",
    "research_events",
    "research_sources",
    "research_tasks",
    "scientific_claims",
    "scientific_reviews",
    "service_accounts",
    "source_chunks",
    "source_documents",
    "storage_usage",
    "strategies",
    "strategy_evaluations",
    "strategy_mutations",
    "strategy_versions",
    "subscriptions",
    "team_members",
    "teams",
    "tool_invocations",
    "usage_records",
    "verification_runs",
    "verifications",
    "workflow_runs",
    "workflow_steps",
    "workspace_members",
    "workspaces",
)

LAB_GLOBAL_READABLE_TABLES: tuple[str, ...] = (
    "benchmark_suites",
    "lab_evaluators",
    "model_configs",
    "prompt_templates",
    "role_permissions",
    "roles",
)

# Fully append-only: no UPDATE ever; DELETE only with the purge switch.
APPEND_ONLY_TABLES: tuple[str, ...] = (
    "events",
    "mission_versions",
    "agent_versions",
    "agent_steps",
    "experiment_versions",
    "dataset_versions",
    "governance_policy_versions",
    "discovery_versions",
    "claim_evidence",
    "model_usage",
    "compute_usage",
    "storage_usage",
    "code_snapshots",
    "execution_environments",
    "strategy_evaluations",
    "strategy_mutations",
)

# Content-immutable with a small set of lifecycle columns that may change.
COLUMN_PROTECTED: dict[str, tuple[str, ...]] = {
    "artifact_versions": ("scan_status",),
    "prompt_templates": ("status", "updated_at"),
    "lab_evaluators": ("status",),
    "strategy_versions": (
        "status",
        "fitness",
        "pareto_rank",
        "crowding",
        "promoted_at",
        "promoted_by_id",
        "retired_at",
        "lock_version",
        "updated_at",
    ),
    "benchmark_suites": (),
}

FTS_INDEXES: dict[str, str] = {
    "ix_fts_missions": "missions USING gin (to_tsvector('english', coalesce(title,'') || ' ' || coalesce(objective,'')))",
    "ix_fts_hypotheses": "hypotheses USING gin (to_tsvector('english', coalesce(statement,'') || ' ' || coalesce(rationale,'')))",
    "ix_fts_research_sources": "research_sources USING gin (to_tsvector('english', coalesce(title,'') || ' ' || coalesce(abstract,'')))",
    "ix_fts_scientific_claims": "scientific_claims USING gin (to_tsvector('english', coalesce(statement,'')))",
    "ix_fts_failures": "failures USING gin (to_tsvector('english', coalesce(title,'') || ' ' || coalesce(root_cause,'')))",
    "ix_fts_discoveries": "discoveries USING gin (to_tsvector('english', coalesce(title,'') || ' ' || coalesce(summary,'')))",
    "ix_fts_lessons": "lessons USING gin (to_tsvector('english', coalesce(statement,'')))",
    "ix_fts_source_chunks": "source_chunks USING gin (tsv)",
}


def upgrade() -> None:
    # --- tenant isolation ---------------------------------------------------------------------
    for table in LAB_TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {table} "
            f"USING {TENANT_PREDICATE} WITH CHECK {TENANT_PREDICATE}"
        )
    for table in LAB_GLOBAL_READABLE_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_read ON {table} FOR SELECT "
            f"USING (organization_id IS NULL OR {TENANT_PREDICATE})"
        )
        op.execute(
            f"CREATE POLICY {table}_write ON {table} FOR ALL "
            f"USING {TENANT_PREDICATE} WITH CHECK {TENANT_PREDICATE}"
        )

    # --- immutability -------------------------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION aegis.forbid_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            IF coalesce(current_setting('aegis.allow_evidence_delete', true), '') = 'on' THEN
              RETURN OLD;
            END IF;
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = 'insufficient_privilege';
          END IF;
          RAISE EXCEPTION '% rows are immutable', TG_TABLE_NAME USING ERRCODE = 'insufficient_privilege';
        END
        $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION aegis.protect_columns() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          mutable text[] := TG_ARGV;
        BEGIN
          IF TG_OP = 'DELETE' THEN
            IF coalesce(current_setting('aegis.allow_evidence_delete', true), '') = 'on' THEN
              RETURN OLD;
            END IF;
            RAISE EXCEPTION '% rows cannot be deleted', TG_TABLE_NAME USING ERRCODE = 'insufficient_privilege';
          END IF;
          IF (to_jsonb(NEW) - mutable) IS DISTINCT FROM (to_jsonb(OLD) - mutable) THEN
            RAISE EXCEPTION 'immutable columns of % cannot be modified', TG_TABLE_NAME
              USING ERRCODE = 'insufficient_privilege';
          END IF;
          RETURN NEW;
        END
        $$;
        """
    )
    for table in APPEND_ONLY_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION aegis.forbid_mutation()"
        )
    for table, columns in COLUMN_PROTECTED.items():
        args = ", ".join(f"'{c}'" for c in columns)
        op.execute(
            f"CREATE TRIGGER {table}_protect_columns BEFORE UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION aegis.protect_columns({args})"
        )

    # --- search & vectors -----------------------------------------------------------------------
    for name, definition in FTS_INDEXES.items():
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {definition}")
    op.execute("CREATE INDEX IF NOT EXISTS ix_vec_embeddings ON embeddings USING hnsw (embedding vector_cosine_ops)")

    # --- grants for the RLS-enforced application role --------------------------------------------
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aegis_app') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO aegis_app;
            GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO aegis_app;
            GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA aegis TO aegis_app;
            ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO aegis_app;
          END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_vec_embeddings")
    for name in FTS_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {name}")
    for table in COLUMN_PROTECTED:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_protect_columns ON {table}")
    for table in APPEND_ONLY_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
    op.execute("DROP FUNCTION IF EXISTS aegis.protect_columns()")
    op.execute("DROP FUNCTION IF EXISTS aegis.forbid_mutation()")
    for table in LAB_GLOBAL_READABLE_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_read ON {table}")
        op.execute(f"DROP POLICY IF EXISTS {table}_write ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    for table in LAB_TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
