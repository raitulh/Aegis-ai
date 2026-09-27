-- ============================================================================
-- Runs once, on first initialisation of the Postgres data volume
-- (mounted into /docker-entrypoint-initdb.d). Executes as the superuser against
-- the POSTGRES_DB database.
--
-- Provisions:
--   * the pgvector extension
--   * aegis_app  — NOLOGIN group role that owns the RLS grants (created here so
--                  the login role can be enrolled before migrations run; the
--                  migration's CREATE ROLE is a no-op when it already exists)
--   * aegis      — the LOGIN role the API and worker connect as (RLS enforced)
-- The superuser (postgres) remains the owner used for migrations, seeding and the
-- identity layer, which bypass RLS by design.
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS vector;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aegis_app') THEN
    CREATE ROLE aegis_app NOLOGIN;
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aegis') THEN
    CREATE ROLE aegis LOGIN PASSWORD 'aegis';
  END IF;

  GRANT aegis_app TO aegis;
END
$$;

GRANT CONNECT ON DATABASE aegis TO aegis;
