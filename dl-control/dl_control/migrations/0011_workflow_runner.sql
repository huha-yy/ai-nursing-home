-- 0011_workflow_runner.sql
-- P13 — workflow runner. Spec: 2026-06-09-dato-workflow-runner-design.md (§5, §11).
-- Forward-only; do not edit after landed.
-- Idempotent DDL (0009 idiom): boot migrations track _schema_migrations while
-- OTA bundle migrations track ota_migrations — the trackers are not reconciled
-- (spec §9), so this file must survive being applied through both channels.

-- ── workflow (flow head) + workflow_version (version-addressable defs, §5.1) ──
CREATE TABLE IF NOT EXISTS workflow (
    id              text PRIMARY KEY,
    enabled         boolean NOT NULL DEFAULT false,
    latest_version  text,
    display_name    text NOT NULL,
    description     text,
    default_trigger text NOT NULL DEFAULT 'manual'
                    CHECK (default_trigger IN ('cron','event','manual','agent')),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS workflow_version (
    workflow_id   text NOT NULL REFERENCES workflow(id) ON DELETE CASCADE,
    version       text NOT NULL,
    code_ref      text NOT NULL,
    registered_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workflow_id, version)
);

-- ── workflow_run (one per execution, §5.2) ──
CREATE TABLE IF NOT EXISTS workflow_run (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id         text NOT NULL REFERENCES workflow(id),
    workflow_version    text NOT NULL,
    status              text NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','running','waiting_approval',
                          'waiting_agent','waiting_timer','waiting_retry',
                          'waiting_manual','succeeded','failed','cancelled')),
    trigger             text NOT NULL
                        CHECK (trigger IN ('cron','event','agent','manual')),
    started_by_agent_id uuid REFERENCES agents(id) ON DELETE SET NULL,
    input               jsonb NOT NULL DEFAULT '{}'::jsonb,
    current_step        text,
    lease_owner         text,
    lease_expires_at    timestamptz,
    wake_at             timestamptz,
    correlation_key     text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    finished_at         timestamptz,
    FOREIGN KEY (workflow_id, workflow_version)
        REFERENCES workflow_version(workflow_id, version)
);

-- At most one live run per business entity (§5.2, Codex R5).
CREATE UNIQUE INDEX IF NOT EXISTS uq_workflow_active_run
    ON workflow_run (workflow_id, correlation_key)
    WHERE status IN ('pending','running','waiting_approval','waiting_agent',
                     'waiting_timer','waiting_retry','waiting_manual')
      AND correlation_key IS NOT NULL;

-- Claim/wake support (§6.2).
CREATE INDEX IF NOT EXISTS workflow_run_claimable
    ON workflow_run (COALESCE(wake_at, created_at))
    WHERE status IN ('pending','running','waiting_timer','waiting_retry','waiting_agent');

-- ── workflow_step (per-step execution, §5.3) ──
CREATE TABLE IF NOT EXISTS workflow_step (
    run_id          uuid NOT NULL REFERENCES workflow_run(id) ON DELETE CASCADE,
    step_key        text NOT NULL,
    status          text NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','running','succeeded','failed','skipped')),
    attempt         int NOT NULL DEFAULT 0,
    idempotency_key text,
    next_attempt_at timestamptz,
    started_at      timestamptz,
    finished_at     timestamptz,
    output          jsonb,
    error           text,
    PRIMARY KEY (run_id, step_key)
);

-- ── side_effect_ledger (durability keystone, §5.4) ──
CREATE TABLE IF NOT EXISTS side_effect_ledger (
    idempotency_key text PRIMARY KEY,
    run_id          uuid NOT NULL REFERENCES workflow_run(id) ON DELETE CASCADE,
    step_key        text NOT NULL,
    attempt         int NOT NULL DEFAULT 0,
    target          text NOT NULL,
    request_hash    text NOT NULL,
    status          text NOT NULL DEFAULT 'started'
                    CHECK (status IN ('started','committed','failed')),
    response        jsonb,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- ── workflow_approval (human gates, §5.5) ──
CREATE TABLE IF NOT EXISTS workflow_approval (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id     uuid NOT NULL REFERENCES workflow_run(id) ON DELETE CASCADE,
    step_key   text NOT NULL,
    prompt     text NOT NULL,
    state      text NOT NULL DEFAULT 'pending'
               CHECK (state IN ('pending','approved','rejected')),
    decided_by uuid REFERENCES users(id),
    decided_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, step_key)
);

