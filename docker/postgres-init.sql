-- ============================================================================
-- Runs once, on first initialisation of the Postgres data volume
-- (mounted into /docker-entrypoint-initdb.d; executed by psql as the superuser
-- against the POSTGRES_DB database). LOCAL DEVELOPMENT ONLY — the passwords
-- below are dev-only defaults; production databases are provisioned by your
-- platform.
--
-- Provisions:
--   * the pgvector extension
--   * aegis_app  — NOLOGIN group role that owns the RLS grants (created here so
--                  the login role can be enrolled before migrations run; the
--                  migration's CREATE ROLE is a no-op when it already exists)
--   * aegis      — the LOGIN role the API and worker connect as (RLS enforced)
--   * temporal   — LOGIN role for the Temporal server (docker-compose `temporal`
--                  service) owning its two databases, `temporal` and
--                  `temporal_visibility`, on this same server. No CREATEDB, no
--                  access to the `aegis` database; auto-setup only installs the
--                  schemas (SKIP_DB_CREATE=true).
-- The superuser (postgres) remains the owner used for migrations, seeding and the
-- identity layer, which bypass RLS by design.
--
-- The script is idempotent. For a volume created before the Temporal role and
-- databases were added, apply it once by hand:
--   docker compose exec -T db psql -U postgres -d aegis -v ON_ERROR_STOP=1 < docker/postgres-init.sql
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

  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'temporal') THEN
    CREATE ROLE temporal LOGIN PASSWORD 'temporal';
  END IF;
END
$$;

-- Least privilege: only the roles that need the application database may connect
-- to it (PUBLIC has CONNECT by default). The superuser is unaffected.
REVOKE CONNECT ON DATABASE aegis FROM PUBLIC;
GRANT CONNECT ON DATABASE aegis TO aegis;

-- Temporal persistence + visibility databases (CREATE DATABASE cannot run inside
-- a DO block; \gexec runs each generated statement only when it is missing).
SELECT 'CREATE DATABASE temporal OWNER temporal'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'temporal')
\gexec
SELECT 'CREATE DATABASE temporal_visibility OWNER temporal'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'temporal_visibility')
\gexec

REVOKE CONNECT ON DATABASE temporal FROM PUBLIC;
REVOKE CONNECT ON DATABASE temporal_visibility FROM PUBLIC;
GRANT CONNECT ON DATABASE temporal, temporal_visibility TO temporal;
