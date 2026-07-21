-- 0009_p7_ota.sql — P7 OTA watcher state.

-- ── 1. Per-agent openclaw image digest column ───────────────────────────────
-- Captures the digest the agent's container is currently RUNNING on.
-- Updated only when a roll-openclaw job reaches "committed" — never partially.

ALTER TABLE agents
    ADD COLUMN IF NOT EXISTS current_openclaw_digest TEXT;
-- NULL means "use the compose-time default" (initial bootstrap before any OTA).

-- ── 2. Watcher's own bookkeeping tables ─────────────────────────────────────

CREATE TABLE IF NOT EXISTS ota_migrations (
    name        TEXT PRIMARY KEY,
    sha256      TEXT NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ota_schema_version (
    singleton   BOOLEAN PRIMARY KEY DEFAULT TRUE
                CONSTRAINT ota_schema_version_singleton CHECK (singleton),
    version     INTEGER NOT NULL
);

-- ── 3. Dedicated watcher role with minimum grants ───────────────────────────
-- The watcher's normal poll loop uses this role, NOT the owner.
-- Owner DSN is loaded only during the MIGRATING phase by a one-shot runner.

DO $do$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dato_ota_watcher_app') THEN
        EXECUTE 'CREATE ROLE dato_ota_watcher_app WITH NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE';
    END IF;
END;
$do$;

GRANT USAGE ON SCHEMA public TO dato_ota_watcher_app;
GRANT SELECT, INSERT ON ota_migrations TO dato_ota_watcher_app;
GRANT SELECT, INSERT, UPDATE ON ota_schema_version TO dato_ota_watcher_app;
GRANT SELECT ON agents TO dato_ota_watcher_app;
-- Insert into audit_log via the existing P1 policy
-- (sets app.current_role='system' before insert).
GRANT INSERT ON audit_log TO dato_ota_watcher_app;
GRANT USAGE ON SEQUENCE audit_log_id_seq TO dato_ota_watcher_app;

-- ── 4. RLS — the watcher always operates as role=system ─────────────────────
-- Both audit_log inserts AND agents reads require
-- SET LOCAL app.current_role = 'system' inside every transaction
-- the watcher opens. The watcher's db helper (dl_ota_watcher/db.py)
-- wraps every connection acquisition with this SET, mirroring how
-- dl-control/dl_control/db.py uses set_user_session().

-- Password granted via DO block with :ota_watcher_app_password literal,
-- matching the cognee 0007 pattern. Init script creates the role NOLOGIN;
-- this migration upgrades to LOGIN with the runtime password.
DO $$
BEGIN
    EXECUTE 'ALTER ROLE dato_ota_watcher_app WITH LOGIN PASSWORD ' || :ota_watcher_app_password;
END;
$$;
