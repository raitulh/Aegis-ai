"""AI Scientist Evolution Lab: tenancy extensions (workspaces, projects, teams, service accounts, custom
roles, quotas, refresh tokens, idempotency, SSO, billing) and the ``lab`` schema (missions, agents, research,
knowledge, experiments, execution, evaluation, failures, strategies, evolution, verification, discoveries,
approvals, policies, MCP, tool calls, events, durable workflow state, usage metering).

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-30 15:00:00+00:00

Design notes
------------
* The scientist domain lives in its own PostgreSQL schema ``lab`` (bounded context / future extraction
  boundary; avoids collisions with the assurance domain's claims, policies, evaluators and tool_calls).
* Every tenant-owned table gets the same RLS predicate as migration 0002. Reference tables with a nullable
  organization_id (system roles, built-in prompt templates, global environments, global model configs) are
  readable by every tenant and writable only within the tenant.
* Append-only ledgers (events, experiment/policy/mission/agent versions, evaluation results, verification
  checks, usage ledgers) are protected by ``lab.protect_immutable()``; the optional trigger arguments list
  the columns that may still change (e.g. processing status). Deletes are only possible during an
  authorized retention purge (``aegis.allow_evidence_delete = on``), mirroring evidence.
* ``evidence.chain_scope`` lets lab evidence form per-mission hash chains in the existing immutable evidence
  store; the uniqueness of (chain_scope, seq) prevents forked chains under concurrency.
* ``lab.tool_usage`` is a security-invoker view over ``lab.tool_calls`` (RLS of the caller applies).
"""

from __future__ import annotations

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_PREDICATE = "(organization_id = aegis.current_org_id() OR aegis.is_org_member(organization_id))"

TENANT_TABLES: tuple[str, ...] = (
    "idempotency_keys",
    "invoice_references",
    "lab.agent_messages",
    "lab.agent_runs",
    "lab.agent_versions",
    "lab.agents",
    "lab.approvals",
    "lab.artifact_versions",
    "lab.artifacts",
    "lab.benchmark_results",
    "lab.benchmark_runs",
    "lab.claim_evidence",
    "lab.claims",
    "lab.compute_jobs",
    "lab.compute_usage",
    "lab.dataset_versions",
    "lab.datasets",
    "lab.discoveries",
    "lab.discovery_versions",
    "lab.document_chunks",
    "lab.document_versions",
    "lab.documents",
    "lab.evaluation_runs",
    "lab.events",
    "lab.evolution_runs",
    "lab.experiment_metrics",
    "lab.experiment_runs",
    "lab.experiment_versions",
    "lab.experiments",
    "lab.failures",
    "lab.graph_edges",
    "lab.graph_nodes",
    "lab.hypotheses",
    "lab.hypothesis_evidence",
    "lab.lessons",
    "lab.mcp_servers",
    "lab.mcp_tools",
    "lab.memories",
    "lab.memory_links",
    "lab.mission_versions",
    "lab.missions",
    "lab.model_usage",
    "lab.policies",
    "lab.policy_versions",
    "lab.reports",
    "lab.research_events",
    "lab.research_sources",
    "lab.research_tasks",
    "lab.run_comparisons",
    "lab.storage_usage",
    "lab.strategies",
    "lab.strategy_evaluations",
    "lab.strategy_mutations",
    "lab.strategy_versions",
    "lab.tool_calls",
    "lab.verification_runs",
    "lab.verifications",
    "lab.workflow_runs",
    "lab.workflow_signals",
    "lab.workflow_steps",
    "organization_quotas",
    "project_members",
    "projects",
    "service_accounts",
    "sso_connections",
    "subscriptions",
    "team_members",
    "teams",
    "usage_records",
    "workspace_members",
    "workspaces",
)

# organization_id NULL = global reference row readable by every tenant.
GLOBAL_READABLE_TABLES: tuple[str, ...] = ("roles", "lab.prompt_templates", "lab.environments", "lab.model_configs")

# (table, mutable columns) — rows are append-only apart from the listed columns.
IMMUTABLE_TABLES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("lab.events", ()),
    ("lab.research_events", ()),
    ("lab.mission_versions", ()),
    ("lab.agent_versions", ()),
    ("lab.experiment_versions", ()),
    ("lab.policy_versions", ()),
    ("lab.discovery_versions", ()),
    ("lab.evaluation_runs", ()),
    ("lab.verification_runs", ()),
    ("lab.model_usage", ()),
    ("lab.compute_usage", ()),
    ("lab.storage_usage", ()),
    ("lab.dataset_versions", ("status", "updated_at")),
    ("lab.artifact_versions", ("scan_status", "metadata", "purged_at")),
)

FTS_INDEXES: dict[str, str] = {
    "ix_fts_lab_memories": "lab.memories USING gin (to_tsvector('english', coalesce(title,'') || ' ' || content))",
    "ix_fts_lab_document_chunks": "lab.document_chunks USING gin (to_tsvector('english', text))",
    "ix_fts_lab_research_sources": "lab.research_sources USING gin (to_tsvector('english', coalesce(title,'') || ' ' || coalesce(snippet,'')))",
    "ix_fts_lab_claims": "lab.claims USING gin (to_tsvector('english', statement))",
    "ix_fts_lab_hypotheses": "lab.hypotheses USING gin (to_tsvector('english', statement || ' ' || coalesce(rationale,'')))",
    "ix_fts_lab_lessons": "lab.lessons USING gin (to_tsvector('english', lesson || ' ' || coalesce(context,'')))",
    "ix_fts_lab_failures": "lab.failures USING gin (to_tsvector('english', root_cause))",
}
VECTOR_INDEXES: dict[str, str] = {
    "ix_vec_lab_memories": "lab.memories USING hnsw (embedding vector_cosine_ops)",
    "ix_vec_lab_document_chunks": "lab.document_chunks USING hnsw (embedding vector_cosine_ops)",
}

_EVIDENCE_TRIGGER_TEMPLATE = """
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
     OR NEW.prev_hash IS DISTINCT FROM OLD.prev_hash{extra} THEN
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


def _policy_name(table: str) -> str:
    return table.replace(".", "_")


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS lab")
    _create_tables()

    # --- tenant isolation -------------------------------------------------------------------------
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {_policy_name(table)}_tenant_isolation ON {table} "
            f"USING {TENANT_PREDICATE} WITH CHECK {TENANT_PREDICATE}"
        )
    for table in GLOBAL_READABLE_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {_policy_name(table)}_read ON {table} FOR SELECT "
            f"USING (organization_id IS NULL OR {TENANT_PREDICATE})"
        )
        op.execute(
            f"CREATE POLICY {_policy_name(table)}_write ON {table} FOR ALL "
            f"USING {TENANT_PREDICATE} WITH CHECK {TENANT_PREDICATE}"
        )
    op.execute("ALTER TABLE role_permissions ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY role_permissions_isolation ON role_permissions USING (EXISTS ("
        "SELECT 1 FROM roles r WHERE r.id = role_id AND (r.organization_id IS NULL OR r.organization_id = aegis.current_org_id())"
        ")) WITH CHECK (EXISTS (SELECT 1 FROM roles r WHERE r.id = role_id AND r.organization_id = aegis.current_org_id()))"
    )

    # --- immutability -----------------------------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION lab.protect_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          mutable text[] := coalesce(TG_ARGV::text[], '{}'::text[]);
        BEGIN
          IF TG_OP = 'DELETE' THEN
            IF coalesce(current_setting('aegis.allow_evidence_delete', true), '') = 'on' THEN
              RETURN OLD;
            END IF;
            RAISE EXCEPTION '%.% rows are append-only', TG_TABLE_SCHEMA, TG_TABLE_NAME
              USING ERRCODE = 'insufficient_privilege';
          END IF;
          IF (to_jsonb(NEW) - mutable) IS DISTINCT FROM (to_jsonb(OLD) - mutable) THEN
            RAISE EXCEPTION '%.% rows are immutable', TG_TABLE_SCHEMA, TG_TABLE_NAME
              USING ERRCODE = 'insufficient_privilege';
          END IF;
          RETURN NEW;
        END
        $$;
        """
    )
    for table, mutable in IMMUTABLE_TABLES:
        args = ", ".join(f"'{c}'" for c in mutable)
        op.execute(
            f"CREATE TRIGGER {table.split('.')[-1]}_immutable BEFORE UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION lab.protect_immutable({args})"
        )
    op.execute(_EVIDENCE_TRIGGER_TEMPLATE.format(extra="\n     OR NEW.chain_scope IS DISTINCT FROM OLD.chain_scope"))

    # --- views --------------------------------------------------------------------------------------
    op.execute(
        """
        CREATE VIEW lab.tool_usage WITH (security_invoker = true) AS
        SELECT organization_id, project_id, mission_id, agent_run_id, tool_id, status, latency_ms, cost_usd,
               created_at
        FROM lab.tool_calls
        """
    )

    # --- search -------------------------------------------------------------------------------------
    for name, definition in FTS_INDEXES.items():
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {definition}")
    for name, definition in VECTOR_INDEXES.items():
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {definition}")

    # --- application role ---------------------------------------------------------------------------
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aegis_app') THEN
            GRANT USAGE ON SCHEMA lab TO aegis_app;
            GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA lab TO aegis_app;
            GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO aegis_app;
            GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA lab TO aegis_app;
            GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA lab TO aegis_app;
            ALTER DEFAULT PRIVILEGES IN SCHEMA lab GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO aegis_app;
            -- Global reference data is read-only for the application role.
            REVOKE INSERT, UPDATE, DELETE ON permissions, plans, lab.evaluators FROM aegis_app;
          END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(_EVIDENCE_TRIGGER_TEMPLATE.format(extra=""))
    for name in (*VECTOR_INDEXES, *FTS_INDEXES):
        op.execute(f"DROP INDEX IF EXISTS lab.{name}")
    op.execute("DROP VIEW IF EXISTS lab.tool_usage")
    for table, _ in IMMUTABLE_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table.split('.')[-1]}_immutable ON {table}")
    op.execute("DROP FUNCTION IF EXISTS lab.protect_immutable()")
    op.execute("DROP POLICY IF EXISTS role_permissions_isolation ON role_permissions")
    for table in GLOBAL_READABLE_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {_policy_name(table)}_read ON {table}")
        op.execute(f"DROP POLICY IF EXISTS {_policy_name(table)}_write ON {table}")
    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {_policy_name(table)}_tenant_isolation ON {table}")
    # Tables are dropped with their policies; lab evidence rows stay in the append-only evidence store.
    _drop_tables()
    op.execute("DROP SCHEMA IF EXISTS lab CASCADE")


