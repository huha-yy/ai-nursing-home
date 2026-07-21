-- Create the cognee role used by the dl-cognee service to connect to the
-- owner DB. Migration 0007_p5_cognee.sql sets the login password and grants
-- schema privileges.
DO $$ BEGIN
    CREATE ROLE cognee WITH NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
EXCEPTION WHEN duplicate_object THEN NULL;
END; $$;
