-- 0005_feishu_pairing.sql
-- P3 — Feishu credential wizard + pairing layer.

-- Per-agent monotonic counters for projection CAS.
ALTER TABLE agents
    ADD COLUMN pairing_version         BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN last_projection_version BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN last_projection_hash    BYTEA,
    ADD COLUMN needs_restart           BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN feishu_configured       BOOLEAN NOT NULL DEFAULT false;

-- Dirty-detection index.
CREATE INDEX agents_pairing_dirty
    ON agents (id)
    WHERE pairing_version > last_projection_version;

-- Uniqueness of Feishu app_id across agents (D-P3-15).
-- Agents are hard-deleted; deleted rows simply do not exist, so no
-- `deleted_at IS NULL` filter is needed.
CREATE UNIQUE INDEX agents_unique_feishu_app
    ON agents ((channel_config -> 'feishu' ->> 'app_id'))
    WHERE (channel_config -> 'feishu' ->> 'app_id') IS NOT NULL;

CREATE TABLE pairings (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id              UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    channel               TEXT NOT NULL CHECK (channel = 'feishu'),       -- P3 scope-lock (D-P3-1)
    account_id            TEXT NOT NULL CHECK (account_id ~ '^[a-z][a-z0-9_]{0,63}$'),
    sender_id_raw         TEXT NOT NULL,                                  -- exact verbatim
    sender_id_normalized  TEXT NOT NULL,                                  -- trim per OpenClaw adapter
    sender_name           TEXT,
    status                TEXT NOT NULL DEFAULT 'approved'
                          CHECK (status IN ('approved', 'revoked')),
    approved_by           UUID NOT NULL REFERENCES users(id),
    approved_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_by            UUID REFERENCES users(id),
    revoked_at            TIMESTAMPTZ,

    -- State-model constraints (D-P3-13).
    CONSTRAINT pairings_approved_clean
        CHECK (status <> 'approved' OR (revoked_by IS NULL AND revoked_at IS NULL)),
    CONSTRAINT pairings_revoked_complete
        CHECK (status <> 'revoked' OR (revoked_by IS NOT NULL AND revoked_at IS NOT NULL)),

    -- Uniqueness on normalized sender (D-P3-14).
    UNIQUE (agent_id, account_id, sender_id_normalized)
);

ALTER TABLE pairings ENABLE ROW LEVEL SECURITY;
ALTER TABLE pairings FORCE ROW LEVEL SECURITY;

CREATE POLICY pairings_admin_or_system_select ON pairings
    FOR SELECT  USING (current_setting('app.current_role', true) IN ('admin', 'system'));
CREATE POLICY pairings_admin_or_system_insert ON pairings
    FOR INSERT  WITH CHECK (current_setting('app.current_role', true) IN ('admin', 'system'));
CREATE POLICY pairings_admin_or_system_update ON pairings
    FOR UPDATE  USING (current_setting('app.current_role', true) IN ('admin', 'system'))
                WITH CHECK (current_setting('app.current_role', true) IN ('admin', 'system'));
CREATE POLICY pairings_admin_or_system_delete ON pairings
    FOR DELETE  USING (current_setting('app.current_role', true) IN ('admin', 'system'));

GRANT SELECT, INSERT, UPDATE, DELETE ON pairings TO dl_control_app;