def _create_tables() -> None:
    op.create_table(
        "evaluators",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=24), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("default_config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluators")),
        sa.UniqueConstraint("key", "version", name="uq_lab_evaluators_key_version"),
        schema="lab",
    )
    op.create_index(op.f("ix_lab_evaluators_created_at"), "evaluators", ["created_at"], unique=False, schema="lab")
    op.create_table(
        "permissions",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("description", sa.String(length=300), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_permissions")),
    )
    op.create_table(
        "plans",
        sa.Column("key", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("limits", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("price", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plans")),
        sa.UniqueConstraint("key", name=op.f("uq_plans_key")),
    )
    op.create_index(op.f("ix_plans_created_at"), "plans", ["created_at"], unique=False)
    op.create_table(
        "idempotency_keys",
        sa.Column("principal_key", sa.String(length=96), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("method", sa.String(length=8), nullable=False),
        sa.Column("path", sa.String(length=500), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_idempotency_keys_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_idempotency_keys")),
        sa.UniqueConstraint("organization_id", "principal_key", "key", name="uq_idempotency_keys_scope"),
    )
    op.create_index(op.f("ix_idempotency_keys_created_at"), "idempotency_keys", ["created_at"], unique=False)
    op.create_index("ix_idempotency_keys_expires", "idempotency_keys", ["expires_at"], unique=False)
    op.create_index(op.f("ix_idempotency_keys_organization_id"), "idempotency_keys", ["organization_id"], unique=False)
    op.create_table(
        "invoice_references",
        sa.Column("provider", sa.String(length=24), nullable=False),
        sa.Column("provider_invoice_id", sa.String(length=255), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("url", sa.String(length=1000), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_invoice_references_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invoice_references")),
        sa.UniqueConstraint("provider", "provider_invoice_id", name="uq_invoice_references_provider_id"),
    )
    op.create_index(op.f("ix_invoice_references_created_at"), "invoice_references", ["created_at"], unique=False)
    op.create_index(
        op.f("ix_invoice_references_organization_id"), "invoice_references", ["organization_id"], unique=False
    )
    op.create_table(
        "benchmark_runs",
        sa.Column("suite_key", sa.String(length=64), nullable=False),
        sa.Column("suite_version", sa.String(length=16), nullable=False),
        sa.Column("subject_kind", sa.String(length=16), nullable=False),
        sa.Column("subject_ref", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("n_cases", sa.Integer(), nullable=False),
        sa.Column("mean_score", sa.Float(), nullable=True),
        sa.Column("pass_rate", sa.Float(), nullable=True),
        sa.Column("comparison", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_benchmark_runs_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_benchmark_runs")),
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_benchmark_runs_created_at"), "benchmark_runs", ["created_at"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_benchmark_runs_organization_id"), "benchmark_runs", ["organization_id"], unique=False, schema="lab"
    )
    op.create_index(
        "ix_lab_benchmark_runs_suite_subject",
        "benchmark_runs",
        ["suite_key", "subject_ref"],
        unique=False,
        schema="lab",
    )
    op.create_table(
        "environments",
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("image", sa.String(length=500), nullable=False),
        sa.Column("image_digest", sa.String(length=100), nullable=True),
        sa.Column("packages", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("lockfile_sha256", sa.String(length=64), nullable=True),
        sa.Column("runtime", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_environments_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_environments")),
        sa.UniqueConstraint("organization_id", "name", name="uq_lab_environments_org_name"),
        schema="lab",
    )
    op.create_index(op.f("ix_lab_environments_created_at"), "environments", ["created_at"], unique=False, schema="lab")
    op.create_index(
        op.f("ix_lab_environments_organization_id"), "environments", ["organization_id"], unique=False, schema="lab"
    )
    op.create_table(
        "model_configs",
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=160), nullable=False),
        sa.Column("tier", sa.String(length=16), nullable=False),
        sa.Column("features", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("input_per_mtok", sa.Float(), nullable=True),
        sa.Column("output_per_mtok", sa.Float(), nullable=True),
        sa.Column("typical_latency_ms", sa.Integer(), nullable=True),
        sa.Column("is_agent", sa.Boolean(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_model_configs_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_configs")),
        sa.UniqueConstraint("organization_id", "provider", "model", name="uq_lab_model_configs_org_model"),
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_model_configs_created_at"), "model_configs", ["created_at"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_model_configs_organization_id"), "model_configs", ["organization_id"], unique=False, schema="lab"
    )
    op.create_table(
        "workflow_runs",
        sa.Column("workflow", sa.String(length=64), nullable=False),
        sa.Column("business_key", sa.String(length=160), nullable=False),
        sa.Column("engine", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("input", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("waiting_on", sa.String(length=160), nullable=True),
        sa.Column("wake_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=160), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("parent_run_id", sa.Uuid(), nullable=True),
        sa.Column("temporal_workflow_id", sa.String(length=255), nullable=True),
        sa.Column("temporal_run_id", sa.String(length=255), nullable=True),
        sa.Column("principal", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_workflow_runs_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_run_id"],
            ["lab.workflow_runs.id"],
            name=op.f("fk_workflow_runs_parent_run_id_workflow_runs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workflow_runs")),
        sa.UniqueConstraint("organization_id", "workflow", "business_key", name="uq_lab_workflow_runs_business_key"),
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_workflow_runs_created_at"), "workflow_runs", ["created_at"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_workflow_runs_organization_id"), "workflow_runs", ["organization_id"], unique=False, schema="lab"
    )
    op.create_index(
        "ix_lab_workflow_runs_status_lease", "workflow_runs", ["status", "lease_until"], unique=False, schema="lab"
    )
    op.create_index(
        "ix_lab_workflow_runs_status_wake", "workflow_runs", ["status", "wake_at"], unique=False, schema="lab"
    )
    op.create_table(
        "organization_quotas",
        sa.Column("max_agents", sa.Integer(), nullable=True),
        sa.Column("max_concurrent_experiments", sa.Integer(), nullable=True),
        sa.Column("max_llm_spend_usd", sa.Float(), nullable=True),
        sa.Column("max_compute_spend_usd", sa.Float(), nullable=True),
        sa.Column("max_storage_bytes", sa.BigInteger(), nullable=True),
        sa.Column("max_research_jobs", sa.Integer(), nullable=True),
        sa.Column("max_autonomy_level", sa.String(length=40), nullable=False),
        sa.Column("expensive_compute_threshold_usd", sa.Float(), nullable=False),
        sa.Column("allow_auto_strategy_promotion", sa.Boolean(), nullable=False),
        sa.Column("allow_external_models", sa.Boolean(), nullable=False),
        sa.Column("retention", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_organization_quotas_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organization_quotas")),
        sa.UniqueConstraint("organization_id", name="uq_organization_quotas_org"),
    )
    op.create_index(op.f("ix_organization_quotas_created_at"), "organization_quotas", ["created_at"], unique=False)
    op.create_index(
        op.f("ix_organization_quotas_organization_id"), "organization_quotas", ["organization_id"], unique=False
    )
    op.create_table(
        "roles",
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("key", sa.String(length=48), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_system", sa.Boolean(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_roles_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_roles")),
        sa.UniqueConstraint("organization_id", "key", name="uq_roles_org_key"),
    )
    op.create_index(op.f("ix_roles_created_at"), "roles", ["created_at"], unique=False)
    op.create_index(op.f("ix_roles_organization_id"), "roles", ["organization_id"], unique=False)
    op.create_table(
        "subscriptions",
        sa.Column("plan_key", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("provider", sa.String(length=24), nullable=False),
        sa.Column("provider_ref", sa.String(length=255), nullable=True),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_subscriptions_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_subscriptions")),
        sa.UniqueConstraint("organization_id", name="uq_subscriptions_org"),
    )
    op.create_index(op.f("ix_subscriptions_created_at"), "subscriptions", ["created_at"], unique=False)
    op.create_index(op.f("ix_subscriptions_organization_id"), "subscriptions", ["organization_id"], unique=False)
    op.create_table(
        "teams",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_teams_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_teams")),
        sa.UniqueConstraint("organization_id", "name", name="uq_teams_org_name"),
    )
    op.create_index(op.f("ix_teams_created_at"), "teams", ["created_at"], unique=False)
    op.create_index(op.f("ix_teams_organization_id"), "teams", ["organization_id"], unique=False)
    op.create_table(
        "usage_records",
        sa.Column("metric", sa.String(length=48), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(length=24), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_ref", sa.String(length=255), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_usage_records_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_records")),
        sa.UniqueConstraint("organization_id", "metric", "period_start", name="uq_usage_records_org_metric_period"),
    )
    op.create_index(op.f("ix_usage_records_created_at"), "usage_records", ["created_at"], unique=False)
    op.create_index("ix_usage_records_org_period", "usage_records", ["organization_id", "period_start"], unique=False)
    op.create_index(op.f("ix_usage_records_organization_id"), "usage_records", ["organization_id"], unique=False)
    op.create_table(
        "benchmark_results",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.String(length=80), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("output_sha256", sa.String(length=64), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_benchmark_results_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["lab.benchmark_runs.id"],
            name=op.f("fk_benchmark_results_run_id_benchmark_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_benchmark_results")),
        sa.UniqueConstraint("run_id", "case_id", name="uq_lab_benchmark_results_run_case"),
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_benchmark_results_created_at"), "benchmark_results", ["created_at"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_benchmark_results_organization_id"),
        "benchmark_results",
        ["organization_id"],
        unique=False,
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_benchmark_results_run_id"), "benchmark_results", ["run_id"], unique=False, schema="lab"
    )
    op.create_table(
        "policies",
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("current_version_id", sa.Uuid(), nullable=True),
        sa.Column("version_count", sa.Integer(), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], name=op.f("fk_policies_created_by_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_policies_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_policies")),
        sa.UniqueConstraint("organization_id", "key", name="uq_lab_policies_org_key"),
        schema="lab",
    )
    op.create_index(op.f("ix_lab_policies_created_at"), "policies", ["created_at"], unique=False, schema="lab")
    op.create_index(
        op.f("ix_lab_policies_organization_id"), "policies", ["organization_id"], unique=False, schema="lab"
    )
    op.create_table(
        "prompt_templates",
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("version", sa.String(length=24), nullable=False),
        sa.Column("task_type", sa.String(length=40), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("template", sa.Text(), nullable=False),
        sa.Column("variables", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], name=op.f("fk_prompt_templates_created_by_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_prompt_templates_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prompt_templates")),
        sa.UniqueConstraint("organization_id", "name", "version", name="uq_lab_prompt_templates_org_name_version"),
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_prompt_templates_created_at"), "prompt_templates", ["created_at"], unique=False, schema="lab"
    )
    op.create_index(op.f("ix_lab_prompt_templates_name"), "prompt_templates", ["name"], unique=False, schema="lab")
    op.create_index(
        op.f("ix_lab_prompt_templates_organization_id"),
        "prompt_templates",
        ["organization_id"],
        unique=False,
        schema="lab",
    )
    op.create_table(
        "workflow_signals",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_workflow_signals_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["lab.workflow_runs.id"],
            name=op.f("fk_workflow_signals_run_id_workflow_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workflow_signals")),
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_workflow_signals_created_at"), "workflow_signals", ["created_at"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_workflow_signals_organization_id"),
        "workflow_signals",
        ["organization_id"],
        unique=False,
        schema="lab",
    )
    op.create_index(
        "ix_lab_workflow_signals_run_name", "workflow_signals", ["run_id", "name"], unique=False, schema="lab"
    )
    op.create_table(
        "workflow_steps",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("step_key", sa.String(length=255), nullable=False),
        sa.Column("activity", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_workflow_steps_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["lab.workflow_runs.id"],
            name=op.f("fk_workflow_steps_run_id_workflow_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workflow_steps")),
        sa.UniqueConstraint("run_id", "step_key", name="uq_lab_workflow_steps_run_key"),
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_workflow_steps_created_at"), "workflow_steps", ["created_at"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_workflow_steps_organization_id"), "workflow_steps", ["organization_id"], unique=False, schema="lab"
    )
    op.create_index(op.f("ix_lab_workflow_steps_run_id"), "workflow_steps", ["run_id"], unique=False, schema="lab")
    op.create_table(
        "refresh_tokens",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("family_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=256), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_refresh_tokens_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_refresh_tokens_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_refresh_tokens_token_hash")),
    )
    op.create_index(op.f("ix_refresh_tokens_created_at"), "refresh_tokens", ["created_at"], unique=False)
    op.create_index("ix_refresh_tokens_family", "refresh_tokens", ["family_id"], unique=False)
    op.create_index(op.f("ix_refresh_tokens_user_id"), "refresh_tokens", ["user_id"], unique=False)
    op.create_table(
        "role_permissions",
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("permission_key", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["permission_key"],
            ["permissions.key"],
            name=op.f("fk_role_permissions_permission_key_permissions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"], ["roles.id"], name=op.f("fk_role_permissions_role_id_roles"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_role_permissions")),
        sa.UniqueConstraint("role_id", "permission_key", name="uq_role_permissions_role_perm"),
    )
    op.create_index(op.f("ix_role_permissions_permission_key"), "role_permissions", ["permission_key"], unique=False)
    op.create_index(op.f("ix_role_permissions_role_id"), "role_permissions", ["role_id"], unique=False)
    op.create_table(
        "service_accounts",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("scopes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("project_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], name=op.f("fk_service_accounts_created_by_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_service_accounts_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_service_accounts")),
        sa.UniqueConstraint("organization_id", "name", name="uq_service_accounts_org_name"),
    )
    op.create_index(op.f("ix_service_accounts_created_at"), "service_accounts", ["created_at"], unique=False)
    op.create_index(op.f("ix_service_accounts_organization_id"), "service_accounts", ["organization_id"], unique=False)
    op.create_table(
        "team_members",
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_team_members_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.id"], name=op.f("fk_team_members_team_id_teams"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_team_members_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_team_members")),
        sa.UniqueConstraint("team_id", "user_id", name="uq_team_members_team_user"),
    )
    op.create_index(op.f("ix_team_members_created_at"), "team_members", ["created_at"], unique=False)
    op.create_index(op.f("ix_team_members_organization_id"), "team_members", ["organization_id"], unique=False)
    op.create_index(op.f("ix_team_members_team_id"), "team_members", ["team_id"], unique=False)
    op.create_index(op.f("ix_team_members_user_id"), "team_members", ["user_id"], unique=False)
    op.create_table(
        "workspaces",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("is_demo", sa.Boolean(), nullable=False),
        sa.Column("settings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], name=op.f("fk_workspaces_created_by_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_workspaces_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspaces")),
        sa.UniqueConstraint("organization_id", "slug", name="uq_workspaces_org_slug"),
    )
    op.create_index(op.f("ix_workspaces_created_at"), "workspaces", ["created_at"], unique=False)
    op.create_index(op.f("ix_workspaces_organization_id"), "workspaces", ["organization_id"], unique=False)
    op.create_table(
        "mcp_servers",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("endpoint", sa.String(length=1000), nullable=False),
        sa.Column("transport", sa.String(length=24), nullable=False),
        sa.Column("auth_method", sa.String(length=16), nullable=False),
        sa.Column("auth_header", sa.String(length=80), nullable=True),
        sa.Column("secret_id", sa.Uuid(), nullable=True),
        sa.Column("allowed_tools", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("project_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("risk_level", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("server_info", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("protocol_version", sa.String(length=24), nullable=True),
        sa.Column("last_health_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_health_status", sa.String(length=160), nullable=True),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("approved_by_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["approved_by_id"], ["users.id"], name=op.f("fk_mcp_servers_approved_by_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], name=op.f("fk_mcp_servers_created_by_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_mcp_servers_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["secret_id"], ["secrets.id"], name=op.f("fk_mcp_servers_secret_id_secrets"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mcp_servers")),
        sa.UniqueConstraint("organization_id", "name", name="uq_lab_mcp_servers_org_name"),
        schema="lab",
    )
    op.create_index(op.f("ix_lab_mcp_servers_created_at"), "mcp_servers", ["created_at"], unique=False, schema="lab")
    op.create_index(
        op.f("ix_lab_mcp_servers_organization_id"), "mcp_servers", ["organization_id"], unique=False, schema="lab"
    )
    op.create_table(
        "policy_versions",
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("document", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("change_note", sa.Text(), nullable=True),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], name=op.f("fk_policy_versions_created_by_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_policy_versions_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["policy_id"], ["lab.policies.id"], name=op.f("fk_policy_versions_policy_id_policies"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_policy_versions")),
        sa.UniqueConstraint("policy_id", "version", name="uq_lab_policy_versions_policy_version"),
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_policy_versions_created_at"), "policy_versions", ["created_at"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_policy_versions_organization_id"),
        "policy_versions",
        ["organization_id"],
        unique=False,
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_policy_versions_policy_id"), "policy_versions", ["policy_id"], unique=False, schema="lab"
    )
    op.create_table(
        "projects",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("slug", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("domain", sa.String(length=48), nullable=False),
        sa.Column("visibility", sa.String(length=16), nullable=False),
        sa.Column("budget", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("verification_criteria", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("settings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_demo", sa.Boolean(), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], name=op.f("fk_projects_created_by_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_projects_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_projects_workspace_id_workspaces"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_projects")),
        sa.UniqueConstraint("workspace_id", "slug", name="uq_projects_workspace_slug"),
    )
    op.create_index(op.f("ix_projects_created_at"), "projects", ["created_at"], unique=False)
    op.create_index("ix_projects_org_created", "projects", ["organization_id", "created_at"], unique=False)
    op.create_index(op.f("ix_projects_organization_id"), "projects", ["organization_id"], unique=False)
    op.create_index(op.f("ix_projects_workspace_id"), "projects", ["workspace_id"], unique=False)
    op.create_table(
        "sso_connections",
        sa.Column("protocol", sa.String(length=8), nullable=False),
        sa.Column("issuer", sa.String(length=500), nullable=False),
        sa.Column("client_id", sa.String(length=255), nullable=False),
        sa.Column("client_secret_id", sa.Uuid(), nullable=True),
        sa.Column("email_domains", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("default_role", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["client_secret_id"],
            ["secrets.id"],
            name=op.f("fk_sso_connections_client_secret_id_secrets"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_sso_connections_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sso_connections")),
        sa.UniqueConstraint("organization_id", "issuer", name="uq_sso_connections_org_issuer"),
    )
    op.create_index(op.f("ix_sso_connections_created_at"), "sso_connections", ["created_at"], unique=False)
    op.create_index(op.f("ix_sso_connections_organization_id"), "sso_connections", ["organization_id"], unique=False)
    op.create_table(
        "workspace_members",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=24), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_workspace_members_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_workspace_members_user_id_users"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_workspace_members_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspace_members")),
        sa.UniqueConstraint("workspace_id", "user_id", name="uq_workspace_members_ws_user"),
    )
    op.create_index(op.f("ix_workspace_members_created_at"), "workspace_members", ["created_at"], unique=False)
    op.create_index(
        op.f("ix_workspace_members_organization_id"), "workspace_members", ["organization_id"], unique=False
    )
    op.create_index(op.f("ix_workspace_members_user_id"), "workspace_members", ["user_id"], unique=False)
    op.create_index(op.f("ix_workspace_members_workspace_id"), "workspace_members", ["workspace_id"], unique=False)
    op.create_table(
        "agents",
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("current_version_id", sa.Uuid(), nullable=True),
        sa.Column("is_demo", sa.Boolean(), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], name=op.f("fk_agents_created_by_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_agents_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_agents_project_id_projects"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agents")),
        sa.UniqueConstraint("organization_id", "name", name="uq_lab_agents_org_name"),
        schema="lab",
    )
    op.create_index(op.f("ix_lab_agents_created_at"), "agents", ["created_at"], unique=False, schema="lab")
    op.create_index(op.f("ix_lab_agents_organization_id"), "agents", ["organization_id"], unique=False, schema="lab")
    op.create_index(op.f("ix_lab_agents_project_id"), "agents", ["project_id"], unique=False, schema="lab")
    op.create_index(op.f("ix_lab_agents_role"), "agents", ["role"], unique=False, schema="lab")
    op.create_table(
        "datasets",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("license", sa.String(length=200), nullable=True),
        sa.Column("source", sa.String(length=1000), nullable=True),
        sa.Column("current_version_id", sa.Uuid(), nullable=True),
        sa.Column("is_demo", sa.Boolean(), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], name=op.f("fk_datasets_created_by_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_datasets_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_datasets_project_id_projects"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_datasets")),
        sa.UniqueConstraint("project_id", "name", name="uq_lab_datasets_project_name"),
        schema="lab",
    )
    op.create_index(op.f("ix_lab_datasets_created_at"), "datasets", ["created_at"], unique=False, schema="lab")
    op.create_index(
        op.f("ix_lab_datasets_organization_id"), "datasets", ["organization_id"], unique=False, schema="lab"
    )
    op.create_index(op.f("ix_lab_datasets_project_id"), "datasets", ["project_id"], unique=False, schema="lab")
    op.create_table(
        "documents",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("doc_type", sa.String(length=24), nullable=False),
        sa.Column("source_kind", sa.String(length=24), nullable=False),
        sa.Column("source_url", sa.String(length=2000), nullable=True),
        sa.Column("filename", sa.String(length=255), nullable=True),
        sa.Column("current_version_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], name=op.f("fk_documents_created_by_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_documents_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_documents_project_id_projects"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
        schema="lab",
    )
    op.create_index(op.f("ix_lab_documents_created_at"), "documents", ["created_at"], unique=False, schema="lab")
    op.create_index(
        op.f("ix_lab_documents_organization_id"), "documents", ["organization_id"], unique=False, schema="lab"
    )
    op.create_index(
        "ix_lab_documents_project_created", "documents", ["project_id", "created_at"], unique=False, schema="lab"
    )
    op.create_index(op.f("ix_lab_documents_project_id"), "documents", ["project_id"], unique=False, schema="lab")
    op.create_table(
        "graph_nodes",
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("node_type", sa.String(length=24), nullable=False),
        sa.Column("key", sa.String(length=300), nullable=False),
        sa.Column("label", sa.String(length=500), nullable=False),
        sa.Column("ref_type", sa.String(length=32), nullable=True),
        sa.Column("ref_id", sa.String(length=64), nullable=True),
        sa.Column("properties", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_graph_nodes_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_graph_nodes_project_id_projects"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_graph_nodes")),
        sa.UniqueConstraint("organization_id", "node_type", "key", name="uq_lab_graph_nodes_type_key"),
        schema="lab",
    )
    op.create_index(op.f("ix_lab_graph_nodes_created_at"), "graph_nodes", ["created_at"], unique=False, schema="lab")
    op.create_index(
        op.f("ix_lab_graph_nodes_organization_id"), "graph_nodes", ["organization_id"], unique=False, schema="lab"
    )
    op.create_index(op.f("ix_lab_graph_nodes_project_id"), "graph_nodes", ["project_id"], unique=False, schema="lab")
    op.create_index("ix_lab_graph_nodes_ref", "graph_nodes", ["ref_type", "ref_id"], unique=False, schema="lab")
    op.create_table(
        "lessons",
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("signature", sa.String(length=80), nullable=False),
        sa.Column("failure_type", sa.String(length=40), nullable=False),
        sa.Column("lesson", sa.Text(), nullable=False),
        sa.Column("context", sa.Text(), nullable=True),
        sa.Column("recovery_kind", sa.String(length=48), nullable=False),
        sa.Column("resolved", sa.Boolean(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("occurrences", sa.Integer(), nullable=False),
        sa.Column("memory_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_lessons_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_lessons_project_id_projects"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lessons")),
        sa.UniqueConstraint("organization_id", "signature", "recovery_kind", name="uq_lab_lessons_sig_recovery"),
        schema="lab",
    )
    op.create_index(op.f("ix_lab_lessons_created_at"), "lessons", ["created_at"], unique=False, schema="lab")
    op.create_index(op.f("ix_lab_lessons_organization_id"), "lessons", ["organization_id"], unique=False, schema="lab")
    op.create_table(
        "mcp_tools",
        sa.Column("server_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("input_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("schema_sha256", sa.String(length=64), nullable=False),
        sa.Column("risk_level", sa.String(length=16), nullable=False),
        sa.Column("approved", sa.Boolean(), nullable=False),
        sa.Column("approved_by_id", sa.Uuid(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["approved_by_id"], ["users.id"], name=op.f("fk_mcp_tools_approved_by_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_mcp_tools_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["server_id"], ["lab.mcp_servers.id"], name=op.f("fk_mcp_tools_server_id_mcp_servers"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mcp_tools")),
        sa.UniqueConstraint("server_id", "name", name="uq_lab_mcp_tools_server_name"),
        schema="lab",
    )
    op.create_index(op.f("ix_lab_mcp_tools_created_at"), "mcp_tools", ["created_at"], unique=False, schema="lab")
    op.create_index(
        op.f("ix_lab_mcp_tools_organization_id"), "mcp_tools", ["organization_id"], unique=False, schema="lab"
    )
    op.create_index(op.f("ix_lab_mcp_tools_server_id"), "mcp_tools", ["server_id"], unique=False, schema="lab")
    op.create_table(
        "missions",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("domain", sa.String(length=48), nullable=False),
        sa.Column("constraints", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("success_criteria", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("budget", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("compute_budget", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("time_budget_seconds", sa.Integer(), nullable=True),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("allowed_tools", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("risk_level", sa.String(length=16), nullable=False),
        sa.Column("autonomy_level", sa.String(length=40), nullable=False),
        sa.Column("approval_policy", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("phase", sa.String(length=32), nullable=True),
        sa.Column("status_reason", sa.Text(), nullable=True),
        sa.Column("current_cycle", sa.Integer(), nullable=False),
        sa.Column("max_cycles", sa.Integer(), nullable=False),
        sa.Column("strategy_version_id", sa.Uuid(), nullable=True),
        sa.Column("brief", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("plan", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("workflow_run_id", sa.Uuid(), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("evidence_head_hash", sa.String(length=64), nullable=True),
        sa.Column("evidence_seq", sa.Integer(), nullable=False),
        sa.Column("event_seq", sa.Integer(), nullable=False),
        sa.Column("is_demo", sa.Boolean(), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], name=op.f("fk_missions_created_by_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_missions_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_missions_project_id_projects"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_missions_workspace_id_workspaces"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_missions")),
        schema="lab",
    )
    op.create_index(op.f("ix_lab_missions_created_at"), "missions", ["created_at"], unique=False, schema="lab")
    op.create_index("ix_lab_missions_org_status", "missions", ["organization_id", "status"], unique=False, schema="lab")
    op.create_index(
        op.f("ix_lab_missions_organization_id"), "missions", ["organization_id"], unique=False, schema="lab"
    )
    op.create_index(
        "ix_lab_missions_project_created", "missions", ["project_id", "created_at"], unique=False, schema="lab"
    )
    op.create_index(op.f("ix_lab_missions_project_id"), "missions", ["project_id"], unique=False, schema="lab")
    op.create_index(op.f("ix_lab_missions_status"), "missions", ["status"], unique=False, schema="lab")
    op.create_index(op.f("ix_lab_missions_workspace_id"), "missions", ["workspace_id"], unique=False, schema="lab")
    op.create_table(
        "strategies",
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("promoted_version_id", sa.Uuid(), nullable=True),
        sa.Column("version_count", sa.Integer(), nullable=False),
        sa.Column("is_demo", sa.Boolean(), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], name=op.f("fk_strategies_created_by_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_strategies_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_strategies_project_id_projects"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_strategies")),
        sa.UniqueConstraint("organization_id", "name", name="uq_lab_strategies_org_name"),
        schema="lab",
    )
    op.create_index(op.f("ix_lab_strategies_created_at"), "strategies", ["created_at"], unique=False, schema="lab")
    op.create_index(
        op.f("ix_lab_strategies_organization_id"), "strategies", ["organization_id"], unique=False, schema="lab"
    )
    op.create_index(op.f("ix_lab_strategies_project_id"), "strategies", ["project_id"], unique=False, schema="lab")
    op.create_table(
        "project_members",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=24), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_project_members_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_project_members_project_id_projects"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_project_members_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_project_members")),
        sa.UniqueConstraint("project_id", "user_id", name="uq_project_members_project_user"),
    )
    op.create_index(op.f("ix_project_members_created_at"), "project_members", ["created_at"], unique=False)
    op.create_index(op.f("ix_project_members_organization_id"), "project_members", ["organization_id"], unique=False)
    op.create_index(op.f("ix_project_members_project_id"), "project_members", ["project_id"], unique=False)
    op.create_index(op.f("ix_project_members_user_id"), "project_members", ["user_id"], unique=False)
    op.create_table(
        "agent_versions",
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("config_sha256", sa.String(length=64), nullable=False),
        sa.Column("change_note", sa.Text(), nullable=True),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["lab.agents.id"], name=op.f("fk_agent_versions_agent_id_agents"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], name=op.f("fk_agent_versions_created_by_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_agent_versions_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_versions")),
        sa.UniqueConstraint("agent_id", "version", name="uq_lab_agent_versions_agent_version"),
        schema="lab",
    )
    op.create_index(op.f("ix_lab_agent_versions_agent_id"), "agent_versions", ["agent_id"], unique=False, schema="lab")
    op.create_index(
        op.f("ix_lab_agent_versions_created_at"), "agent_versions", ["created_at"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_agent_versions_organization_id"), "agent_versions", ["organization_id"], unique=False, schema="lab"
    )
    op.create_table(
        "approvals",
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("mission_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("resource_type", sa.String(length=32), nullable=False),
        sa.Column("resource_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("request", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("requester_type", sa.String(length=24), nullable=False),
        sa.Column("requester_id", sa.String(length=64), nullable=True),
        sa.Column("requester_label", sa.String(length=320), nullable=True),
        sa.Column("approver_id", sa.Uuid(), nullable=True),
        sa.Column("required_permission", sa.String(length=64), nullable=False),
        sa.Column("policy", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("workflow_run_id", sa.Uuid(), nullable=True),
        sa.Column("signal_name", sa.String(length=160), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["approver_id"], ["users.id"], name=op.f("fk_approvals_approver_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["lab.missions.id"], name=op.f("fk_approvals_mission_id_missions"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_approvals_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_approvals_project_id_projects"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approvals")),
        schema="lab",
    )
    op.create_index(op.f("ix_lab_approvals_created_at"), "approvals", ["created_at"], unique=False, schema="lab")
    op.create_index(op.f("ix_lab_approvals_mission_id"), "approvals", ["mission_id"], unique=False, schema="lab")
    op.create_index(
        "ix_lab_approvals_org_status", "approvals", ["organization_id", "status"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_approvals_organization_id"), "approvals", ["organization_id"], unique=False, schema="lab"
    )
    op.create_index(op.f("ix_lab_approvals_project_id"), "approvals", ["project_id"], unique=False, schema="lab")
    op.create_index(
        "ix_lab_approvals_resource", "approvals", ["resource_type", "resource_id"], unique=False, schema="lab"
    )
    op.create_table(
        "dataset_versions",
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("parent_version_id", sa.Uuid(), nullable=True),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("schema", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("license", sa.String(length=200), nullable=True),
        sa.Column("source", sa.String(length=1000), nullable=True),
        sa.Column("transformations", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("files", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("storage_prefix", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], name=op.f("fk_dataset_versions_created_by_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["lab.datasets.id"],
            name=op.f("fk_dataset_versions_dataset_id_datasets"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_dataset_versions_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_version_id"],
            ["lab.dataset_versions.id"],
            name=op.f("fk_dataset_versions_parent_version_id_dataset_versions"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dataset_versions")),
        sa.UniqueConstraint("dataset_id", "checksum", name="uq_lab_dataset_versions_ds_checksum"),
        sa.UniqueConstraint("dataset_id", "version", name="uq_lab_dataset_versions_ds_version"),
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_dataset_versions_created_at"), "dataset_versions", ["created_at"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_dataset_versions_dataset_id"), "dataset_versions", ["dataset_id"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_dataset_versions_organization_id"),
        "dataset_versions",
        ["organization_id"],
        unique=False,
        schema="lab",
    )
    op.create_table(
        "document_versions",
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=True),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("entities", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("injection_report", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("scan_status", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["lab.documents.id"],
            name=op.f("fk_document_versions_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_document_versions_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_versions")),
        sa.UniqueConstraint("document_id", "sha256", name="uq_lab_document_versions_doc_sha"),
        sa.UniqueConstraint("document_id", "version", name="uq_lab_document_versions_doc_version"),
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_document_versions_created_at"), "document_versions", ["created_at"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_document_versions_document_id"), "document_versions", ["document_id"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_document_versions_organization_id"),
        "document_versions",
        ["organization_id"],
        unique=False,
        schema="lab",
    )
    op.create_table(
        "events",
        sa.Column("mission_id", sa.Uuid(), nullable=True),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("level", sa.String(length=12), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("actor", sa.String(length=160), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["lab.missions.id"], name=op.f("fk_events_mission_id_missions"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_events_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_events_project_id_projects"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_events")),
        sa.UniqueConstraint("mission_id", "seq", name="uq_lab_events_mission_seq"),
        schema="lab",
    )
    op.create_index(op.f("ix_lab_events_created_at"), "events", ["created_at"], unique=False, schema="lab")
    op.create_index(
        "ix_lab_events_org_created", "events", ["organization_id", "created_at"], unique=False, schema="lab"
    )
    op.create_index(op.f("ix_lab_events_organization_id"), "events", ["organization_id"], unique=False, schema="lab")
    op.create_index("ix_lab_events_type", "events", ["event_type"], unique=False, schema="lab")
    op.create_table(
        "evolution_runs",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("mission_id", sa.Uuid(), nullable=True),
        sa.Column("strategy_id", sa.Uuid(), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("max_generations", sa.Integer(), nullable=False),
        sa.Column("archive_version_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("generations", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("workflow_run_id", sa.Uuid(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["lab.missions.id"], name=op.f("fk_evolution_runs_mission_id_missions"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_evolution_runs_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_evolution_runs_project_id_projects"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["strategy_id"],
            ["lab.strategies.id"],
            name=op.f("fk_evolution_runs_strategy_id_strategies"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evolution_runs")),
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_evolution_runs_created_at"), "evolution_runs", ["created_at"], unique=False, schema="lab"
    )
    op.create_index(
        "ix_lab_evolution_runs_mission", "evolution_runs", ["mission_id", "created_at"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_evolution_runs_organization_id"), "evolution_runs", ["organization_id"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_evolution_runs_project_id"), "evolution_runs", ["project_id"], unique=False, schema="lab"
    )
    op.create_table(
        "graph_edges",
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("relation", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("created_by", sa.String(length=24), nullable=False),
        sa.Column("evidence_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("properties", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_graph_edges_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["lab.graph_nodes.id"], name=op.f("fk_graph_edges_source_id_graph_nodes"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["target_id"], ["lab.graph_nodes.id"], name=op.f("fk_graph_edges_target_id_graph_nodes"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_graph_edges")),
        sa.UniqueConstraint("source_id", "target_id", "relation", name="uq_lab_graph_edges_triple"),
        schema="lab",
    )
    op.create_index(op.f("ix_lab_graph_edges_created_at"), "graph_edges", ["created_at"], unique=False, schema="lab")
    op.create_index(
        op.f("ix_lab_graph_edges_organization_id"), "graph_edges", ["organization_id"], unique=False, schema="lab"
    )
    op.create_index(op.f("ix_lab_graph_edges_source_id"), "graph_edges", ["source_id"], unique=False, schema="lab")
    op.create_index("ix_lab_graph_edges_target", "graph_edges", ["target_id"], unique=False, schema="lab")
    op.create_table(
        "memories",
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("mission_id", sa.Uuid(), nullable=True),
        sa.Column("owner_user_id", sa.Uuid(), nullable=True),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=24), nullable=False),
        sa.Column("source_ref", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("provenance", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("requires_review", sa.Boolean(), nullable=False),
        sa.Column("reviewed_by_id", sa.Uuid(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sensitivity", sa.String(length=16), nullable=False),
        sa.Column("injection_score", sa.Float(), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.vector.VECTOR(dim=768), nullable=True),
        sa.Column("embedding_model", sa.String(length=120), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["lab.missions.id"], name=op.f("fk_memories_mission_id_missions"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_memories_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["users.id"], name=op.f("fk_memories_owner_user_id_users"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_memories_project_id_projects"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_id"], ["users.id"], name=op.f("fk_memories_reviewed_by_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"], ["lab.memories.id"], name=op.f("fk_memories_supersedes_id_memories"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memories")),
        schema="lab",
    )
    op.create_index(op.f("ix_lab_memories_created_at"), "memories", ["created_at"], unique=False, schema="lab")
    op.create_index(op.f("ix_lab_memories_mission_id"), "memories", ["mission_id"], unique=False, schema="lab")
    op.create_index(
        op.f("ix_lab_memories_organization_id"), "memories", ["organization_id"], unique=False, schema="lab"
    )
    op.create_index("ix_lab_memories_project", "memories", ["project_id", "created_at"], unique=False, schema="lab")
    op.create_index(
        "ix_lab_memories_scope",
        "memories",
        ["organization_id", "scope", "category", "status"],
        unique=False,
        schema="lab",
    )
    op.create_table(
        "mission_versions",
        sa.Column("mission_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("change_summary", sa.Text(), nullable=True),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], name=op.f("fk_mission_versions_created_by_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"],
            ["lab.missions.id"],
            name=op.f("fk_mission_versions_mission_id_missions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_mission_versions_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mission_versions")),
        sa.UniqueConstraint("mission_id", "version", name="uq_lab_mission_versions_mission_version"),
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_mission_versions_created_at"), "mission_versions", ["created_at"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_mission_versions_mission_id"), "mission_versions", ["mission_id"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_mission_versions_organization_id"),
        "mission_versions",
        ["organization_id"],
        unique=False,
        schema="lab",
    )
    op.create_table(
        "reports",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("mission_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("sections", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("markdown_artifact_id", sa.Uuid(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("cited_evidence_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("citation_check", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("narrative_run_id", sa.Uuid(), nullable=True),
        sa.Column("generated_by", sa.String(length=160), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["lab.missions.id"], name=op.f("fk_reports_mission_id_missions"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_reports_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_reports_project_id_projects"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reports")),
        schema="lab",
    )
    op.create_index(op.f("ix_lab_reports_created_at"), "reports", ["created_at"], unique=False, schema="lab")
    op.create_index("ix_lab_reports_mission", "reports", ["mission_id", "created_at"], unique=False, schema="lab")
    op.create_index(op.f("ix_lab_reports_organization_id"), "reports", ["organization_id"], unique=False, schema="lab")
    op.create_index(op.f("ix_lab_reports_project_id"), "reports", ["project_id"], unique=False, schema="lab")
    op.create_table(
        "research_tasks",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("mission_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("mode", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("require_plan_approval", sa.Boolean(), nullable=False),
        sa.Column("plan", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("plan_approved_by_id", sa.Uuid(), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("provider_agent", sa.String(length=120), nullable=True),
        sa.Column("provider_interaction_id", sa.String(length=255), nullable=True),
        sa.Column("provider_status", sa.String(length=32), nullable=True),
        sa.Column("last_event_id", sa.String(length=255), nullable=True),
        sa.Column("event_seq", sa.Integer(), nullable=False),
        sa.Column("report", sa.Text(), nullable=True),
        sa.Column("report_artifact_id", sa.Uuid(), nullable=True),
        sa.Column("citations_count", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("workflow_run_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], name=op.f("fk_research_tasks_created_by_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["lab.missions.id"], name=op.f("fk_research_tasks_mission_id_missions"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_research_tasks_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["plan_approved_by_id"],
            ["users.id"],
            name=op.f("fk_research_tasks_plan_approved_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_research_tasks_project_id_projects"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_research_tasks")),
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_research_tasks_created_at"), "research_tasks", ["created_at"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_research_tasks_mission_id"), "research_tasks", ["mission_id"], unique=False, schema="lab"
    )
    op.create_index(
        "ix_lab_research_tasks_org_status", "research_tasks", ["organization_id", "status"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_research_tasks_organization_id"), "research_tasks", ["organization_id"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_research_tasks_project_id"), "research_tasks", ["project_id"], unique=False, schema="lab"
    )
    op.create_table(
        "storage_usage",
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("mission_id", sa.Uuid(), nullable=True),
        sa.Column("object_kind", sa.String(length=24), nullable=False),
        sa.Column("object_id", sa.String(length=64), nullable=False),
        sa.Column("operation", sa.String(length=8), nullable=False),
        sa.Column("bytes", sa.BigInteger(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["lab.missions.id"], name=op.f("fk_storage_usage_mission_id_missions"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_storage_usage_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_storage_usage_project_id_projects"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_storage_usage")),
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_storage_usage_created_at"), "storage_usage", ["created_at"], unique=False, schema="lab"
    )
    op.create_index(
        "ix_lab_storage_usage_org_created",
        "storage_usage",
        ["organization_id", "created_at"],
        unique=False,
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_storage_usage_organization_id"), "storage_usage", ["organization_id"], unique=False, schema="lab"
    )
    op.create_table(
        "strategy_versions",
        sa.Column("strategy_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("parent_version_id", sa.Uuid(), nullable=True),
        sa.Column("definition", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("definition_sha256", sa.String(length=64), nullable=False),
        sa.Column("parameter_hash", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("evolution_run_id", sa.Uuid(), nullable=True),
        sa.Column("origin", sa.String(length=24), nullable=False),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("fitness", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evaluations", sa.Integer(), nullable=False),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], name=op.f("fk_strategy_versions_created_by_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_strategy_versions_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_version_id"],
            ["lab.strategy_versions.id"],
            name=op.f("fk_strategy_versions_parent_version_id_strategy_versions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["strategy_id"],
            ["lab.strategies.id"],
            name=op.f("fk_strategy_versions_strategy_id_strategies"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_strategy_versions")),
        sa.UniqueConstraint("strategy_id", "version", name="uq_lab_strategy_versions_strategy_version"),
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_strategy_versions_created_at"), "strategy_versions", ["created_at"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_strategy_versions_evolution_run_id"),
        "strategy_versions",
        ["evolution_run_id"],
        unique=False,
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_strategy_versions_organization_id"),
        "strategy_versions",
        ["organization_id"],
        unique=False,
        schema="lab",
    )
    op.create_index(
        "ix_lab_strategy_versions_status", "strategy_versions", ["strategy_id", "status"], unique=False, schema="lab"
    )
    op.create_table(
        "agent_runs",
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("mission_id", sa.Uuid(), nullable=True),
        sa.Column("agent_id", sa.Uuid(), nullable=True),
        sa.Column("agent_version_id", sa.Uuid(), nullable=True),
        sa.Column("parent_run_id", sa.Uuid(), nullable=True),
        sa.Column("workflow_run_id", sa.Uuid(), nullable=True),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("purpose", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("input", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("output", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("output_valid", sa.Boolean(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("prompt_ref", sa.String(length=120), nullable=True),
        sa.Column("prompt_template_sha256", sa.String(length=64), nullable=True),
        sa.Column("prompt_hash", sa.String(length=64), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("model", sa.String(length=160), nullable=True),
        sa.Column("model_revision", sa.String(length=160), nullable=True),
        sa.Column("model_params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("routing", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("tools_used", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("steps", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("injection_score", sa.Float(), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["lab.agents.id"], name=op.f("fk_agent_runs_agent_id_agents"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["agent_version_id"],
            ["lab.agent_versions.id"],
            name=op.f("fk_agent_runs_agent_version_id_agent_versions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["lab.missions.id"], name=op.f("fk_agent_runs_mission_id_missions"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_agent_runs_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_run_id"],
            ["lab.agent_runs.id"],
            name=op.f("fk_agent_runs_parent_run_id_agent_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_agent_runs_project_id_projects"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_runs")),
        schema="lab",
    )
    op.create_index(op.f("ix_lab_agent_runs_agent_id"), "agent_runs", ["agent_id"], unique=False, schema="lab")
    op.create_index(op.f("ix_lab_agent_runs_created_at"), "agent_runs", ["created_at"], unique=False, schema="lab")
    op.create_index(
        "ix_lab_agent_runs_mission_created", "agent_runs", ["mission_id", "created_at"], unique=False, schema="lab"
    )
    op.create_index(
        "ix_lab_agent_runs_org_status", "agent_runs", ["organization_id", "status"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_agent_runs_organization_id"), "agent_runs", ["organization_id"], unique=False, schema="lab"
    )
    op.create_index(op.f("ix_lab_agent_runs_project_id"), "agent_runs", ["project_id"], unique=False, schema="lab")
    op.create_index(
        op.f("ix_lab_agent_runs_workflow_run_id"), "agent_runs", ["workflow_run_id"], unique=False, schema="lab"
    )
    op.create_table(
        "document_chunks",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.vector.VECTOR(dim=768), nullable=True),
        sa.Column("embedding_model", sa.String(length=120), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["lab.document_versions.id"],
            name=op.f("fk_document_chunks_document_version_id_document_versions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_document_chunks_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_document_chunks_project_id_projects"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_chunks")),
        sa.UniqueConstraint("document_version_id", "chunk_index", name="uq_lab_document_chunks_version_idx"),
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_document_chunks_created_at"), "document_chunks", ["created_at"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_document_chunks_document_version_id"),
        "document_chunks",
        ["document_version_id"],
        unique=False,
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_document_chunks_organization_id"),
        "document_chunks",
        ["organization_id"],
        unique=False,
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_document_chunks_project_id"), "document_chunks", ["project_id"], unique=False, schema="lab"
    )
    op.create_table(
        "memory_links",
        sa.Column("memory_id", sa.Uuid(), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.String(length=64), nullable=False),
        sa.Column("relation", sa.String(length=32), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["memory_id"], ["lab.memories.id"], name=op.f("fk_memory_links_memory_id_memories"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_memory_links_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memory_links")),
        sa.UniqueConstraint("memory_id", "target_type", "target_id", "relation", name="uq_lab_memory_links_quad"),
        schema="lab",
    )
    op.create_index(op.f("ix_lab_memory_links_created_at"), "memory_links", ["created_at"], unique=False, schema="lab")
    op.create_index(op.f("ix_lab_memory_links_memory_id"), "memory_links", ["memory_id"], unique=False, schema="lab")
    op.create_index(
        op.f("ix_lab_memory_links_organization_id"), "memory_links", ["organization_id"], unique=False, schema="lab"
    )
    op.create_index(
        "ix_lab_memory_links_target", "memory_links", ["target_type", "target_id"], unique=False, schema="lab"
    )
    op.create_table(
        "research_events",
        sa.Column("research_task_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=48), nullable=False),
        sa.Column("provider_event_id", sa.String(length=255), nullable=True),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_research_events_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["research_task_id"],
            ["lab.research_tasks.id"],
            name=op.f("fk_research_events_research_task_id_research_tasks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_research_events")),
        sa.UniqueConstraint("research_task_id", "seq", name="uq_lab_research_events_task_seq"),
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_research_events_created_at"), "research_events", ["created_at"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_research_events_organization_id"),
        "research_events",
        ["organization_id"],
        unique=False,
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_research_events_research_task_id"),
        "research_events",
        ["research_task_id"],
        unique=False,
        schema="lab",
    )
    op.create_table(
        "research_sources",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("research_task_id", sa.Uuid(), nullable=True),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("url", sa.String(length=2000), nullable=True),
        sa.Column("title", sa.String(length=1000), nullable=True),
        sa.Column("publisher", sa.String(length=300), nullable=True),
        sa.Column("authors", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("publication_date", sa.String(length=32), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("citation", sa.Text(), nullable=True),
        sa.Column("doi", sa.String(length=255), nullable=True),
        sa.Column("arxiv_id", sa.String(length=32), nullable=True),
        sa.Column("snippet", sa.Text(), nullable=True),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("trust_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("injection_report", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["lab.documents.id"],
            name=op.f("fk_research_sources_document_id_documents"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_research_sources_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_research_sources_project_id_projects"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["research_task_id"],
            ["lab.research_tasks.id"],
            name=op.f("fk_research_sources_research_task_id_research_tasks"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_research_sources")),
        sa.UniqueConstraint("project_id", "checksum", name="uq_lab_research_sources_project_checksum"),
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_research_sources_created_at"), "research_sources", ["created_at"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_research_sources_organization_id"),
        "research_sources",
        ["organization_id"],
        unique=False,
        schema="lab",
    )
    op.create_index(
        "ix_lab_research_sources_project", "research_sources", ["project_id", "created_at"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_research_sources_research_task_id"),
        "research_sources",
        ["research_task_id"],
        unique=False,
        schema="lab",
    )
    op.create_table(
        "strategy_mutations",
        sa.Column("parent_version_id", sa.Uuid(), nullable=False),
        sa.Column("second_parent_version_id", sa.Uuid(), nullable=True),
        sa.Column("child_version_id", sa.Uuid(), nullable=True),
        sa.Column("evolution_run_id", sa.Uuid(), nullable=True),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("operator", sa.String(length=32), nullable=False),
        sa.Column("changes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=24), nullable=False),
        sa.Column("rejected", sa.Boolean(), nullable=False),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["child_version_id"],
            ["lab.strategy_versions.id"],
            name=op.f("fk_strategy_mutations_child_version_id_strategy_versions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_strategy_mutations_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_version_id"],
            ["lab.strategy_versions.id"],
            name=op.f("fk_strategy_mutations_parent_version_id_strategy_versions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_strategy_mutations")),
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_strategy_mutations_child_version_id"),
        "strategy_mutations",
        ["child_version_id"],
        unique=False,
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_strategy_mutations_created_at"), "strategy_mutations", ["created_at"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_strategy_mutations_organization_id"),
        "strategy_mutations",
        ["organization_id"],
        unique=False,
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_strategy_mutations_parent_version_id"),
        "strategy_mutations",
        ["parent_version_id"],
        unique=False,
        schema="lab",
    )
    op.create_table(
        "agent_messages",
        sa.Column("mission_id", sa.Uuid(), nullable=True),
        sa.Column("message_id", sa.String(length=64), nullable=False),
        sa.Column("sender_run_id", sa.Uuid(), nullable=True),
        sa.Column("receiver_run_id", sa.Uuid(), nullable=True),
        sa.Column("message_type", sa.String(length=24), nullable=False),
        sa.Column("envelope", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["lab.missions.id"], name=op.f("fk_agent_messages_mission_id_missions"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_agent_messages_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["receiver_run_id"],
            ["lab.agent_runs.id"],
            name=op.f("fk_agent_messages_receiver_run_id_agent_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["sender_run_id"],
            ["lab.agent_runs.id"],
            name=op.f("fk_agent_messages_sender_run_id_agent_runs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_messages")),
        sa.UniqueConstraint("message_id", name="uq_lab_agent_messages_message_id"),
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_agent_messages_created_at"), "agent_messages", ["created_at"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_agent_messages_mission_id"), "agent_messages", ["mission_id"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_agent_messages_organization_id"), "agent_messages", ["organization_id"], unique=False, schema="lab"
    )
    op.create_table(
        "hypotheses",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("mission_id", sa.Uuid(), nullable=True),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("expected_outcome", sa.Text(), nullable=True),
        sa.Column("measurable_prediction", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("assumptions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("novelty_notes", sa.Text(), nullable=True),
        sa.Column("feasibility", sa.Float(), nullable=True),
        sa.Column("estimated_cost_usd", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("parent_hypothesis_id", sa.Uuid(), nullable=True),
        sa.Column("generated_by_run_id", sa.Uuid(), nullable=True),
        sa.Column("critique", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("critique_score", sa.Float(), nullable=True),
        sa.Column("selection_reason", sa.Text(), nullable=True),
        sa.Column("outcome_summary", sa.Text(), nullable=True),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], name=op.f("fk_hypotheses_created_by_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["generated_by_run_id"],
            ["lab.agent_runs.id"],
            name=op.f("fk_hypotheses_generated_by_run_id_agent_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["lab.missions.id"], name=op.f("fk_hypotheses_mission_id_missions"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_hypotheses_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_hypothesis_id"],
            ["lab.hypotheses.id"],
            name=op.f("fk_hypotheses_parent_hypothesis_id_hypotheses"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_hypotheses_project_id_projects"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_hypotheses")),
        schema="lab",
    )
    op.create_index(op.f("ix_lab_hypotheses_created_at"), "hypotheses", ["created_at"], unique=False, schema="lab")
    op.create_index(
        "ix_lab_hypotheses_mission_status", "hypotheses", ["mission_id", "status"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_hypotheses_organization_id"), "hypotheses", ["organization_id"], unique=False, schema="lab"
    )
    op.create_index(op.f("ix_lab_hypotheses_project_id"), "hypotheses", ["project_id"], unique=False, schema="lab")
    op.create_table(
        "model_usage",
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("mission_id", sa.Uuid(), nullable=True),
        sa.Column("agent_run_id", sa.Uuid(), nullable=True),
        sa.Column("research_task_id", sa.Uuid(), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=160), nullable=False),
        sa.Column("task_type", sa.String(length=40), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("provider_request_id", sa.String(length=255), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cached_tokens", sa.Integer(), nullable=False),
        sa.Column("thought_tokens", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("cost_basis", sa.String(length=160), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("prompt_hash", sa.String(length=64), nullable=True),
        sa.Column("routing_reason", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["lab.agent_runs.id"],
            name=op.f("fk_model_usage_agent_run_id_agent_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["lab.missions.id"], name=op.f("fk_model_usage_mission_id_missions"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_model_usage_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_model_usage_project_id_projects"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_usage")),
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_model_usage_agent_run_id"), "model_usage", ["agent_run_id"], unique=False, schema="lab"
    )
    op.create_index(op.f("ix_lab_model_usage_created_at"), "model_usage", ["created_at"], unique=False, schema="lab")
    op.create_index("ix_lab_model_usage_mission", "model_usage", ["mission_id"], unique=False, schema="lab")
    op.create_index(
        "ix_lab_model_usage_org_created", "model_usage", ["organization_id", "created_at"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_model_usage_organization_id"), "model_usage", ["organization_id"], unique=False, schema="lab"
    )
    op.create_table(
        "tool_calls",
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("mission_id", sa.Uuid(), nullable=True),
        sa.Column("agent_run_id", sa.Uuid(), nullable=True),
        sa.Column("tool_id", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("risk_level", sa.String(length=16), nullable=False),
        sa.Column("policy_decision", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("arguments", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("result_sha256", sa.String(length=64), nullable=True),
        sa.Column("result_bytes", sa.Integer(), nullable=True),
        sa.Column("injection_score", sa.Float(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("approval_id", sa.Uuid(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("actor", sa.String(length=160), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["lab.agent_runs.id"],
            name=op.f("fk_tool_calls_agent_run_id_agent_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["lab.missions.id"], name=op.f("fk_tool_calls_mission_id_missions"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_tool_calls_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_tool_calls_project_id_projects"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tool_calls")),
        schema="lab",
    )
    op.create_index(op.f("ix_lab_tool_calls_agent_run_id"), "tool_calls", ["agent_run_id"], unique=False, schema="lab")
    op.create_index(op.f("ix_lab_tool_calls_created_at"), "tool_calls", ["created_at"], unique=False, schema="lab")
    op.create_index(
        "ix_lab_tool_calls_mission_created", "tool_calls", ["mission_id", "created_at"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_tool_calls_organization_id"), "tool_calls", ["organization_id"], unique=False, schema="lab"
    )
    op.create_index("ix_lab_tool_calls_tool", "tool_calls", ["organization_id", "tool_id"], unique=False, schema="lab")
    op.create_table(
        "experiments",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("mission_id", sa.Uuid(), nullable=True),
        sa.Column("hypothesis_id", sa.Uuid(), nullable=True),
        sa.Column("strategy_version_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("current_version_id", sa.Uuid(), nullable=True),
        sa.Column("version_count", sa.Integer(), nullable=False),
        sa.Column("designed_by_run_id", sa.Uuid(), nullable=True),
        sa.Column("outcome", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("is_demo", sa.Boolean(), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], name=op.f("fk_experiments_created_by_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["designed_by_run_id"],
            ["lab.agent_runs.id"],
            name=op.f("fk_experiments_designed_by_run_id_agent_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["hypothesis_id"],
            ["lab.hypotheses.id"],
            name=op.f("fk_experiments_hypothesis_id_hypotheses"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["lab.missions.id"], name=op.f("fk_experiments_mission_id_missions"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_experiments_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_experiments_project_id_projects"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_experiments")),
        schema="lab",
    )
    op.create_index(op.f("ix_lab_experiments_created_at"), "experiments", ["created_at"], unique=False, schema="lab")
    op.create_index(
        op.f("ix_lab_experiments_hypothesis_id"), "experiments", ["hypothesis_id"], unique=False, schema="lab"
    )
    op.create_index(
        "ix_lab_experiments_mission_status", "experiments", ["mission_id", "status"], unique=False, schema="lab"
    )
    op.create_index(
        "ix_lab_experiments_org_status", "experiments", ["organization_id", "status"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_experiments_organization_id"), "experiments", ["organization_id"], unique=False, schema="lab"
    )
    op.create_index(op.f("ix_lab_experiments_project_id"), "experiments", ["project_id"], unique=False, schema="lab")
    op.create_index(
        op.f("ix_lab_experiments_strategy_version_id"),
        "experiments",
        ["strategy_version_id"],
        unique=False,
        schema="lab",
    )
    op.create_table(
        "hypothesis_evidence",
        sa.Column("hypothesis_id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("relation", sa.String(length=16), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["hypothesis_id"],
            ["lab.hypotheses.id"],
            name=op.f("fk_hypothesis_evidence_hypothesis_id_hypotheses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_hypothesis_evidence_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_hypothesis_evidence")),
        sa.UniqueConstraint(
            "hypothesis_id", "source_type", "source_id", "relation", name="uq_lab_hypothesis_evidence_quad"
        ),
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_hypothesis_evidence_created_at"), "hypothesis_evidence", ["created_at"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_hypothesis_evidence_hypothesis_id"),
        "hypothesis_evidence",
        ["hypothesis_id"],
        unique=False,
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_hypothesis_evidence_organization_id"),
        "hypothesis_evidence",
        ["organization_id"],
        unique=False,
        schema="lab",
    )
    op.create_table(
        "claims",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("mission_id", sa.Uuid(), nullable=True),
        sa.Column("experiment_id", sa.Uuid(), nullable=True),
        sa.Column("hypothesis_id", sa.Uuid(), nullable=True),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("claim_type", sa.String(length=24), nullable=False),
        sa.Column("metric", sa.String(length=120), nullable=True),
        sa.Column("source", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("scope", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("uncertainty", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["experiment_id"],
            ["lab.experiments.id"],
            name=op.f("fk_claims_experiment_id_experiments"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["hypothesis_id"],
            ["lab.hypotheses.id"],
            name=op.f("fk_claims_hypothesis_id_hypotheses"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["lab.missions.id"], name=op.f("fk_claims_mission_id_missions"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_claims_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_claims_project_id_projects"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_claims")),
        sa.UniqueConstraint("organization_id", "fingerprint", name="uq_lab_claims_org_fingerprint"),
        schema="lab",
    )
    op.create_index(op.f("ix_lab_claims_created_at"), "claims", ["created_at"], unique=False, schema="lab")
    op.create_index(op.f("ix_lab_claims_experiment_id"), "claims", ["experiment_id"], unique=False, schema="lab")
    op.create_index("ix_lab_claims_mission_status", "claims", ["mission_id", "status"], unique=False, schema="lab")
    op.create_index(op.f("ix_lab_claims_organization_id"), "claims", ["organization_id"], unique=False, schema="lab")
    op.create_index(op.f("ix_lab_claims_project_id"), "claims", ["project_id"], unique=False, schema="lab")
    op.create_table(
        "evaluation_runs",
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("experiment_id", sa.Uuid(), nullable=False),
        sa.Column("experiment_version_id", sa.Uuid(), nullable=True),
        sa.Column("run_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evaluator_key", sa.String(length=64), nullable=False),
        sa.Column("evaluator_version", sa.String(length=24), nullable=False),
        sa.Column("evaluator_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("verdict", sa.String(length=16), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("warnings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evidence_id", sa.Uuid(), nullable=True),
        sa.Column("independent", sa.Boolean(), nullable=False),
        sa.Column("evaluated_by", sa.String(length=160), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["evidence_id"], ["evidence.id"], name=op.f("fk_evaluation_runs_evidence_id_evidence"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["experiment_id"],
            ["lab.experiments.id"],
            name=op.f("fk_evaluation_runs_experiment_id_experiments"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_evaluation_runs_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_evaluation_runs_project_id_projects"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluation_runs")),
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_evaluation_runs_created_at"), "evaluation_runs", ["created_at"], unique=False, schema="lab"
    )
    op.create_index(
        "ix_lab_evaluation_runs_exp", "evaluation_runs", ["experiment_id", "evaluator_key"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_evaluation_runs_organization_id"),
        "evaluation_runs",
        ["organization_id"],
        unique=False,
        schema="lab",
    )
    op.create_table(
        "experiment_versions",
        sa.Column("experiment_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("spec", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("spec_sha256", sa.String(length=64), nullable=False),
        sa.Column("validation", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("code_artifact_id", sa.Uuid(), nullable=True),
        sa.Column("code_sha256", sa.String(length=64), nullable=True),
        sa.Column("generated_by_run_id", sa.Uuid(), nullable=True),
        sa.Column("change_note", sa.Text(), nullable=True),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_experiment_versions_created_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["experiment_id"],
            ["lab.experiments.id"],
            name=op.f("fk_experiment_versions_experiment_id_experiments"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_experiment_versions_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_experiment_versions")),
        sa.UniqueConstraint("experiment_id", "version", name="uq_lab_experiment_versions_exp_version"),
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_experiment_versions_created_at"), "experiment_versions", ["created_at"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_experiment_versions_experiment_id"),
        "experiment_versions",
        ["experiment_id"],
        unique=False,
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_experiment_versions_organization_id"),
        "experiment_versions",
        ["organization_id"],
        unique=False,
        schema="lab",
    )
    op.create_table(
        "strategy_evaluations",
        sa.Column("strategy_version_id", sa.Uuid(), nullable=False),
        sa.Column("mission_id", sa.Uuid(), nullable=True),
        sa.Column("experiment_id", sa.Uuid(), nullable=True),
        sa.Column("benchmark_run_id", sa.Uuid(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("primary_samples", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("fitness", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("feasible", sa.Boolean(), nullable=False),
        sa.Column("reproduced", sa.Boolean(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["experiment_id"],
            ["lab.experiments.id"],
            name=op.f("fk_strategy_evaluations_experiment_id_experiments"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"],
            ["lab.missions.id"],
            name=op.f("fk_strategy_evaluations_mission_id_missions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_strategy_evaluations_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["strategy_version_id"],
            ["lab.strategy_versions.id"],
            name=op.f("fk_strategy_evaluations_strategy_version_id_strategy_versions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_strategy_evaluations")),
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_strategy_evaluations_created_at"),
        "strategy_evaluations",
        ["created_at"],
        unique=False,
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_strategy_evaluations_organization_id"),
        "strategy_evaluations",
        ["organization_id"],
        unique=False,
        schema="lab",
    )
    op.create_index(
        "ix_lab_strategy_evaluations_version",
        "strategy_evaluations",
        ["strategy_version_id", "created_at"],
        unique=False,
        schema="lab",
    )
    op.create_table(
        "claim_evidence",
        sa.Column("claim_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_id", sa.Uuid(), nullable=False),
        sa.Column("relation", sa.String(length=16), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["claim_id"], ["lab.claims.id"], name=op.f("fk_claim_evidence_claim_id_claims"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"], ["evidence.id"], name=op.f("fk_claim_evidence_evidence_id_evidence"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_claim_evidence_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_claim_evidence")),
        sa.UniqueConstraint("claim_id", "evidence_id", "relation", name="uq_lab_claim_evidence_triple"),
        schema="lab",
    )
    op.create_index(op.f("ix_lab_claim_evidence_claim_id"), "claim_evidence", ["claim_id"], unique=False, schema="lab")
    op.create_index(
        op.f("ix_lab_claim_evidence_created_at"), "claim_evidence", ["created_at"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_claim_evidence_evidence_id"), "claim_evidence", ["evidence_id"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_claim_evidence_organization_id"), "claim_evidence", ["organization_id"], unique=False, schema="lab"
    )
    op.create_table(
        "discoveries",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("mission_id", sa.Uuid(), nullable=True),
        sa.Column("claim_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("evidence_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("experiment_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("reproduction_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("verifier_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("strategy_version_id", sa.Uuid(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("limitations", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("requested_by", sa.String(length=64), nullable=True),
        sa.Column("reviewed_by_id", sa.Uuid(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_demo", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["claim_id"], ["lab.claims.id"], name=op.f("fk_discoveries_claim_id_claims"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["lab.missions.id"], name=op.f("fk_discoveries_mission_id_missions"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_discoveries_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_discoveries_project_id_projects"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_id"], ["users.id"], name=op.f("fk_discoveries_reviewed_by_id_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_discoveries")),
        sa.UniqueConstraint("claim_id", name="uq_lab_discoveries_claim"),
        schema="lab",
    )
    op.create_index(op.f("ix_lab_discoveries_created_at"), "discoveries", ["created_at"], unique=False, schema="lab")
    op.create_index(op.f("ix_lab_discoveries_mission_id"), "discoveries", ["mission_id"], unique=False, schema="lab")
    op.create_index(
        "ix_lab_discoveries_org_status", "discoveries", ["organization_id", "status"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_discoveries_organization_id"), "discoveries", ["organization_id"], unique=False, schema="lab"
    )
    op.create_index(op.f("ix_lab_discoveries_project_id"), "discoveries", ["project_id"], unique=False, schema="lab")
    op.create_table(
        "experiment_runs",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("mission_id", sa.Uuid(), nullable=True),
        sa.Column("experiment_id", sa.Uuid(), nullable=False),
        sa.Column("experiment_version_id", sa.Uuid(), nullable=False),
        sa.Column("run_kind", sa.String(length=16), nullable=False),
        sa.Column("variant", sa.String(length=120), nullable=True),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("execution_job_id", sa.Uuid(), nullable=True),
        sa.Column("harness_job_id", sa.Uuid(), nullable=True),
        sa.Column("reproduction_of_run_id", sa.Uuid(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("self_reported", sa.Boolean(), nullable=False),
        sa.Column("resources", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=True),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["experiment_id"],
            ["lab.experiments.id"],
            name=op.f("fk_experiment_runs_experiment_id_experiments"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["experiment_version_id"],
            ["lab.experiment_versions.id"],
            name=op.f("fk_experiment_runs_experiment_version_id_experiment_versions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["lab.missions.id"], name=op.f("fk_experiment_runs_mission_id_missions"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_experiment_runs_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_experiment_runs_project_id_projects"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["reproduction_of_run_id"],
            ["lab.experiment_runs.id"],
            name=op.f("fk_experiment_runs_reproduction_of_run_id_experiment_runs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_experiment_runs")),
        sa.UniqueConstraint("organization_id", "idempotency_key", name="uq_lab_experiment_runs_idem"),
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_experiment_runs_created_at"), "experiment_runs", ["created_at"], unique=False, schema="lab"
    )
    op.create_index(
        "ix_lab_experiment_runs_exp_kind", "experiment_runs", ["experiment_id", "run_kind"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_experiment_runs_mission_id"), "experiment_runs", ["mission_id"], unique=False, schema="lab"
    )
    op.create_index(
        "ix_lab_experiment_runs_org_status",
        "experiment_runs",
        ["organization_id", "status"],
        unique=False,
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_experiment_runs_organization_id"),
        "experiment_runs",
        ["organization_id"],
        unique=False,
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_experiment_runs_project_id"), "experiment_runs", ["project_id"], unique=False, schema="lab"
    )
    op.create_table(
        "run_comparisons",
        sa.Column("experiment_id", sa.Uuid(), nullable=False),
        sa.Column("evaluation_run_id", sa.Uuid(), nullable=True),
        sa.Column("metric", sa.String(length=120), nullable=False),
        sa.Column("direction", sa.String(length=8), nullable=False),
        sa.Column("baseline_run_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("candidate_run_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("baseline_mean", sa.Float(), nullable=True),
        sa.Column("candidate_mean", sa.Float(), nullable=True),
        sa.Column("delta", sa.Float(), nullable=True),
        sa.Column("relative_change", sa.Float(), nullable=True),
        sa.Column("ci_low", sa.Float(), nullable=True),
        sa.Column("ci_high", sa.Float(), nullable=True),
        sa.Column("test", sa.String(length=24), nullable=True),
        sa.Column("p_value", sa.Float(), nullable=True),
        sa.Column("p_adjusted", sa.Float(), nullable=True),
        sa.Column("effect_size", sa.Float(), nullable=True),
        sa.Column("config_diff", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["evaluation_run_id"],
            ["lab.evaluation_runs.id"],
            name=op.f("fk_run_comparisons_evaluation_run_id_evaluation_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["experiment_id"],
            ["lab.experiments.id"],
            name=op.f("fk_run_comparisons_experiment_id_experiments"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_run_comparisons_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run_comparisons")),
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_run_comparisons_created_at"), "run_comparisons", ["created_at"], unique=False, schema="lab"
    )
    op.create_index(
        "ix_lab_run_comparisons_exp_metric", "run_comparisons", ["experiment_id", "metric"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_run_comparisons_organization_id"),
        "run_comparisons",
        ["organization_id"],
        unique=False,
        schema="lab",
    )
    op.create_table(
        "verifications",
        sa.Column("claim_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("criteria", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("decision", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("resulting_status", sa.String(length=24), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("reproductions_passed", sa.Integer(), nullable=False),
        sa.Column("reproductions_failed", sa.Integer(), nullable=False),
        sa.Column("requested_by", sa.String(length=160), nullable=True),
        sa.Column("workflow_run_id", sa.Uuid(), nullable=True),
        sa.Column("evidence_id", sa.Uuid(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["claim_id"], ["lab.claims.id"], name=op.f("fk_verifications_claim_id_claims"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"], ["evidence.id"], name=op.f("fk_verifications_evidence_id_evidence"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_verifications_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_verifications_project_id_projects"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_verifications")),
        schema="lab",
    )
    op.create_index(
        "ix_lab_verifications_claim", "verifications", ["claim_id", "created_at"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_verifications_created_at"), "verifications", ["created_at"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_verifications_organization_id"), "verifications", ["organization_id"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_verifications_project_id"), "verifications", ["project_id"], unique=False, schema="lab"
    )
    op.create_table(
        "compute_jobs",
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("mission_id", sa.Uuid(), nullable=True),
        sa.Column("experiment_id", sa.Uuid(), nullable=True),
        sa.Column("experiment_run_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("backend", sa.String(length=24), nullable=False),
        sa.Column("image", sa.String(length=500), nullable=False),
        sa.Column("image_digest", sa.String(length=100), nullable=True),
        sa.Column("command", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=True),
        sa.Column("resource_request", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("network_policy", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("filesystem_policy", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("secrets_policy", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("backend_ref", sa.String(length=255), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("measured", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("artifact_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("logs_artifact_id", sa.Uuid(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("approval_id", sa.Uuid(), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["experiment_id"],
            ["lab.experiments.id"],
            name=op.f("fk_compute_jobs_experiment_id_experiments"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["experiment_run_id"],
            ["lab.experiment_runs.id"],
            name=op.f("fk_compute_jobs_experiment_run_id_experiment_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["lab.missions.id"], name=op.f("fk_compute_jobs_mission_id_missions"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_compute_jobs_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_compute_jobs_project_id_projects"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_compute_jobs")),
        sa.UniqueConstraint("organization_id", "idempotency_key", name="uq_lab_compute_jobs_idem"),
        schema="lab",
    )
    op.create_index(op.f("ix_lab_compute_jobs_created_at"), "compute_jobs", ["created_at"], unique=False, schema="lab")
    op.create_index(
        op.f("ix_lab_compute_jobs_experiment_run_id"), "compute_jobs", ["experiment_run_id"], unique=False, schema="lab"
    )
    op.create_index(op.f("ix_lab_compute_jobs_mission_id"), "compute_jobs", ["mission_id"], unique=False, schema="lab")
    op.create_index(
        "ix_lab_compute_jobs_org_status", "compute_jobs", ["organization_id", "status"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_compute_jobs_organization_id"), "compute_jobs", ["organization_id"], unique=False, schema="lab"
    )
    op.create_index(op.f("ix_lab_compute_jobs_project_id"), "compute_jobs", ["project_id"], unique=False, schema="lab")
    op.create_index(
        "ix_lab_compute_jobs_status_heartbeat", "compute_jobs", ["status", "heartbeat_at"], unique=False, schema="lab"
    )
    op.create_table(
        "discovery_versions",
        sa.Column("discovery_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("actor", sa.String(length=160), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["discovery_id"],
            ["lab.discoveries.id"],
            name=op.f("fk_discovery_versions_discovery_id_discoveries"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_discovery_versions_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_discovery_versions")),
        sa.UniqueConstraint("discovery_id", "version", name="uq_lab_discovery_versions_version"),
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_discovery_versions_created_at"), "discovery_versions", ["created_at"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_discovery_versions_discovery_id"),
        "discovery_versions",
        ["discovery_id"],
        unique=False,
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_discovery_versions_organization_id"),
        "discovery_versions",
        ["organization_id"],
        unique=False,
        schema="lab",
    )
    op.create_table(
        "experiment_metrics",
        sa.Column("experiment_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["experiment_id"],
            ["lab.experiments.id"],
            name=op.f("fk_experiment_metrics_experiment_id_experiments"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_experiment_metrics_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["lab.experiment_runs.id"],
            name=op.f("fk_experiment_metrics_run_id_experiment_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_experiment_metrics")),
        sa.UniqueConstraint("run_id", "name", "source", name="uq_lab_experiment_metrics_run_name_source"),
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_experiment_metrics_created_at"), "experiment_metrics", ["created_at"], unique=False, schema="lab"
    )
    op.create_index(
        "ix_lab_experiment_metrics_exp_name",
        "experiment_metrics",
        ["experiment_id", "name"],
        unique=False,
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_experiment_metrics_organization_id"),
        "experiment_metrics",
        ["organization_id"],
        unique=False,
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_experiment_metrics_run_id"), "experiment_metrics", ["run_id"], unique=False, schema="lab"
    )
    op.create_table(
        "failures",
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("mission_id", sa.Uuid(), nullable=True),
        sa.Column("experiment_id", sa.Uuid(), nullable=True),
        sa.Column("experiment_run_id", sa.Uuid(), nullable=True),
        sa.Column("agent_run_id", sa.Uuid(), nullable=True),
        sa.Column("strategy_version_id", sa.Uuid(), nullable=True),
        sa.Column("stage", sa.String(length=24), nullable=False),
        sa.Column("failure_type", sa.String(length=40), nullable=False),
        sa.Column("rule_id", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("root_cause", sa.Text(), nullable=False),
        sa.Column("evidence_lines", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("traceback", sa.Text(), nullable=True),
        sa.Column("logs_artifact_id", sa.Uuid(), nullable=True),
        sa.Column("signature", sa.String(length=80), nullable=False),
        sa.Column("recurrence_count", sa.Integer(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("signal", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("diagnosis", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("similar_failure_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("recovery_actions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("recovery_status", sa.String(length=16), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lesson_id", sa.Uuid(), nullable=True),
        sa.Column("evidence_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["lab.agent_runs.id"],
            name=op.f("fk_failures_agent_run_id_agent_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"], ["evidence.id"], name=op.f("fk_failures_evidence_id_evidence"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["experiment_id"],
            ["lab.experiments.id"],
            name=op.f("fk_failures_experiment_id_experiments"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["experiment_run_id"],
            ["lab.experiment_runs.id"],
            name=op.f("fk_failures_experiment_run_id_experiment_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["lab.missions.id"], name=op.f("fk_failures_mission_id_missions"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_failures_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_failures_project_id_projects"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_failures")),
        schema="lab",
    )
    op.create_index(op.f("ix_lab_failures_created_at"), "failures", ["created_at"], unique=False, schema="lab")
    op.create_index(op.f("ix_lab_failures_experiment_id"), "failures", ["experiment_id"], unique=False, schema="lab")
    op.create_index(
        "ix_lab_failures_mission_type", "failures", ["mission_id", "failure_type"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_failures_organization_id"), "failures", ["organization_id"], unique=False, schema="lab"
    )
    op.create_index(op.f("ix_lab_failures_project_id"), "failures", ["project_id"], unique=False, schema="lab")
    op.create_index(
        "ix_lab_failures_signature", "failures", ["organization_id", "signature"], unique=False, schema="lab"
    )
    op.create_table(
        "verification_runs",
        sa.Column("verification_id", sa.Uuid(), nullable=False),
        sa.Column("check_name", sa.String(length=48), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=True),
        sa.Column("contradicts", sa.Boolean(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("verifier", sa.String(length=160), nullable=False),
        sa.Column("independent", sa.Boolean(), nullable=False),
        sa.Column("evidence_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_verification_runs_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["verification_id"],
            ["lab.verifications.id"],
            name=op.f("fk_verification_runs_verification_id_verifications"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_verification_runs")),
        sa.UniqueConstraint("verification_id", "check_name", name="uq_lab_verification_runs_check"),
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_verification_runs_created_at"), "verification_runs", ["created_at"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_verification_runs_organization_id"),
        "verification_runs",
        ["organization_id"],
        unique=False,
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_verification_runs_verification_id"),
        "verification_runs",
        ["verification_id"],
        unique=False,
        schema="lab",
    )
    op.create_table(
        "artifacts",
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("mission_id", sa.Uuid(), nullable=True),
        sa.Column("experiment_run_id", sa.Uuid(), nullable=True),
        sa.Column("execution_job_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("current_version_id", sa.Uuid(), nullable=True),
        sa.Column("retention_class", sa.String(length=16), nullable=False),
        sa.Column("created_by", sa.String(length=160), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["execution_job_id"],
            ["lab.compute_jobs.id"],
            name=op.f("fk_artifacts_execution_job_id_compute_jobs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["experiment_run_id"],
            ["lab.experiment_runs.id"],
            name=op.f("fk_artifacts_experiment_run_id_experiment_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["lab.missions.id"], name=op.f("fk_artifacts_mission_id_missions"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_artifacts_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_artifacts_project_id_projects"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_artifacts")),
        schema="lab",
    )
    op.create_index(op.f("ix_lab_artifacts_created_at"), "artifacts", ["created_at"], unique=False, schema="lab")
    op.create_index(
        op.f("ix_lab_artifacts_execution_job_id"), "artifacts", ["execution_job_id"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_artifacts_experiment_run_id"), "artifacts", ["experiment_run_id"], unique=False, schema="lab"
    )
    op.create_index(op.f("ix_lab_artifacts_mission_id"), "artifacts", ["mission_id"], unique=False, schema="lab")
    op.create_index(
        op.f("ix_lab_artifacts_organization_id"), "artifacts", ["organization_id"], unique=False, schema="lab"
    )
    op.create_index("ix_lab_artifacts_project_kind", "artifacts", ["project_id", "kind"], unique=False, schema="lab")
    op.create_table(
        "compute_usage",
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("mission_id", sa.Uuid(), nullable=True),
        sa.Column("experiment_id", sa.Uuid(), nullable=True),
        sa.Column("experiment_run_id", sa.Uuid(), nullable=True),
        sa.Column("execution_job_id", sa.Uuid(), nullable=False),
        sa.Column("backend", sa.String(length=24), nullable=False),
        sa.Column("cpu", sa.Float(), nullable=False),
        sa.Column("memory_mb", sa.Integer(), nullable=False),
        sa.Column("gpu_type", sa.String(length=40), nullable=True),
        sa.Column("gpu_count", sa.Integer(), nullable=False),
        sa.Column("runtime_seconds", sa.Float(), nullable=False),
        sa.Column("cpu_seconds", sa.Float(), nullable=True),
        sa.Column("gpu_seconds", sa.Float(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("cost_basis", sa.String(length=160), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["execution_job_id"],
            ["lab.compute_jobs.id"],
            name=op.f("fk_compute_usage_execution_job_id_compute_jobs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["experiment_id"],
            ["lab.experiments.id"],
            name=op.f("fk_compute_usage_experiment_id_experiments"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["experiment_run_id"],
            ["lab.experiment_runs.id"],
            name=op.f("fk_compute_usage_experiment_run_id_experiment_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["lab.missions.id"], name=op.f("fk_compute_usage_mission_id_missions"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_compute_usage_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_compute_usage_project_id_projects"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_compute_usage")),
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_compute_usage_created_at"), "compute_usage", ["created_at"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_compute_usage_execution_job_id"), "compute_usage", ["execution_job_id"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_compute_usage_experiment_id"), "compute_usage", ["experiment_id"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_compute_usage_mission_id"), "compute_usage", ["mission_id"], unique=False, schema="lab"
    )
    op.create_index(
        "ix_lab_compute_usage_org_created",
        "compute_usage",
        ["organization_id", "created_at"],
        unique=False,
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_compute_usage_organization_id"), "compute_usage", ["organization_id"], unique=False, schema="lab"
    )
    op.create_table(
        "artifact_versions",
        sa.Column("artifact_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=False),
        sa.Column("scan_status", sa.String(length=24), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            ["lab.artifacts.id"],
            name=op.f("fk_artifact_versions_artifact_id_artifacts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_artifact_versions_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_artifact_versions")),
        sa.UniqueConstraint("artifact_id", "version", name="uq_lab_artifact_versions_art_version"),
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_artifact_versions_artifact_id"), "artifact_versions", ["artifact_id"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_artifact_versions_created_at"), "artifact_versions", ["created_at"], unique=False, schema="lab"
    )
    op.create_index(
        op.f("ix_lab_artifact_versions_organization_id"),
        "artifact_versions",
        ["organization_id"],
        unique=False,
        schema="lab",
    )
    op.create_index(
        op.f("ix_lab_artifact_versions_sha256"), "artifact_versions", ["sha256"], unique=False, schema="lab"
    )
    op.add_column("api_keys", sa.Column("service_account_id", sa.Uuid(), nullable=True))
    op.add_column("api_keys", sa.Column("rotated_from_id", sa.Uuid(), nullable=True))
    op.create_index(op.f("ix_api_keys_service_account_id"), "api_keys", ["service_account_id"], unique=False)
    op.create_foreign_key(
        op.f("fk_api_keys_service_account_id_service_accounts"),
        "api_keys",
        "service_accounts",
        ["service_account_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.add_column("evidence", sa.Column("chain_scope", sa.String(length=96), nullable=True))
    op.create_index(
        "uq_evidence_chain_scope_seq",
        "evidence",
        ["chain_scope", "seq"],
        unique=True,
        postgresql_where=sa.text("chain_scope IS NOT NULL"),
    )
    op.create_unique_constraint("uq_webhook_deliveries_hook_event", "webhook_deliveries", ["webhook_id", "event_id"])


def _drop_tables() -> None:
    op.drop_constraint("uq_webhook_deliveries_hook_event", "webhook_deliveries", type_="unique")
    op.drop_index(
        "uq_evidence_chain_scope_seq", table_name="evidence", postgresql_where=sa.text("chain_scope IS NOT NULL")
    )
    op.drop_column("evidence", "chain_scope")
    op.drop_constraint(op.f("fk_api_keys_service_account_id_service_accounts"), "api_keys", type_="foreignkey")
    op.drop_index(op.f("ix_api_keys_service_account_id"), table_name="api_keys")
    op.drop_column("api_keys", "rotated_from_id")
    op.drop_column("api_keys", "service_account_id")
    op.drop_index(op.f("ix_lab_artifact_versions_sha256"), table_name="artifact_versions", schema="lab")
    op.drop_index(op.f("ix_lab_artifact_versions_organization_id"), table_name="artifact_versions", schema="lab")
    op.drop_index(op.f("ix_lab_artifact_versions_created_at"), table_name="artifact_versions", schema="lab")
    op.drop_index(op.f("ix_lab_artifact_versions_artifact_id"), table_name="artifact_versions", schema="lab")
    op.drop_table("artifact_versions", schema="lab")
    op.drop_index(op.f("ix_lab_compute_usage_organization_id"), table_name="compute_usage", schema="lab")
    op.drop_index("ix_lab_compute_usage_org_created", table_name="compute_usage", schema="lab")
    op.drop_index(op.f("ix_lab_compute_usage_mission_id"), table_name="compute_usage", schema="lab")
    op.drop_index(op.f("ix_lab_compute_usage_experiment_id"), table_name="compute_usage", schema="lab")
    op.drop_index(op.f("ix_lab_compute_usage_execution_job_id"), table_name="compute_usage", schema="lab")
    op.drop_index(op.f("ix_lab_compute_usage_created_at"), table_name="compute_usage", schema="lab")
    op.drop_table("compute_usage", schema="lab")
    op.drop_index("ix_lab_artifacts_project_kind", table_name="artifacts", schema="lab")
    op.drop_index(op.f("ix_lab_artifacts_organization_id"), table_name="artifacts", schema="lab")
    op.drop_index(op.f("ix_lab_artifacts_mission_id"), table_name="artifacts", schema="lab")
    op.drop_index(op.f("ix_lab_artifacts_experiment_run_id"), table_name="artifacts", schema="lab")
    op.drop_index(op.f("ix_lab_artifacts_execution_job_id"), table_name="artifacts", schema="lab")
    op.drop_index(op.f("ix_lab_artifacts_created_at"), table_name="artifacts", schema="lab")
    op.drop_table("artifacts", schema="lab")
    op.drop_index(op.f("ix_lab_verification_runs_verification_id"), table_name="verification_runs", schema="lab")
    op.drop_index(op.f("ix_lab_verification_runs_organization_id"), table_name="verification_runs", schema="lab")
    op.drop_index(op.f("ix_lab_verification_runs_created_at"), table_name="verification_runs", schema="lab")
    op.drop_table("verification_runs", schema="lab")
    op.drop_index("ix_lab_failures_signature", table_name="failures", schema="lab")
    op.drop_index(op.f("ix_lab_failures_project_id"), table_name="failures", schema="lab")
    op.drop_index(op.f("ix_lab_failures_organization_id"), table_name="failures", schema="lab")
    op.drop_index("ix_lab_failures_mission_type", table_name="failures", schema="lab")
    op.drop_index(op.f("ix_lab_failures_experiment_id"), table_name="failures", schema="lab")
    op.drop_index(op.f("ix_lab_failures_created_at"), table_name="failures", schema="lab")
    op.drop_table("failures", schema="lab")
    op.drop_index(op.f("ix_lab_experiment_metrics_run_id"), table_name="experiment_metrics", schema="lab")
    op.drop_index(op.f("ix_lab_experiment_metrics_organization_id"), table_name="experiment_metrics", schema="lab")
    op.drop_index("ix_lab_experiment_metrics_exp_name", table_name="experiment_metrics", schema="lab")
    op.drop_index(op.f("ix_lab_experiment_metrics_created_at"), table_name="experiment_metrics", schema="lab")
    op.drop_table("experiment_metrics", schema="lab")
    op.drop_index(op.f("ix_lab_discovery_versions_organization_id"), table_name="discovery_versions", schema="lab")
    op.drop_index(op.f("ix_lab_discovery_versions_discovery_id"), table_name="discovery_versions", schema="lab")
    op.drop_index(op.f("ix_lab_discovery_versions_created_at"), table_name="discovery_versions", schema="lab")
    op.drop_table("discovery_versions", schema="lab")
    op.drop_index("ix_lab_compute_jobs_status_heartbeat", table_name="compute_jobs", schema="lab")
    op.drop_index(op.f("ix_lab_compute_jobs_project_id"), table_name="compute_jobs", schema="lab")
    op.drop_index(op.f("ix_lab_compute_jobs_organization_id"), table_name="compute_jobs", schema="lab")
    op.drop_index("ix_lab_compute_jobs_org_status", table_name="compute_jobs", schema="lab")
    op.drop_index(op.f("ix_lab_compute_jobs_mission_id"), table_name="compute_jobs", schema="lab")
    op.drop_index(op.f("ix_lab_compute_jobs_experiment_run_id"), table_name="compute_jobs", schema="lab")
    op.drop_index(op.f("ix_lab_compute_jobs_created_at"), table_name="compute_jobs", schema="lab")
    op.drop_table("compute_jobs", schema="lab")
    op.drop_index(op.f("ix_lab_verifications_project_id"), table_name="verifications", schema="lab")
    op.drop_index(op.f("ix_lab_verifications_organization_id"), table_name="verifications", schema="lab")
    op.drop_index(op.f("ix_lab_verifications_created_at"), table_name="verifications", schema="lab")
    op.drop_index("ix_lab_verifications_claim", table_name="verifications", schema="lab")
    op.drop_table("verifications", schema="lab")
    op.drop_index(op.f("ix_lab_run_comparisons_organization_id"), table_name="run_comparisons", schema="lab")
    op.drop_index("ix_lab_run_comparisons_exp_metric", table_name="run_comparisons", schema="lab")
    op.drop_index(op.f("ix_lab_run_comparisons_created_at"), table_name="run_comparisons", schema="lab")
    op.drop_table("run_comparisons", schema="lab")
    op.drop_index(op.f("ix_lab_experiment_runs_project_id"), table_name="experiment_runs", schema="lab")
    op.drop_index(op.f("ix_lab_experiment_runs_organization_id"), table_name="experiment_runs", schema="lab")
    op.drop_index("ix_lab_experiment_runs_org_status", table_name="experiment_runs", schema="lab")
    op.drop_index(op.f("ix_lab_experiment_runs_mission_id"), table_name="experiment_runs", schema="lab")
    op.drop_index("ix_lab_experiment_runs_exp_kind", table_name="experiment_runs", schema="lab")
    op.drop_index(op.f("ix_lab_experiment_runs_created_at"), table_name="experiment_runs", schema="lab")
    op.drop_table("experiment_runs", schema="lab")
    op.drop_index(op.f("ix_lab_discoveries_project_id"), table_name="discoveries", schema="lab")
    op.drop_index(op.f("ix_lab_discoveries_organization_id"), table_name="discoveries", schema="lab")
    op.drop_index("ix_lab_discoveries_org_status", table_name="discoveries", schema="lab")
    op.drop_index(op.f("ix_lab_discoveries_mission_id"), table_name="discoveries", schema="lab")
    op.drop_index(op.f("ix_lab_discoveries_created_at"), table_name="discoveries", schema="lab")
    op.drop_table("discoveries", schema="lab")
    op.drop_index(op.f("ix_lab_claim_evidence_organization_id"), table_name="claim_evidence", schema="lab")
    op.drop_index(op.f("ix_lab_claim_evidence_evidence_id"), table_name="claim_evidence", schema="lab")
    op.drop_index(op.f("ix_lab_claim_evidence_created_at"), table_name="claim_evidence", schema="lab")
    op.drop_index(op.f("ix_lab_claim_evidence_claim_id"), table_name="claim_evidence", schema="lab")
    op.drop_table("claim_evidence", schema="lab")
    op.drop_index("ix_lab_strategy_evaluations_version", table_name="strategy_evaluations", schema="lab")
    op.drop_index(op.f("ix_lab_strategy_evaluations_organization_id"), table_name="strategy_evaluations", schema="lab")
    op.drop_index(op.f("ix_lab_strategy_evaluations_created_at"), table_name="strategy_evaluations", schema="lab")
    op.drop_table("strategy_evaluations", schema="lab")
    op.drop_index(op.f("ix_lab_experiment_versions_organization_id"), table_name="experiment_versions", schema="lab")
    op.drop_index(op.f("ix_lab_experiment_versions_experiment_id"), table_name="experiment_versions", schema="lab")
    op.drop_index(op.f("ix_lab_experiment_versions_created_at"), table_name="experiment_versions", schema="lab")
    op.drop_table("experiment_versions", schema="lab")
    op.drop_index(op.f("ix_lab_evaluation_runs_organization_id"), table_name="evaluation_runs", schema="lab")
    op.drop_index("ix_lab_evaluation_runs_exp", table_name="evaluation_runs", schema="lab")
    op.drop_index(op.f("ix_lab_evaluation_runs_created_at"), table_name="evaluation_runs", schema="lab")
    op.drop_table("evaluation_runs", schema="lab")
    op.drop_index(op.f("ix_lab_claims_project_id"), table_name="claims", schema="lab")
    op.drop_index(op.f("ix_lab_claims_organization_id"), table_name="claims", schema="lab")
    op.drop_index("ix_lab_claims_mission_status", table_name="claims", schema="lab")
    op.drop_index(op.f("ix_lab_claims_experiment_id"), table_name="claims", schema="lab")
    op.drop_index(op.f("ix_lab_claims_created_at"), table_name="claims", schema="lab")
    op.drop_table("claims", schema="lab")
    op.drop_index(op.f("ix_lab_hypothesis_evidence_organization_id"), table_name="hypothesis_evidence", schema="lab")
    op.drop_index(op.f("ix_lab_hypothesis_evidence_hypothesis_id"), table_name="hypothesis_evidence", schema="lab")
    op.drop_index(op.f("ix_lab_hypothesis_evidence_created_at"), table_name="hypothesis_evidence", schema="lab")
    op.drop_table("hypothesis_evidence", schema="lab")
    op.drop_index(op.f("ix_lab_experiments_strategy_version_id"), table_name="experiments", schema="lab")
    op.drop_index(op.f("ix_lab_experiments_project_id"), table_name="experiments", schema="lab")
    op.drop_index(op.f("ix_lab_experiments_organization_id"), table_name="experiments", schema="lab")
    op.drop_index("ix_lab_experiments_org_status", table_name="experiments", schema="lab")
    op.drop_index("ix_lab_experiments_mission_status", table_name="experiments", schema="lab")
    op.drop_index(op.f("ix_lab_experiments_hypothesis_id"), table_name="experiments", schema="lab")
    op.drop_index(op.f("ix_lab_experiments_created_at"), table_name="experiments", schema="lab")
    op.drop_table("experiments", schema="lab")
    op.drop_index("ix_lab_tool_calls_tool", table_name="tool_calls", schema="lab")
    op.drop_index(op.f("ix_lab_tool_calls_organization_id"), table_name="tool_calls", schema="lab")
    op.drop_index("ix_lab_tool_calls_mission_created", table_name="tool_calls", schema="lab")
    op.drop_index(op.f("ix_lab_tool_calls_created_at"), table_name="tool_calls", schema="lab")
    op.drop_index(op.f("ix_lab_tool_calls_agent_run_id"), table_name="tool_calls", schema="lab")
    op.drop_table("tool_calls", schema="lab")
    op.drop_index(op.f("ix_lab_model_usage_organization_id"), table_name="model_usage", schema="lab")
    op.drop_index("ix_lab_model_usage_org_created", table_name="model_usage", schema="lab")
    op.drop_index("ix_lab_model_usage_mission", table_name="model_usage", schema="lab")
    op.drop_index(op.f("ix_lab_model_usage_created_at"), table_name="model_usage", schema="lab")
    op.drop_index(op.f("ix_lab_model_usage_agent_run_id"), table_name="model_usage", schema="lab")
    op.drop_table("model_usage", schema="lab")
    op.drop_index(op.f("ix_lab_hypotheses_project_id"), table_name="hypotheses", schema="lab")
    op.drop_index(op.f("ix_lab_hypotheses_organization_id"), table_name="hypotheses", schema="lab")
    op.drop_index("ix_lab_hypotheses_mission_status", table_name="hypotheses", schema="lab")
    op.drop_index(op.f("ix_lab_hypotheses_created_at"), table_name="hypotheses", schema="lab")
    op.drop_table("hypotheses", schema="lab")
    op.drop_index(op.f("ix_lab_agent_messages_organization_id"), table_name="agent_messages", schema="lab")
    op.drop_index(op.f("ix_lab_agent_messages_mission_id"), table_name="agent_messages", schema="lab")
    op.drop_index(op.f("ix_lab_agent_messages_created_at"), table_name="agent_messages", schema="lab")
    op.drop_table("agent_messages", schema="lab")
    op.drop_index(op.f("ix_lab_strategy_mutations_parent_version_id"), table_name="strategy_mutations", schema="lab")
    op.drop_index(op.f("ix_lab_strategy_mutations_organization_id"), table_name="strategy_mutations", schema="lab")
    op.drop_index(op.f("ix_lab_strategy_mutations_created_at"), table_name="strategy_mutations", schema="lab")
    op.drop_index(op.f("ix_lab_strategy_mutations_child_version_id"), table_name="strategy_mutations", schema="lab")
    op.drop_table("strategy_mutations", schema="lab")
    op.drop_index(op.f("ix_lab_research_sources_research_task_id"), table_name="research_sources", schema="lab")
    op.drop_index("ix_lab_research_sources_project", table_name="research_sources", schema="lab")
    op.drop_index(op.f("ix_lab_research_sources_organization_id"), table_name="research_sources", schema="lab")
    op.drop_index(op.f("ix_lab_research_sources_created_at"), table_name="research_sources", schema="lab")
    op.drop_table("research_sources", schema="lab")
    op.drop_index(op.f("ix_lab_research_events_research_task_id"), table_name="research_events", schema="lab")
    op.drop_index(op.f("ix_lab_research_events_organization_id"), table_name="research_events", schema="lab")
    op.drop_index(op.f("ix_lab_research_events_created_at"), table_name="research_events", schema="lab")
    op.drop_table("research_events", schema="lab")
    op.drop_index("ix_lab_memory_links_target", table_name="memory_links", schema="lab")
    op.drop_index(op.f("ix_lab_memory_links_organization_id"), table_name="memory_links", schema="lab")
    op.drop_index(op.f("ix_lab_memory_links_memory_id"), table_name="memory_links", schema="lab")
    op.drop_index(op.f("ix_lab_memory_links_created_at"), table_name="memory_links", schema="lab")
    op.drop_table("memory_links", schema="lab")
    op.drop_index(op.f("ix_lab_document_chunks_project_id"), table_name="document_chunks", schema="lab")
    op.drop_index(op.f("ix_lab_document_chunks_organization_id"), table_name="document_chunks", schema="lab")
    op.drop_index(op.f("ix_lab_document_chunks_document_version_id"), table_name="document_chunks", schema="lab")
    op.drop_index(op.f("ix_lab_document_chunks_created_at"), table_name="document_chunks", schema="lab")
    op.drop_table("document_chunks", schema="lab")
    op.drop_index(op.f("ix_lab_agent_runs_workflow_run_id"), table_name="agent_runs", schema="lab")
    op.drop_index(op.f("ix_lab_agent_runs_project_id"), table_name="agent_runs", schema="lab")
    op.drop_index(op.f("ix_lab_agent_runs_organization_id"), table_name="agent_runs", schema="lab")
    op.drop_index("ix_lab_agent_runs_org_status", table_name="agent_runs", schema="lab")
    op.drop_index("ix_lab_agent_runs_mission_created", table_name="agent_runs", schema="lab")
    op.drop_index(op.f("ix_lab_agent_runs_created_at"), table_name="agent_runs", schema="lab")
    op.drop_index(op.f("ix_lab_agent_runs_agent_id"), table_name="agent_runs", schema="lab")
    op.drop_table("agent_runs", schema="lab")
    op.drop_index("ix_lab_strategy_versions_status", table_name="strategy_versions", schema="lab")
    op.drop_index(op.f("ix_lab_strategy_versions_organization_id"), table_name="strategy_versions", schema="lab")
    op.drop_index(op.f("ix_lab_strategy_versions_evolution_run_id"), table_name="strategy_versions", schema="lab")
    op.drop_index(op.f("ix_lab_strategy_versions_created_at"), table_name="strategy_versions", schema="lab")
    op.drop_table("strategy_versions", schema="lab")
    op.drop_index(op.f("ix_lab_storage_usage_organization_id"), table_name="storage_usage", schema="lab")
    op.drop_index("ix_lab_storage_usage_org_created", table_name="storage_usage", schema="lab")
    op.drop_index(op.f("ix_lab_storage_usage_created_at"), table_name="storage_usage", schema="lab")
    op.drop_table("storage_usage", schema="lab")
    op.drop_index(op.f("ix_lab_research_tasks_project_id"), table_name="research_tasks", schema="lab")
    op.drop_index(op.f("ix_lab_research_tasks_organization_id"), table_name="research_tasks", schema="lab")
    op.drop_index("ix_lab_research_tasks_org_status", table_name="research_tasks", schema="lab")
    op.drop_index(op.f("ix_lab_research_tasks_mission_id"), table_name="research_tasks", schema="lab")
    op.drop_index(op.f("ix_lab_research_tasks_created_at"), table_name="research_tasks", schema="lab")
    op.drop_table("research_tasks", schema="lab")
    op.drop_index(op.f("ix_lab_reports_project_id"), table_name="reports", schema="lab")
    op.drop_index(op.f("ix_lab_reports_organization_id"), table_name="reports", schema="lab")
    op.drop_index("ix_lab_reports_mission", table_name="reports", schema="lab")
    op.drop_index(op.f("ix_lab_reports_created_at"), table_name="reports", schema="lab")
    op.drop_table("reports", schema="lab")
    op.drop_index(op.f("ix_lab_mission_versions_organization_id"), table_name="mission_versions", schema="lab")
    op.drop_index(op.f("ix_lab_mission_versions_mission_id"), table_name="mission_versions", schema="lab")
    op.drop_index(op.f("ix_lab_mission_versions_created_at"), table_name="mission_versions", schema="lab")
    op.drop_table("mission_versions", schema="lab")
    op.drop_index("ix_lab_memories_scope", table_name="memories", schema="lab")
    op.drop_index("ix_lab_memories_project", table_name="memories", schema="lab")
    op.drop_index(op.f("ix_lab_memories_organization_id"), table_name="memories", schema="lab")
    op.drop_index(op.f("ix_lab_memories_mission_id"), table_name="memories", schema="lab")
    op.drop_index(op.f("ix_lab_memories_created_at"), table_name="memories", schema="lab")
    op.drop_table("memories", schema="lab")
    op.drop_index("ix_lab_graph_edges_target", table_name="graph_edges", schema="lab")
    op.drop_index(op.f("ix_lab_graph_edges_source_id"), table_name="graph_edges", schema="lab")
    op.drop_index(op.f("ix_lab_graph_edges_organization_id"), table_name="graph_edges", schema="lab")
    op.drop_index(op.f("ix_lab_graph_edges_created_at"), table_name="graph_edges", schema="lab")
    op.drop_table("graph_edges", schema="lab")
    op.drop_index(op.f("ix_lab_evolution_runs_project_id"), table_name="evolution_runs", schema="lab")
    op.drop_index(op.f("ix_lab_evolution_runs_organization_id"), table_name="evolution_runs", schema="lab")
    op.drop_index("ix_lab_evolution_runs_mission", table_name="evolution_runs", schema="lab")
    op.drop_index(op.f("ix_lab_evolution_runs_created_at"), table_name="evolution_runs", schema="lab")
    op.drop_table("evolution_runs", schema="lab")
    op.drop_index("ix_lab_events_type", table_name="events", schema="lab")
    op.drop_index(op.f("ix_lab_events_organization_id"), table_name="events", schema="lab")
    op.drop_index("ix_lab_events_org_created", table_name="events", schema="lab")
    op.drop_index(op.f("ix_lab_events_created_at"), table_name="events", schema="lab")
    op.drop_table("events", schema="lab")
    op.drop_index(op.f("ix_lab_document_versions_organization_id"), table_name="document_versions", schema="lab")
    op.drop_index(op.f("ix_lab_document_versions_document_id"), table_name="document_versions", schema="lab")
    op.drop_index(op.f("ix_lab_document_versions_created_at"), table_name="document_versions", schema="lab")
    op.drop_table("document_versions", schema="lab")
    op.drop_index(op.f("ix_lab_dataset_versions_organization_id"), table_name="dataset_versions", schema="lab")
    op.drop_index(op.f("ix_lab_dataset_versions_dataset_id"), table_name="dataset_versions", schema="lab")
    op.drop_index(op.f("ix_lab_dataset_versions_created_at"), table_name="dataset_versions", schema="lab")
    op.drop_table("dataset_versions", schema="lab")
    op.drop_index("ix_lab_approvals_resource", table_name="approvals", schema="lab")
    op.drop_index(op.f("ix_lab_approvals_project_id"), table_name="approvals", schema="lab")
    op.drop_index(op.f("ix_lab_approvals_organization_id"), table_name="approvals", schema="lab")
    op.drop_index("ix_lab_approvals_org_status", table_name="approvals", schema="lab")
    op.drop_index(op.f("ix_lab_approvals_mission_id"), table_name="approvals", schema="lab")
    op.drop_index(op.f("ix_lab_approvals_created_at"), table_name="approvals", schema="lab")
    op.drop_table("approvals", schema="lab")
    op.drop_index(op.f("ix_lab_agent_versions_organization_id"), table_name="agent_versions", schema="lab")
    op.drop_index(op.f("ix_lab_agent_versions_created_at"), table_name="agent_versions", schema="lab")
    op.drop_index(op.f("ix_lab_agent_versions_agent_id"), table_name="agent_versions", schema="lab")
    op.drop_table("agent_versions", schema="lab")
    op.drop_index(op.f("ix_project_members_user_id"), table_name="project_members")
    op.drop_index(op.f("ix_project_members_project_id"), table_name="project_members")
    op.drop_index(op.f("ix_project_members_organization_id"), table_name="project_members")
    op.drop_index(op.f("ix_project_members_created_at"), table_name="project_members")
    op.drop_table("project_members")
    op.drop_index(op.f("ix_lab_strategies_project_id"), table_name="strategies", schema="lab")
    op.drop_index(op.f("ix_lab_strategies_organization_id"), table_name="strategies", schema="lab")
    op.drop_index(op.f("ix_lab_strategies_created_at"), table_name="strategies", schema="lab")
    op.drop_table("strategies", schema="lab")
    op.drop_index(op.f("ix_lab_missions_workspace_id"), table_name="missions", schema="lab")
    op.drop_index(op.f("ix_lab_missions_status"), table_name="missions", schema="lab")
    op.drop_index(op.f("ix_lab_missions_project_id"), table_name="missions", schema="lab")
    op.drop_index("ix_lab_missions_project_created", table_name="missions", schema="lab")
    op.drop_index(op.f("ix_lab_missions_organization_id"), table_name="missions", schema="lab")
    op.drop_index("ix_lab_missions_org_status", table_name="missions", schema="lab")
    op.drop_index(op.f("ix_lab_missions_created_at"), table_name="missions", schema="lab")
    op.drop_table("missions", schema="lab")
    op.drop_index(op.f("ix_lab_mcp_tools_server_id"), table_name="mcp_tools", schema="lab")
    op.drop_index(op.f("ix_lab_mcp_tools_organization_id"), table_name="mcp_tools", schema="lab")
    op.drop_index(op.f("ix_lab_mcp_tools_created_at"), table_name="mcp_tools", schema="lab")
    op.drop_table("mcp_tools", schema="lab")
    op.drop_index(op.f("ix_lab_lessons_organization_id"), table_name="lessons", schema="lab")
    op.drop_index(op.f("ix_lab_lessons_created_at"), table_name="lessons", schema="lab")
    op.drop_table("lessons", schema="lab")
    op.drop_index("ix_lab_graph_nodes_ref", table_name="graph_nodes", schema="lab")
    op.drop_index(op.f("ix_lab_graph_nodes_project_id"), table_name="graph_nodes", schema="lab")
    op.drop_index(op.f("ix_lab_graph_nodes_organization_id"), table_name="graph_nodes", schema="lab")
    op.drop_index(op.f("ix_lab_graph_nodes_created_at"), table_name="graph_nodes", schema="lab")
    op.drop_table("graph_nodes", schema="lab")
    op.drop_index(op.f("ix_lab_documents_project_id"), table_name="documents", schema="lab")
    op.drop_index("ix_lab_documents_project_created", table_name="documents", schema="lab")
    op.drop_index(op.f("ix_lab_documents_organization_id"), table_name="documents", schema="lab")
    op.drop_index(op.f("ix_lab_documents_created_at"), table_name="documents", schema="lab")
    op.drop_table("documents", schema="lab")
    op.drop_index(op.f("ix_lab_datasets_project_id"), table_name="datasets", schema="lab")
    op.drop_index(op.f("ix_lab_datasets_organization_id"), table_name="datasets", schema="lab")
    op.drop_index(op.f("ix_lab_datasets_created_at"), table_name="datasets", schema="lab")
    op.drop_table("datasets", schema="lab")
    op.drop_index(op.f("ix_lab_agents_role"), table_name="agents", schema="lab")
    op.drop_index(op.f("ix_lab_agents_project_id"), table_name="agents", schema="lab")
    op.drop_index(op.f("ix_lab_agents_organization_id"), table_name="agents", schema="lab")
    op.drop_index(op.f("ix_lab_agents_created_at"), table_name="agents", schema="lab")
    op.drop_table("agents", schema="lab")
    op.drop_index(op.f("ix_workspace_members_workspace_id"), table_name="workspace_members")
    op.drop_index(op.f("ix_workspace_members_user_id"), table_name="workspace_members")
    op.drop_index(op.f("ix_workspace_members_organization_id"), table_name="workspace_members")
    op.drop_index(op.f("ix_workspace_members_created_at"), table_name="workspace_members")
    op.drop_table("workspace_members")
    op.drop_index(op.f("ix_sso_connections_organization_id"), table_name="sso_connections")
    op.drop_index(op.f("ix_sso_connections_created_at"), table_name="sso_connections")
    op.drop_table("sso_connections")
    op.drop_index(op.f("ix_projects_workspace_id"), table_name="projects")
    op.drop_index(op.f("ix_projects_organization_id"), table_name="projects")
    op.drop_index("ix_projects_org_created", table_name="projects")
    op.drop_index(op.f("ix_projects_created_at"), table_name="projects")
    op.drop_table("projects")
    op.drop_index(op.f("ix_lab_policy_versions_policy_id"), table_name="policy_versions", schema="lab")
    op.drop_index(op.f("ix_lab_policy_versions_organization_id"), table_name="policy_versions", schema="lab")
    op.drop_index(op.f("ix_lab_policy_versions_created_at"), table_name="policy_versions", schema="lab")
    op.drop_table("policy_versions", schema="lab")
    op.drop_index(op.f("ix_lab_mcp_servers_organization_id"), table_name="mcp_servers", schema="lab")
    op.drop_index(op.f("ix_lab_mcp_servers_created_at"), table_name="mcp_servers", schema="lab")
    op.drop_table("mcp_servers", schema="lab")
    op.drop_index(op.f("ix_workspaces_organization_id"), table_name="workspaces")
    op.drop_index(op.f("ix_workspaces_created_at"), table_name="workspaces")
    op.drop_table("workspaces")
    op.drop_index(op.f("ix_team_members_user_id"), table_name="team_members")
    op.drop_index(op.f("ix_team_members_team_id"), table_name="team_members")
    op.drop_index(op.f("ix_team_members_organization_id"), table_name="team_members")
    op.drop_index(op.f("ix_team_members_created_at"), table_name="team_members")
    op.drop_table("team_members")
    op.drop_index(op.f("ix_service_accounts_organization_id"), table_name="service_accounts")
    op.drop_index(op.f("ix_service_accounts_created_at"), table_name="service_accounts")
    op.drop_table("service_accounts")
    op.drop_index(op.f("ix_role_permissions_role_id"), table_name="role_permissions")
    op.drop_index(op.f("ix_role_permissions_permission_key"), table_name="role_permissions")
    op.drop_table("role_permissions")
    op.drop_index(op.f("ix_refresh_tokens_user_id"), table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_family", table_name="refresh_tokens")
    op.drop_index(op.f("ix_refresh_tokens_created_at"), table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
    op.drop_index(op.f("ix_lab_workflow_steps_run_id"), table_name="workflow_steps", schema="lab")
    op.drop_index(op.f("ix_lab_workflow_steps_organization_id"), table_name="workflow_steps", schema="lab")
    op.drop_index(op.f("ix_lab_workflow_steps_created_at"), table_name="workflow_steps", schema="lab")
    op.drop_table("workflow_steps", schema="lab")
    op.drop_index("ix_lab_workflow_signals_run_name", table_name="workflow_signals", schema="lab")
    op.drop_index(op.f("ix_lab_workflow_signals_organization_id"), table_name="workflow_signals", schema="lab")
    op.drop_index(op.f("ix_lab_workflow_signals_created_at"), table_name="workflow_signals", schema="lab")
    op.drop_table("workflow_signals", schema="lab")
    op.drop_index(op.f("ix_lab_prompt_templates_organization_id"), table_name="prompt_templates", schema="lab")
    op.drop_index(op.f("ix_lab_prompt_templates_name"), table_name="prompt_templates", schema="lab")
    op.drop_index(op.f("ix_lab_prompt_templates_created_at"), table_name="prompt_templates", schema="lab")
    op.drop_table("prompt_templates", schema="lab")
    op.drop_index(op.f("ix_lab_policies_organization_id"), table_name="policies", schema="lab")
    op.drop_index(op.f("ix_lab_policies_created_at"), table_name="policies", schema="lab")
    op.drop_table("policies", schema="lab")
    op.drop_index(op.f("ix_lab_benchmark_results_run_id"), table_name="benchmark_results", schema="lab")
    op.drop_index(op.f("ix_lab_benchmark_results_organization_id"), table_name="benchmark_results", schema="lab")
    op.drop_index(op.f("ix_lab_benchmark_results_created_at"), table_name="benchmark_results", schema="lab")
    op.drop_table("benchmark_results", schema="lab")
    op.drop_index(op.f("ix_usage_records_organization_id"), table_name="usage_records")
    op.drop_index("ix_usage_records_org_period", table_name="usage_records")
    op.drop_index(op.f("ix_usage_records_created_at"), table_name="usage_records")
    op.drop_table("usage_records")
    op.drop_index(op.f("ix_teams_organization_id"), table_name="teams")
    op.drop_index(op.f("ix_teams_created_at"), table_name="teams")
    op.drop_table("teams")
    op.drop_index(op.f("ix_subscriptions_organization_id"), table_name="subscriptions")
    op.drop_index(op.f("ix_subscriptions_created_at"), table_name="subscriptions")
    op.drop_table("subscriptions")
    op.drop_index(op.f("ix_roles_organization_id"), table_name="roles")
    op.drop_index(op.f("ix_roles_created_at"), table_name="roles")
    op.drop_table("roles")
    op.drop_index(op.f("ix_organization_quotas_organization_id"), table_name="organization_quotas")
    op.drop_index(op.f("ix_organization_quotas_created_at"), table_name="organization_quotas")
    op.drop_table("organization_quotas")
    op.drop_index("ix_lab_workflow_runs_status_wake", table_name="workflow_runs", schema="lab")
    op.drop_index("ix_lab_workflow_runs_status_lease", table_name="workflow_runs", schema="lab")
    op.drop_index(op.f("ix_lab_workflow_runs_organization_id"), table_name="workflow_runs", schema="lab")
    op.drop_index(op.f("ix_lab_workflow_runs_created_at"), table_name="workflow_runs", schema="lab")
    op.drop_table("workflow_runs", schema="lab")
    op.drop_index(op.f("ix_lab_model_configs_organization_id"), table_name="model_configs", schema="lab")
    op.drop_index(op.f("ix_lab_model_configs_created_at"), table_name="model_configs", schema="lab")
    op.drop_table("model_configs", schema="lab")
    op.drop_index(op.f("ix_lab_environments_organization_id"), table_name="environments", schema="lab")
    op.drop_index(op.f("ix_lab_environments_created_at"), table_name="environments", schema="lab")
    op.drop_table("environments", schema="lab")
    op.drop_index("ix_lab_benchmark_runs_suite_subject", table_name="benchmark_runs", schema="lab")
    op.drop_index(op.f("ix_lab_benchmark_runs_organization_id"), table_name="benchmark_runs", schema="lab")
    op.drop_index(op.f("ix_lab_benchmark_runs_created_at"), table_name="benchmark_runs", schema="lab")
    op.drop_table("benchmark_runs", schema="lab")
    op.drop_index(op.f("ix_invoice_references_organization_id"), table_name="invoice_references")
    op.drop_index(op.f("ix_invoice_references_created_at"), table_name="invoice_references")
    op.drop_table("invoice_references")
    op.drop_index(op.f("ix_idempotency_keys_organization_id"), table_name="idempotency_keys")
    op.drop_index("ix_idempotency_keys_expires", table_name="idempotency_keys")
    op.drop_index(op.f("ix_idempotency_keys_created_at"), table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
    op.drop_index(op.f("ix_plans_created_at"), table_name="plans")
    op.drop_table("plans")
    op.drop_table("permissions")
    op.drop_index(op.f("ix_lab_evaluators_created_at"), table_name="evaluators", schema="lab")
    op.drop_table("evaluators", schema="lab")