-- ── agent_call (durable workflow→agent correlation, §5.6) ──
CREATE TABLE IF NOT EXISTS agent_call (
    correlation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id         uuid NOT NULL REFERENCES workflow_run(id) ON DELETE CASCADE,
    step_key       text NOT NULL,
    attempt        int NOT NULL DEFAULT 0,
    agent_id       uuid NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    status         text NOT NULL DEFAULT 'posted'
                   CHECK (status IN ('posted','dispatched','responded','timed_out','superseded')),
    request_hash   text NOT NULL,
    response       jsonb,
    created_at     timestamptz NOT NULL DEFAULT now(),
    responded_at   timestamptz,
    -- One correlation per (run, step, attempt) — §7.1 "current attempt" is unambiguous.
    CONSTRAINT agent_call_attempt_unique UNIQUE (run_id, step_key, attempt)
);

-- ── workflow_agent_grant (agent→workflow authz, §5.7) ──
CREATE TABLE IF NOT EXISTS workflow_agent_grant (
    agent_id    uuid NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    workflow_id text NOT NULL REFERENCES workflow(id) ON DELETE CASCADE,
    granted_at  timestamptz NOT NULL DEFAULT now(),
    granted_by  uuid REFERENCES users(id),
    PRIMARY KEY (agent_id, workflow_id)
);

-- ── workflow_schedule (cron triggers, §5.8) ──
CREATE TABLE IF NOT EXISTS workflow_schedule (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id    text NOT NULL REFERENCES workflow(id) ON DELETE CASCADE,
    cron           text NOT NULL,
    input_template jsonb NOT NULL DEFAULT '{}'::jsonb,
    enabled        boolean NOT NULL DEFAULT true,
    last_fired_at  timestamptz,
    next_fire_at   timestamptz
);

-- ── RLS: every workflow table is admin/system only (§11), ENABLE + FORCE,
--    mirroring the pairings idiom. Policy naming: <table>_aon_<verb>. ──
DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'workflow','workflow_version','workflow_run','workflow_step',
        'side_effect_ledger','workflow_approval','agent_call',
        'workflow_agent_grant','workflow_schedule'
    ] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        -- DROP + CREATE keeps the block idempotent (CREATE POLICY has no
        -- IF NOT EXISTS clause).
        EXECUTE format('DROP POLICY IF EXISTS %I ON %I', t || '_aon_select', t);
        EXECUTE format(
            'CREATE POLICY %I ON %I FOR SELECT USING '
            '(current_setting(''app.current_role'', true) IN (''admin'',''system''))',
            t || '_aon_select', t);
        EXECUTE format('DROP POLICY IF EXISTS %I ON %I', t || '_aon_insert', t);
        EXECUTE format(
            'CREATE POLICY %I ON %I FOR INSERT WITH CHECK '
            '(current_setting(''app.current_role'', true) IN (''admin'',''system''))',
            t || '_aon_insert', t);
        EXECUTE format('DROP POLICY IF EXISTS %I ON %I', t || '_aon_update', t);
        EXECUTE format(
            'CREATE POLICY %I ON %I FOR UPDATE USING '
            '(current_setting(''app.current_role'', true) IN (''admin'',''system'')) '
            'WITH CHECK (current_setting(''app.current_role'', true) IN (''admin'',''system''))',
            t || '_aon_update', t);
        EXECUTE format('DROP POLICY IF EXISTS %I ON %I', t || '_aon_delete', t);
        EXECUTE format(
            'CREATE POLICY %I ON %I FOR DELETE USING '
            '(current_setting(''app.current_role'', true) IN (''admin'',''system''))',
            t || '_aon_delete', t);
    END LOOP;
END;
$$;

-- ── Grants: least privilege per table lifecycle (0009 idiom — independent
--    review). No DELETE on the durability-evidence tables (side_effect_ledger,
--    agent_call) or run history; no UPDATE on immutable workflow_version.
--    The four RLS policies per table stay uniform; a policy without a matching
--    grant is simply inert. ──
GRANT SELECT, INSERT, UPDATE ON workflow, workflow_run, workflow_step,
    side_effect_ledger, workflow_approval, agent_call TO dl_control_app;
GRANT SELECT, INSERT ON workflow_version TO dl_control_app;
GRANT SELECT, INSERT, DELETE ON workflow_agent_grant TO dl_control_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON workflow_schedule TO dl_control_app;
