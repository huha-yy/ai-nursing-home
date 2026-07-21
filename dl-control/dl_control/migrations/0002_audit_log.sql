-- 0002_audit_log.sql — P1. Forward-only.
-- Append-only audit log + the project's RLS pattern.

CREATE TABLE audit_log (
  id            bigserial PRIMARY KEY,
  occurred_at   timestamptz NOT NULL DEFAULT now(),
  actor_user_id uuid REFERENCES users(id),
  action        text NOT NULL,
  target        text,
  meta          jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX audit_log_occurred_at_idx ON audit_log (occurred_at DESC);
CREATE INDEX audit_log_actor_idx       ON audit_log (actor_user_id);

ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log FORCE ROW LEVEL SECURITY;

-- INSERT: actor matches the GUC, OR actor IS NULL and role is admin/system.
CREATE POLICY audit_log_insert_self ON audit_log
  FOR INSERT
  WITH CHECK (
    (actor_user_id IS NOT NULL
     AND actor_user_id::text = current_setting('app.current_user_id', true))
    OR
    (actor_user_id IS NULL
     AND current_setting('app.current_role', true) IN ('admin', 'system'))
  );

-- SELECT: admins/system see all; users see only their own rows.
CREATE POLICY audit_log_select_own_or_admin ON audit_log
  FOR SELECT
  USING (
    current_setting('app.current_role', true) IN ('admin', 'system')
    OR (actor_user_id IS NOT NULL
        AND actor_user_id::text = current_setting('app.current_user_id', true))
  );

-- No UPDATE/DELETE policy -> append-only at the DB layer.

GRANT SELECT, INSERT ON audit_log TO dl_control_app;
GRANT USAGE ON SEQUENCE audit_log_id_seq TO dl_control_app;
