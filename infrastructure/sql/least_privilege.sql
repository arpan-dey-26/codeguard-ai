-- Least-privilege database roles for CodeGuard AI.
--
-- Run this ONCE, as a superuser, after the first `alembic upgrade head`.
-- It must run after the migration because it grants on tables that have to
-- exist first; the DEFAULT PRIVILEGES statements at the end cover tables
-- created by later migrations.
--
-- Three roles, because they need three different things:
--
--   codeguard_migrate   owns the schema. DDL. Used only by the migration step.
--   codeguard_app       DML on the application tables. Used by the API and the
--                       worker. Cannot create, alter or drop anything.
--   codeguard_readonly  SELECT only. For ad-hoc investigation and for a BI tool
--                       that should never be able to change a row.
--
-- **Why this is worth doing.** Until these roles exist, the application
-- connects as the owner, which means an SQL injection or a compromised process
-- can `DROP TABLE findings` — and, more quietly, can `ALTER TABLE` to disable a
-- constraint the application relies on. Separating DDL from DML bounds what
-- goes wrong to what can be restored from a backup of the *data*, which is the
-- thing backups are good at.
--
-- The application does not notice: it issues INSERT, UPDATE, DELETE and SELECT
-- and nothing else. That was verified by reading every statement in
-- `app/db/repositories/`, not assumed.

\set ON_ERROR_STOP on

-- ---------------------------------------------------------------------------
-- Roles
-- ---------------------------------------------------------------------------
-- Passwords are placeholders. Set real ones through your secret store and
-- rotate them with ALTER ROLE ... PASSWORD; never leave these values in place.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'codeguard_migrate') THEN
        CREATE ROLE codeguard_migrate LOGIN PASSWORD 'CHANGE_ME_MIGRATE';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'codeguard_app') THEN
        CREATE ROLE codeguard_app LOGIN PASSWORD 'CHANGE_ME_APP';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'codeguard_readonly') THEN
        CREATE ROLE codeguard_readonly LOGIN PASSWORD 'CHANGE_ME_READONLY';
    END IF;
END
$$;

-- ---------------------------------------------------------------------------
-- Schema ownership
-- ---------------------------------------------------------------------------
ALTER SCHEMA public OWNER TO codeguard_migrate;

-- PUBLIC can create objects in `public` by default in PostgreSQL < 15, and the
-- habit of relying on that outlives the version. Revoking it explicitly means
-- the behaviour is the same on every server this is deployed to.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO codeguard_app, codeguard_readonly;

-- ---------------------------------------------------------------------------
-- Application role: data, never structure
-- ---------------------------------------------------------------------------
GRANT SELECT, INSERT, UPDATE, DELETE
    ON ALL TABLES IN SCHEMA public
    TO codeguard_app;

-- BIGSERIAL primary keys need the sequence.
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO codeguard_app;

-- Deliberately NOT granted to codeguard_app:
--   CREATE / ALTER / DROP   — schema changes belong to the migration role
--   TRUNCATE                — bypasses triggers and cannot be rolled back
--                             usefully at scale; nothing in the application
--                             issues it
--   REFERENCES              — the app never creates a foreign key
--   SUPERUSER, CREATEDB, CREATEROLE, REPLICATION

-- ---------------------------------------------------------------------------
-- Read-only role
-- ---------------------------------------------------------------------------
GRANT SELECT ON ALL TABLES IN SCHEMA public TO codeguard_readonly;

-- The session and identity tables are excluded. `user_sessions.token_hash`
-- and `oauth_states.state_hash` are credential material — hashed, but there is
-- no investigative question a BI tool should be answering from them — and
-- `users.email` is personal data. Least privilege applies to reads too.
REVOKE SELECT ON user_sessions, oauth_states FROM codeguard_readonly;
REVOKE SELECT (email) ON users FROM codeguard_readonly;

-- ---------------------------------------------------------------------------
-- Future tables
-- ---------------------------------------------------------------------------
-- Without this, every new migration silently creates a table the application
-- role cannot touch, and the failure appears as a permission error in
-- production some time after the deploy that caused it.
ALTER DEFAULT PRIVILEGES FOR ROLE codeguard_migrate IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO codeguard_app;
ALTER DEFAULT PRIVILEGES FOR ROLE codeguard_migrate IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO codeguard_app;
ALTER DEFAULT PRIVILEGES FOR ROLE codeguard_migrate IN SCHEMA public
    GRANT SELECT ON TABLES TO codeguard_readonly;

-- ---------------------------------------------------------------------------
-- Verification
-- ---------------------------------------------------------------------------
-- Run this afterwards and read the output. A grant that silently did not apply
-- is worse than no grant at all, because the deployment looks configured.
--
--   SELECT grantee, table_name, string_agg(privilege_type, ', ' ORDER BY privilege_type)
--     FROM information_schema.role_table_grants
--    WHERE grantee IN ('codeguard_app', 'codeguard_readonly')
--    GROUP BY grantee, table_name
--    ORDER BY grantee, table_name;
--
-- And confirm the application role genuinely cannot change the schema:
--
--   SET ROLE codeguard_app;
--   CREATE TABLE should_fail (id int);   -- expect: permission denied
--   RESET ROLE;
