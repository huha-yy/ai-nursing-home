-- 0003_agent_registry.sql — P1. Forward-only.
-- The agent registry + per-agent role assignment, both RLS-protected.
-- status CHECK is intentionally limited to 'registered'; P2 extends it.

CREATE TABLE agents (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  display_name    text NOT NULL,
  tier            text NOT NULL CHECK (tier IN ('tier0', 'tier1')),
  skill_list      jsonb NOT NULL DEFAULT '[]'::jsonb,
  channel_config  jsonb NOT NULL DEFAULT '{}'::jsonb,
  model_selection jsonb NOT NULL DEFAULT '{}'::jsonb,
  status          text NOT NULL DEFAULT 'registered'
                    CHECK (status IN ('registered')),
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TRIGGER agents_updated_at
  BEFORE UPDATE ON agents
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE roles_on_agent (
  user_id    uuid NOT NULL REFERENCES users(id)  ON DELETE CASCADE,
  agent_id   uuid NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  role       text NOT NULL CHECK (role IN ('viewer', 'member', 'power_user', 'owner')),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, agent_id)
);

ALTER TABLE agents ENABLE ROW LEVEL SECURITY;
ALTER TABLE agents FORCE ROW LEVEL SECURITY;
ALTER TABLE roles_on_agent ENABLE ROW LEVEL SECURITY;
ALTER TABLE roles_on_agent FORCE ROW LEVEL SECURITY;

-- agents: admins/system see all; a user sees only assigned agents.
CREATE POLICY agents_select_admin_or_assigned ON agents
  FOR SELECT
  USING (
    current_setting('app.current_role', true) IN ('admin', 'system')
    OR EXISTS (
      SELECT 1 FROM roles_on_agent roa
      WHERE roa.agent_id = agents.id
        AND roa.user_id::text = current_setting('app.current_user_id', true)
    )
  );

CREATE POLICY agents_insert_admin ON agents
  FOR INSERT
  WITH CHECK (current_setting('app.current_role', true) IN ('admin', 'system'));

CREATE POLICY agents_update_admin ON agents
  FOR UPDATE
  USING (current_setting('app.current_role', true) IN ('admin', 'system'))
  WITH CHECK (current_setting('app.current_role', true) IN ('admin', 'system'));

CREATE POLICY agents_delete_admin ON agents
  FOR DELETE
  USING (current_setting('app.current_role', true) IN ('admin', 'system'));

-- roles_on_agent: select own-or-admin; write admin-only.
CREATE POLICY roles_on_agent_select_own_or_admin ON roles_on_agent
  FOR SELECT
  USING (
    current_setting('app.current_role', true) IN ('admin', 'system')
    OR user_id::text = current_setting('app.current_user_id', true)
  );

CREATE POLICY roles_on_agent_insert_admin ON roles_on_agent
  FOR INSERT
  WITH CHECK (current_setting('app.current_role', true) IN ('admin', 'system'));

CREATE POLICY roles_on_agent_update_admin ON roles_on_agent
  FOR UPDATE
  USING (current_setting('app.current_role', true) IN ('admin', 'system'))
  WITH CHECK (current_setting('app.current_role', true) IN ('admin', 'system'));

CREATE POLICY roles_on_agent_delete_admin ON roles_on_agent
  FOR DELETE
  USING (current_setting('app.current_role', true) IN ('admin', 'system'));

GRANT SELECT, INSERT, UPDATE, DELETE ON agents TO dl_control_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON roles_on_agent TO dl_control_app;
