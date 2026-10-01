"""Platform: runtime guard, runtime policies (policy studio), continuous assurance, findings workflow,
evidence exports, contact requests.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-01 07:00:00+00:00

* ``runtime_events`` — normalized agent telemetry (schema ``aegis.runtime.v1``) with the guard's decision;
  ``event_id`` is unique per workspace (idempotent ingestion).
* ``runtime_policies`` / ``runtime_policy_versions`` / ``runtime_policy_assignments`` — versioned YAML policies;
  versions are immutable (trigger), publishing moves a pointer, rollback re-publishes an older version.
* ``runtime_approvals`` — human approvals requested by enforce-mode decisions.
* ``assurance_schedules`` / ``assurance_triggers`` — recurring and change-driven audits.
* ``evidence_exports`` — record of every exported evidence package (root hash + signature).
* ``finding_comments`` (append-only) and finding workflow columns (source, tags, priority, SLA, risk acceptance).
* ``contact_requests`` — website sales/security-review requests (owner connection only; no tenant access).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

TENANT_PREDICATE = "(organization_id = aegis.current_org_id() OR aegis.is_org_member(organization_id))"
PURGE_ALLOWED = (
    "coalesce(current_setting('aegis.allow_evidence_delete', true), '') = 'on' "
    "AND ((SELECT rolsuper FROM pg_roles WHERE rolname = current_user) "
    "OR NOT pg_has_role(current_user, 'aegis_app', 'MEMBER'))"
)
TENANT_TABLES = (
    "assurance_schedules",
    "assurance_triggers",
    "evidence_exports",
    "finding_comments",
    "runtime_approvals",
    "runtime_events",
    "runtime_policies",
    "runtime_policy_assignments",
    "runtime_policy_versions",
)

revision: str = '0004'
down_revision: str | None = '0003'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('contact_requests',
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('company', sa.String(length=200), nullable=True),
    sa.Column('message', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('source_ip_hash', sa.String(length=64), nullable=True),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_contact_requests'))
    )
    op.create_index(op.f('ix_contact_requests_created_at'), 'contact_requests', ['created_at'], unique=False)
    op.create_table('runtime_policies',
    sa.Column('key', sa.String(length=80), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('category', sa.String(length=48), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('published_version_id', sa.Uuid(), nullable=True),
    sa.Column('latest_version', sa.Integer(), nullable=False),
    sa.Column('template_key', sa.String(length=80), nullable=True),
    sa.Column('created_by_id', sa.Uuid(), nullable=True),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('fk_runtime_policies_created_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_runtime_policies_organization_id_organizations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_runtime_policies')),
    sa.UniqueConstraint('organization_id', 'key', name='uq_runtime_policies_org_key')
    )
    op.create_index(op.f('ix_runtime_policies_created_at'), 'runtime_policies', ['created_at'], unique=False)
    op.create_index(op.f('ix_runtime_policies_organization_id'), 'runtime_policies', ['organization_id'], unique=False)
    op.create_table('runtime_policy_versions',
    sa.Column('policy_id', sa.Uuid(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('source_yaml', sa.Text(), nullable=False),
    sa.Column('compiled', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('checksum', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('change_note', sa.String(length=500), nullable=True),
    sa.Column('created_by_id', sa.Uuid(), nullable=True),
    sa.Column('published_by_id', sa.Uuid(), nullable=True),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('fk_runtime_policy_versions_created_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_runtime_policy_versions_organization_id_organizations'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['policy_id'], ['runtime_policies.id'], name=op.f('fk_runtime_policy_versions_policy_id_runtime_policies'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['published_by_id'], ['users.id'], name=op.f('fk_runtime_policy_versions_published_by_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_runtime_policy_versions')),
    sa.UniqueConstraint('policy_id', 'version', name='uq_runtime_policy_versions_policy_version')
    )
    op.create_index(op.f('ix_runtime_policy_versions_created_at'), 'runtime_policy_versions', ['created_at'], unique=False)
    op.create_index(op.f('ix_runtime_policy_versions_organization_id'), 'runtime_policy_versions', ['organization_id'], unique=False)
    op.create_index(op.f('ix_runtime_policy_versions_policy_id'), 'runtime_policy_versions', ['policy_id'], unique=False)
    op.create_table('runtime_policy_assignments',
    sa.Column('policy_id', sa.Uuid(), nullable=False),
    sa.Column('scope_type', sa.String(length=16), nullable=False),
    sa.Column('scope_key', sa.String(length=64), nullable=False),
    sa.Column('system_id', sa.Uuid(), nullable=True),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('created_by_id', sa.Uuid(), nullable=True),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('fk_runtime_policy_assignments_created_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_runtime_policy_assignments_organization_id_organizations'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['policy_id'], ['runtime_policies.id'], name=op.f('fk_runtime_policy_assignments_policy_id_runtime_policies'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['system_id'], ['ai_systems.id'], name=op.f('fk_runtime_policy_assignments_system_id_ai_systems'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_runtime_policy_assignments')),
    sa.UniqueConstraint('policy_id', 'scope_type', 'scope_key', name='uq_runtime_policy_assignments_scope')
    )
    op.create_index(op.f('ix_runtime_policy_assignments_created_at'), 'runtime_policy_assignments', ['created_at'], unique=False)
    op.create_index(op.f('ix_runtime_policy_assignments_organization_id'), 'runtime_policy_assignments', ['organization_id'], unique=False)
    op.create_index(op.f('ix_runtime_policy_assignments_policy_id'), 'runtime_policy_assignments', ['policy_id'], unique=False)
    op.create_index(op.f('ix_runtime_policy_assignments_system_id'), 'runtime_policy_assignments', ['system_id'], unique=False)
    op.create_table('assurance_schedules',
    sa.Column('system_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('interval_hours', sa.Integer(), nullable=True),
    sa.Column('trigger_on', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('categories', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('intensity', sa.String(length=16), nullable=False),
    sa.Column('policy_version_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('next_run_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_audit_id', sa.Uuid(), nullable=True),
    sa.Column('created_by_id', sa.Uuid(), nullable=True),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('fk_assurance_schedules_created_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['last_audit_id'], ['audits.id'], name=op.f('fk_assurance_schedules_last_audit_id_audits'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_assurance_schedules_organization_id_organizations'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['system_id'], ['ai_systems.id'], name=op.f('fk_assurance_schedules_system_id_ai_systems'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_assurance_schedules'))
    )
    op.create_index(op.f('ix_assurance_schedules_created_at'), 'assurance_schedules', ['created_at'], unique=False)
    op.create_index('ix_assurance_schedules_due', 'assurance_schedules', ['enabled', 'next_run_at'], unique=False)
    op.create_index(op.f('ix_assurance_schedules_organization_id'), 'assurance_schedules', ['organization_id'], unique=False)
    op.create_index(op.f('ix_assurance_schedules_system_id'), 'assurance_schedules', ['system_id'], unique=False)
    op.create_table('evidence_exports',
    sa.Column('audit_id', sa.Uuid(), nullable=True),
    sa.Column('scope', sa.String(length=32), nullable=False),
    sa.Column('root_hash', sa.String(length=64), nullable=False),
    sa.Column('artifact_count', sa.Integer(), nullable=False),
    sa.Column('integrity_status', sa.String(length=24), nullable=False),
    sa.Column('signature', sa.String(length=200), nullable=True),
    sa.Column('key_id', sa.String(length=64), nullable=True),
    sa.Column('manifest', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_by_id', sa.Uuid(), nullable=True),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['audit_id'], ['audits.id'], name=op.f('fk_evidence_exports_audit_id_audits'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('fk_evidence_exports_created_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_evidence_exports_organization_id_organizations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_evidence_exports'))
    )
    op.create_index(op.f('ix_evidence_exports_audit_id'), 'evidence_exports', ['audit_id'], unique=False)
    op.create_index(op.f('ix_evidence_exports_created_at'), 'evidence_exports', ['created_at'], unique=False)
    op.create_index(op.f('ix_evidence_exports_organization_id'), 'evidence_exports', ['organization_id'], unique=False)
    op.create_table('assurance_triggers',
    sa.Column('system_id', sa.Uuid(), nullable=False),
    sa.Column('schedule_id', sa.Uuid(), nullable=True),
    sa.Column('event_type', sa.String(length=32), nullable=False),
    sa.Column('ref', sa.String(length=200), nullable=False),
    sa.Column('source', sa.String(length=32), nullable=False),
    sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('categories', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('selection_reason', sa.Text(), nullable=True),
    sa.Column('audit_id', sa.Uuid(), nullable=True),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['audit_id'], ['audits.id'], name=op.f('fk_assurance_triggers_audit_id_audits'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_assurance_triggers_organization_id_organizations'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['schedule_id'], ['assurance_schedules.id'], name=op.f('fk_assurance_triggers_schedule_id_assurance_schedules'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['system_id'], ['ai_systems.id'], name=op.f('fk_assurance_triggers_system_id_ai_systems'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_assurance_triggers')),
    sa.UniqueConstraint('organization_id', 'system_id', 'event_type', 'ref', name='uq_assurance_triggers_ref')
    )
    op.create_index(op.f('ix_assurance_triggers_created_at'), 'assurance_triggers', ['created_at'], unique=False)
    op.create_index(op.f('ix_assurance_triggers_organization_id'), 'assurance_triggers', ['organization_id'], unique=False)
    op.create_index(op.f('ix_assurance_triggers_system_id'), 'assurance_triggers', ['system_id'], unique=False)
    op.create_table('finding_comments',
    sa.Column('finding_id', sa.Uuid(), nullable=False),
    sa.Column('author_id', sa.Uuid(), nullable=True),
    sa.Column('author_label', sa.String(length=320), nullable=True),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['author_id'], ['users.id'], name=op.f('fk_finding_comments_author_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['finding_id'], ['findings.id'], name=op.f('fk_finding_comments_finding_id_findings'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_finding_comments_organization_id_organizations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_finding_comments'))
    )
    op.create_index(op.f('ix_finding_comments_created_at'), 'finding_comments', ['created_at'], unique=False)
    op.create_index(op.f('ix_finding_comments_finding_id'), 'finding_comments', ['finding_id'], unique=False)
    op.create_index(op.f('ix_finding_comments_organization_id'), 'finding_comments', ['organization_id'], unique=False)
    op.create_table('runtime_events',
    sa.Column('system_id', sa.Uuid(), nullable=False),
    sa.Column('event_id', sa.String(length=80), nullable=False),
    sa.Column('schema_version', sa.String(length=24), nullable=False),
    sa.Column('event_type', sa.String(length=48), nullable=False),
    sa.Column('source', sa.String(length=24), nullable=False),
    sa.Column('environment', sa.String(length=24), nullable=True),
    sa.Column('agent_name', sa.String(length=160), nullable=True),
    sa.Column('actor', sa.String(length=160), nullable=True),
    sa.Column('session_id', sa.String(length=120), nullable=True),
    sa.Column('trace_id', sa.String(length=80), nullable=True),
    sa.Column('span_id', sa.String(length=64), nullable=True),
    sa.Column('parent_span_id', sa.String(length=64), nullable=True),
    sa.Column('tool_name', sa.String(length=160), nullable=True),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('signals', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('mode', sa.String(length=12), nullable=False),
    sa.Column('decision', sa.String(length=20), nullable=False),
    sa.Column('effective_decision', sa.String(length=20), nullable=False),
    sa.Column('decision_reason', sa.Text(), nullable=True),
    sa.Column('policy_matches', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('risk_level', sa.String(length=16), nullable=True),
    sa.Column('finding_id', sa.Uuid(), nullable=True),
    sa.Column('approval_id', sa.Uuid(), nullable=True),
    sa.Column('evidence_id', sa.Uuid(), nullable=True),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['evidence_id'], ['evidence.id'], name=op.f('fk_runtime_events_evidence_id_evidence'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['finding_id'], ['findings.id'], name=op.f('fk_runtime_events_finding_id_findings'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_runtime_events_organization_id_organizations'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['system_id'], ['ai_systems.id'], name=op.f('fk_runtime_events_system_id_ai_systems'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_runtime_events')),
    sa.UniqueConstraint('organization_id', 'event_id', name='uq_runtime_events_org_event')
    )
    op.create_index(op.f('ix_runtime_events_created_at'), 'runtime_events', ['created_at'], unique=False)
    op.create_index('ix_runtime_events_org_decision', 'runtime_events', ['organization_id', 'decision'], unique=False)
    op.create_index('ix_runtime_events_org_occurred', 'runtime_events', ['organization_id', 'occurred_at'], unique=False)
    op.create_index('ix_runtime_events_org_type', 'runtime_events', ['organization_id', 'event_type'], unique=False)
    op.create_index(op.f('ix_runtime_events_organization_id'), 'runtime_events', ['organization_id'], unique=False)
    op.create_index('ix_runtime_events_system_occurred', 'runtime_events', ['system_id', 'occurred_at'], unique=False)
    op.create_index('ix_runtime_events_trace', 'runtime_events', ['organization_id', 'trace_id'], unique=False)
    op.create_table('runtime_approvals',
    sa.Column('system_id', sa.Uuid(), nullable=False),
    sa.Column('runtime_event_id', sa.Uuid(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('summary', sa.String(length=500), nullable=False),
    sa.Column('rule_ref', sa.String(length=200), nullable=True),
    sa.Column('request', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('decided_by_id', sa.Uuid(), nullable=True),
    sa.Column('decided_by_label', sa.String(length=320), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('decision_note', sa.Text(), nullable=True),
    sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['decided_by_id'], ['users.id'], name=op.f('fk_runtime_approvals_decided_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_runtime_approvals_organization_id_organizations'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['runtime_event_id'], ['runtime_events.id'], name=op.f('fk_runtime_approvals_runtime_event_id_runtime_events'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['system_id'], ['ai_systems.id'], name=op.f('fk_runtime_approvals_system_id_ai_systems'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_runtime_approvals'))
    )
    op.create_index(op.f('ix_runtime_approvals_created_at'), 'runtime_approvals', ['created_at'], unique=False)
    op.create_index('ix_runtime_approvals_org_status', 'runtime_approvals', ['organization_id', 'status'], unique=False)
    op.create_index(op.f('ix_runtime_approvals_organization_id'), 'runtime_approvals', ['organization_id'], unique=False)
    op.create_index(op.f('ix_runtime_approvals_system_id'), 'runtime_approvals', ['system_id'], unique=False)
    op.add_column('ai_systems', sa.Column('runtime_mode', sa.String(length=12), server_default='observe', nullable=False))
    op.add_column('ai_systems', sa.Column('baseline_audit_id', sa.Uuid(), nullable=True))
    op.create_foreign_key(op.f('fk_ai_systems_baseline_audit_id_audits'), 'ai_systems', 'audits', ['baseline_audit_id'], ['id'], ondelete='SET NULL', use_alter=True)
    op.add_column('findings', sa.Column('source', sa.String(length=16), server_default='audit', nullable=False))
    op.add_column('findings', sa.Column('tags', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False))
    op.add_column('findings', sa.Column('priority', sa.String(length=4), nullable=True))
    op.add_column('findings', sa.Column('sla_due_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('findings', sa.Column('risk_acceptance', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('findings', sa.Column('risk_accepted_until', sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f('ix_findings_risk_accepted_until'), 'findings', ['risk_accepted_until'], unique=False)
    op.create_index(op.f('ix_job_runs_created_at'), 'job_runs', ['created_at'], unique=False)

    # --- tenant isolation -----------------------------------------------------------------------
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {table} "
            f"USING {TENANT_PREDICATE} WITH CHECK {TENANT_PREDICATE}"
        )
    # Website requests are not tenant data: RLS on, no policy → only the owner connection can access them.
    op.execute("ALTER TABLE contact_requests ENABLE ROW LEVEL SECURITY")

    # --- immutability -----------------------------------------------------------------------------
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION aegis.protect_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' AND {PURGE_ALLOWED} THEN
            RETURN OLD;
          END IF;
          RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = 'insufficient_privilege';
        END
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER finding_comments_append_only BEFORE UPDATE OR DELETE ON finding_comments "
        "FOR EACH ROW EXECUTE FUNCTION aegis.protect_append_only()"
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION aegis.protect_policy_version() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            IF {PURGE_ALLOWED} THEN
              RETURN OLD;
            END IF;
            RAISE EXCEPTION 'runtime policy versions are immutable' USING ERRCODE = 'insufficient_privilege';
          END IF;
          IF NEW.source_yaml IS DISTINCT FROM OLD.source_yaml
             OR NEW.compiled IS DISTINCT FROM OLD.compiled
             OR NEW.checksum IS DISTINCT FROM OLD.checksum
             OR NEW.version IS DISTINCT FROM OLD.version
             OR NEW.policy_id IS DISTINCT FROM OLD.policy_id THEN
            RAISE EXCEPTION 'runtime policy versions are immutable' USING ERRCODE = 'insufficient_privilege';
          END IF;
          RETURN NEW;
        END
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER runtime_policy_versions_immutable BEFORE UPDATE OR DELETE ON runtime_policy_versions "
        "FOR EACH ROW EXECUTE FUNCTION aegis.protect_policy_version()"
    )

    # --- backfills --------------------------------------------------------------------------------
    op.execute(
        "UPDATE findings SET sla_due_at = created_at + (CASE severity WHEN 'critical' THEN interval '7 days' "
        "WHEN 'high' THEN interval '30 days' WHEN 'medium' THEN interval '90 days' ELSE interval '180 days' END) "
        "WHERE sla_due_at IS NULL"
    )
    op.execute("UPDATE findings SET source = 'redteam' WHERE evaluator_key = 'redteam.engine'")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO aegis_app")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS runtime_policy_versions_immutable ON runtime_policy_versions")
    op.execute("DROP TRIGGER IF EXISTS finding_comments_append_only ON finding_comments")
    op.execute("DROP FUNCTION IF EXISTS aegis.protect_policy_version()")
    op.drop_index(op.f('ix_job_runs_created_at'), table_name='job_runs')
    op.drop_index(op.f('ix_findings_risk_accepted_until'), table_name='findings')
    op.drop_column('findings', 'risk_accepted_until')
    op.drop_column('findings', 'risk_acceptance')
    op.drop_column('findings', 'sla_due_at')
    op.drop_column('findings', 'priority')
    op.drop_column('findings', 'tags')
    op.drop_column('findings', 'source')
    op.drop_constraint(op.f('fk_ai_systems_baseline_audit_id_audits'), 'ai_systems', type_='foreignkey')
    op.drop_column('ai_systems', 'baseline_audit_id')
    op.drop_column('ai_systems', 'runtime_mode')
    op.drop_index(op.f('ix_runtime_approvals_system_id'), table_name='runtime_approvals')
    op.drop_index(op.f('ix_runtime_approvals_organization_id'), table_name='runtime_approvals')
    op.drop_index('ix_runtime_approvals_org_status', table_name='runtime_approvals')
    op.drop_index(op.f('ix_runtime_approvals_created_at'), table_name='runtime_approvals')
    op.drop_table('runtime_approvals')
    op.drop_index('ix_runtime_events_trace', table_name='runtime_events')
    op.drop_index('ix_runtime_events_system_occurred', table_name='runtime_events')
    op.drop_index(op.f('ix_runtime_events_organization_id'), table_name='runtime_events')
    op.drop_index('ix_runtime_events_org_type', table_name='runtime_events')
    op.drop_index('ix_runtime_events_org_occurred', table_name='runtime_events')
    op.drop_index('ix_runtime_events_org_decision', table_name='runtime_events')
    op.drop_index(op.f('ix_runtime_events_created_at'), table_name='runtime_events')
    op.drop_table('runtime_events')
    op.drop_index(op.f('ix_finding_comments_organization_id'), table_name='finding_comments')
    op.drop_index(op.f('ix_finding_comments_finding_id'), table_name='finding_comments')
    op.drop_index(op.f('ix_finding_comments_created_at'), table_name='finding_comments')
    op.drop_table('finding_comments')
    op.drop_index(op.f('ix_assurance_triggers_system_id'), table_name='assurance_triggers')
    op.drop_index(op.f('ix_assurance_triggers_organization_id'), table_name='assurance_triggers')
    op.drop_index(op.f('ix_assurance_triggers_created_at'), table_name='assurance_triggers')
    op.drop_table('assurance_triggers')
    op.drop_index(op.f('ix_evidence_exports_organization_id'), table_name='evidence_exports')
    op.drop_index(op.f('ix_evidence_exports_created_at'), table_name='evidence_exports')
    op.drop_index(op.f('ix_evidence_exports_audit_id'), table_name='evidence_exports')
    op.drop_table('evidence_exports')
    op.drop_index(op.f('ix_assurance_schedules_system_id'), table_name='assurance_schedules')
    op.drop_index(op.f('ix_assurance_schedules_organization_id'), table_name='assurance_schedules')
    op.drop_index('ix_assurance_schedules_due', table_name='assurance_schedules')
    op.drop_index(op.f('ix_assurance_schedules_created_at'), table_name='assurance_schedules')
    op.drop_table('assurance_schedules')
    op.drop_index(op.f('ix_runtime_policy_assignments_system_id'), table_name='runtime_policy_assignments')
    op.drop_index(op.f('ix_runtime_policy_assignments_policy_id'), table_name='runtime_policy_assignments')
    op.drop_index(op.f('ix_runtime_policy_assignments_organization_id'), table_name='runtime_policy_assignments')
    op.drop_index(op.f('ix_runtime_policy_assignments_created_at'), table_name='runtime_policy_assignments')
    op.drop_table('runtime_policy_assignments')
    op.drop_index(op.f('ix_runtime_policy_versions_policy_id'), table_name='runtime_policy_versions')
    op.drop_index(op.f('ix_runtime_policy_versions_organization_id'), table_name='runtime_policy_versions')
    op.drop_index(op.f('ix_runtime_policy_versions_created_at'), table_name='runtime_policy_versions')
    op.drop_table('runtime_policy_versions')
    op.drop_index(op.f('ix_runtime_policies_organization_id'), table_name='runtime_policies')
    op.drop_index(op.f('ix_runtime_policies_created_at'), table_name='runtime_policies')
    op.drop_table('runtime_policies')
    op.drop_index(op.f('ix_contact_requests_created_at'), table_name='contact_requests')
    op.drop_table('contact_requests')
    op.execute("DROP FUNCTION IF EXISTS aegis.protect_append_only()")
