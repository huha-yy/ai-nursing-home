-- Create the dato_ota_watcher_app role used by dl-ota-watcher to connect to
-- the owner DB. Migration 0009_p7_ota.sql sets the login password and grants
-- schema privileges.
DO $$ BEGIN
    CREATE ROLE dato_ota_watcher_app WITH NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
EXCEPTION WHEN duplicate_object THEN NULL;
END; $$;
