"""Harden evidence immutability: every recorded field is frozen, not just the hashes.

Before this revision the trigger protected the hash columns and ``content`` only. ``title``, ``source_uri`` and
``storage_key`` are part of the hashed body, so edits to them were detectable by chain verification but not
rejected; ``seq``, ``confidence_*``, ``sensitive`` and the owning ids could be rewritten silently. Now only the
lifecycle columns may change: ``legal_hold``, ``deleted_at`` (soft delete) and a one-way purge
(``purged_at`` NULL → set, which may clear ``content``/``sensitive_ciphertext``; never while on legal hold).

Revision ID: 0004
Revises: 0003
"""

from __future__ import annotations

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels = None
depends_on = None

_PREVIOUS = """
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
     OR NEW.prev_hash IS DISTINCT FROM OLD.prev_hash
     OR NEW.chain_scope IS DISTINCT FROM OLD.chain_scope THEN
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

_HARDENED = """
CREATE OR REPLACE FUNCTION aegis.protect_evidence() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF coalesce(current_setting('aegis.allow_evidence_delete', true), '') = 'on' AND NOT OLD.legal_hold THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'evidence is append-only (use soft delete / retention purge)'
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NEW.id IS DISTINCT FROM OLD.id
     OR NEW.organization_id IS DISTINCT FROM OLD.organization_id
     OR NEW.audit_id IS DISTINCT FROM OLD.audit_id
     OR NEW.system_id IS DISTINCT FROM OLD.system_id
     OR NEW.chain_scope IS DISTINCT FROM OLD.chain_scope
     OR NEW.seq IS DISTINCT FROM OLD.seq
     OR NEW.kind IS DISTINCT FROM OLD.kind
     OR NEW.title IS DISTINCT FROM OLD.title
     OR NEW.source_uri IS DISTINCT FROM OLD.source_uri
     OR NEW.storage_key IS DISTINCT FROM OLD.storage_key
     OR NEW.sensitive IS DISTINCT FROM OLD.sensitive
     OR NEW.confidence_level IS DISTINCT FROM OLD.confidence_level
     OR NEW.confidence_reasons IS DISTINCT FROM OLD.confidence_reasons
     OR NEW.content_hash IS DISTINCT FROM OLD.content_hash
     OR NEW.prev_hash IS DISTINCT FROM OLD.prev_hash
     OR NEW.chain_hash IS DISTINCT FROM OLD.chain_hash
     OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
    RAISE EXCEPTION 'evidence records are immutable' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF OLD.purged_at IS NOT NULL AND NEW.purged_at IS DISTINCT FROM OLD.purged_at THEN
    RAISE EXCEPTION 'evidence purge is irreversible' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF OLD.purged_at IS NULL AND NEW.purged_at IS NOT NULL AND OLD.legal_hold THEN
    RAISE EXCEPTION 'evidence under legal hold cannot be purged' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF (NEW.content IS DISTINCT FROM OLD.content OR NEW.sensitive_ciphertext IS DISTINCT FROM OLD.sensitive_ciphertext)
     AND NOT (OLD.purged_at IS NULL AND NEW.purged_at IS NOT NULL) THEN
    RAISE EXCEPTION 'evidence content is immutable' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END
$$;
"""


def upgrade() -> None:
    op.execute(_HARDENED)


def downgrade() -> None:
    op.execute(_PREVIOUS)
