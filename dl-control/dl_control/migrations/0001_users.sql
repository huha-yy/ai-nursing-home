-- 0001_users.sql — P1. Forward-only; do not edit after landed.
-- Creates the users table and the dl_control_app application role.
-- The runner substitutes :app_password with a quoted string literal.

CREATE TABLE users (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  username             text NOT NULL UNIQUE,
  password_hash        text NOT NULL,
  role                 text NOT NULL CHECK (role IN ('admin', 'user')),
  status               text NOT NULL DEFAULT 'active'
                          CHECK (status IN ('active', 'disabled')),
  must_change_password boolean NOT NULL DEFAULT false,
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER users_updated_at
  BEFORE UPDATE ON users
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Application role: NOT an owner of any table, so RLS applies to it.
DO $do$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dl_control_app') THEN
    EXECUTE 'CREATE ROLE dl_control_app LOGIN PASSWORD ' || :app_password;
  END IF;
END;
$do$;

GRANT USAGE ON SCHEMA public TO dl_control_app;
-- P1 has no user-delete path; DELETE is withheld (spec §4.2).
GRANT SELECT, INSERT, UPDATE ON users TO dl_control_app;
