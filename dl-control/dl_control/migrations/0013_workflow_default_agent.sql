-- 0013_workflow_default_agent.sql
-- P13c — per-workflow default agent id for flows that dispatch to an agent.
-- Forward-only; do not edit after landed.
-- Idempotent DDL (0011 idiom).

ALTER TABLE workflow ADD COLUMN IF NOT EXISTS default_agent_id uuid
    REFERENCES agents(id) ON DELETE SET NULL;
