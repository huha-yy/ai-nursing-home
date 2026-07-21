-- 0004_agent_status_lifecycle.sql — P2. Forward-only.
-- Extends the agents.status state machine and adds the container handle.
-- The status CHECK was created in 0003 limited to ('registered'); P2 widens
-- it. container_id holds the Docker container id once the agent is booted.

ALTER TABLE agents DROP CONSTRAINT agents_status_check;

ALTER TABLE agents ADD CONSTRAINT agents_status_check
  CHECK (status IN ('registered', 'provisioning', 'active', 'error', 'stopped'));

ALTER TABLE agents ADD COLUMN container_id text;
