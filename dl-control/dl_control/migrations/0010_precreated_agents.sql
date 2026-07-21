-- 0010_precreated_agents.sql
-- P8 — precreated agents seed registry.
-- Forward-only; do not edit after landed.

ALTER TABLE agents
  ADD COLUMN precreated_id          text NULL,
  ADD COLUMN precreated_yaml_sha256 text NULL;

COMMENT ON COLUMN agents.precreated_id IS
  'Stable identity of the precreated_agents/<id>/ seed that produced '
  'this row. NULL for ad-hoc agents. Set atomically at INSERT; never '
  'mutated after creation.';

COMMENT ON COLUMN agents.precreated_yaml_sha256 IS
  'SHA-256 (hex) of canonical-JSON serialization of the mutable subset '
  'of agent.yaml at last provision/apply. NULL when precreated_id NULL.';

CREATE UNIQUE INDEX agents_precreated_id_unique
  ON agents (precreated_id)
  WHERE precreated_id IS NOT NULL;

-- Sanity check: precreated_yaml_sha256 set iff precreated_id set.
ALTER TABLE agents
  ADD CONSTRAINT agents_precreated_pair CHECK (
    (precreated_id IS NULL AND precreated_yaml_sha256 IS NULL)
    OR
    (precreated_id IS NOT NULL AND precreated_yaml_sha256 IS NOT NULL)
  );

CREATE TABLE precreated_suppressions (
  precreated_id  text PRIMARY KEY,
  suppressed_at  timestamptz NOT NULL DEFAULT now(),
  suppressed_by  uuid NULL REFERENCES users(id) ON DELETE SET NULL
);

GRANT SELECT, INSERT, DELETE ON precreated_suppressions TO dl_control_app;
