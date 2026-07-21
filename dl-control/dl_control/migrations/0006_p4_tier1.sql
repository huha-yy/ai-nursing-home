-- 0006_p4_tier1.sql — P4 Tier 1 isolation (spec §4.1).
-- Forward-only; do not edit after landed.
--
-- Adds:
--   - per_agent_db_name / per_agent_db_role columns on agents
--   - audit_log_outbox table
--   - AFTER INSERT trigger on audit_log → outbox (Tier 1 agents only)

-- ── 1. Agent registry columns ──────────────────────────────────────────────

ALTER TABLE agents ADD COLUMN IF NOT EXISTS per_agent_db_name TEXT;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS per_agent_db_role TEXT;

-- Enforce uniqueness of per_agent_db_name (short8 collision guard).
-- Partial index: NULL values (Tier 0) are excluded.
CREATE UNIQUE INDEX IF NOT EXISTS agents_per_agent_db_name_unique
    ON agents (per_agent_db_name) WHERE per_agent_db_name IS NOT NULL;

-- ── 2. Audit outbox table ──────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS audit_log_outbox (
    id              BIGSERIAL PRIMARY KEY,
    audit_log_id    BIGINT NOT NULL,
    agent_id        UUID NOT NULL,
    payload         JSONB NOT NULL,
    attempts        INT NOT NULL DEFAULT 0,
    last_error      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Index for the reconciler's poll query: drain unprocessed rows per agent.
CREATE INDEX IF NOT EXISTS idx_audit_outbox_agent
    ON audit_log_outbox (agent_id, id)
    WHERE attempts < 10;

-- dl_control_app needs SELECT + DELETE for the reconciler poll+drain path,
-- and the trigger (running as owner) INSERTs directly.
GRANT SELECT, INSERT, DELETE ON audit_log_outbox TO dl_control_app;
GRANT USAGE ON SEQUENCE audit_log_outbox_id_seq TO dl_control_app;

-- ── 3. Trigger function — enqueue audit events for Tier 1 agents ───────────

CREATE OR REPLACE FUNCTION enqueue_audit_outbox()
RETURNS trigger AS $$
BEGIN
    -- Only enqueue when the target column is a valid UUID that corresponds
    -- to a Tier 1 agent with a per_agent_db_name set.  Non-UUID targets
    -- (e.g. 'system', 'dashboard', 'anonymous') will fail the cast, and the
    -- exception block silently returns.  The EXISTS subquery also catches
    -- UUID targets that are Tier 0 or not yet provisioned.
    BEGIN
        IF EXISTS (
            SELECT 1 FROM agents
            WHERE id = NEW.target::uuid
              AND tier = 'tier1'
              AND per_agent_db_name IS NOT NULL
        ) THEN
            INSERT INTO audit_log_outbox (audit_log_id, agent_id, payload)
            VALUES (
                NEW.id,
                NEW.target::uuid,
                jsonb_build_object(
                    'id',             NEW.id,
                    'actor_user_id',  NEW.actor_user_id,
                    'action',         NEW.action,
                    'target',         NEW.target,
                    'meta',           NEW.meta,
                    'occurred_at',    NEW.occurred_at
                )
            );
        END IF;
    EXCEPTION
        WHEN invalid_text_representation THEN
            -- target is not a UUID (e.g. 'system') — no-op.
            NULL;
    END;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ── 4. Attach trigger ──────────────────────────────────────────────────────

DROP TRIGGER IF EXISTS audit_log_after_insert ON audit_log;

CREATE TRIGGER audit_log_after_insert
    AFTER INSERT ON audit_log
    FOR EACH ROW
    EXECUTE FUNCTION enqueue_audit_outbox();
