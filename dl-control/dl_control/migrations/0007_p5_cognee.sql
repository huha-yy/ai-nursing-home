-- 0007_p5_cognee.sql — P5 knowledge libraries + per-agent internal token.
-- Forward-only; do not edit after landed.
--
-- The runner substitutes :cognee_password with a quoted literal string
-- (same pattern as :app_password in 0001).  The init script
-- infra/postgres/init/02_cognee_role.sql creates the 'cognee' role with
-- NOLOGIN first; this migration then grants LOGIN + password so the
-- password is never baked into a committed SQL file.

-- ── 0. Ensure the cognee role exists (upgrade-safe) ───────────────────────

DO $do$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cognee') THEN
        EXECUTE 'CREATE ROLE cognee WITH NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE';
    END IF;
END;
$do$;

-- ── 1. Per-agent internal token (agent → dl-cognee bearer auth) ──────────

ALTER TABLE agents
  ADD COLUMN IF NOT EXISTS internal_token_hash BYTEA;
                                         -- SHA-256, NULL until provisioned

ALTER TABLE agents
  ADD COLUMN IF NOT EXISTS cognee_authz_version BIGINT NOT NULL DEFAULT 0;
                                         -- Bumped on ACL change for this agent.

-- ── 2. Knowledge library catalogue (owner DB) ─────────────────────────────

CREATE TABLE IF NOT EXISTS knowledge_libraries (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug                TEXT UNIQUE NOT NULL
                        CHECK (slug ~ '^[a-z0-9_-]{2,64}$'),
    display_name        TEXT NOT NULL,
    sensitivity         TEXT NOT NULL
                        CHECK (sensitivity IN ('public', 'shared', 'restricted')),
    storage_kind        TEXT NOT NULL
                        CHECK (storage_kind IN ('shared', 'isolated')),
    per_library_db_name TEXT,
    per_library_db_role TEXT,
    owner_agent_id      UUID REFERENCES agents(id) ON DELETE CASCADE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (
        (storage_kind = 'isolated'
            AND per_library_db_name IS NOT NULL
            AND per_library_db_role IS NOT NULL)
        OR
        (storage_kind = 'shared'
            AND per_library_db_name IS NULL
            AND per_library_db_role IS NULL)
    )
);

DROP TRIGGER IF EXISTS knowledge_libraries_updated_at ON knowledge_libraries;
CREATE TRIGGER knowledge_libraries_updated_at
    BEFORE UPDATE ON knowledge_libraries
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- One auto-private library per agent (slug = agent_<short>_private).
CREATE UNIQUE INDEX IF NOT EXISTS knowledge_libraries_owner_unique
    ON knowledge_libraries (owner_agent_id)
    WHERE owner_agent_id IS NOT NULL;

GRANT SELECT, INSERT, UPDATE, DELETE ON knowledge_libraries TO dl_control_app;

-- ── 3. ACL ────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS agent_library_access (
    agent_id    UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    library_id  UUID NOT NULL REFERENCES knowledge_libraries(id)
                    ON DELETE CASCADE,
    access      TEXT NOT NULL CHECK (access IN ('read', 'read_write')),
    granted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (agent_id, library_id)
);

GRANT SELECT, INSERT, DELETE ON agent_library_access TO dl_control_app;

-- ── 4. authz_version bump trigger ──────────────────────────────────────────

CREATE OR REPLACE FUNCTION bump_agent_authz_version() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        UPDATE agents SET cognee_authz_version = cognee_authz_version + 1
            WHERE id = NEW.agent_id;
    ELSIF TG_OP = 'DELETE' THEN
        UPDATE agents SET cognee_authz_version = cognee_authz_version + 1
            WHERE id = OLD.agent_id;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS agent_library_access_bump_authz ON agent_library_access;
CREATE TRIGGER agent_library_access_bump_authz
    AFTER INSERT OR DELETE ON agent_library_access
    FOR EACH ROW EXECUTE FUNCTION bump_agent_authz_version();

-- ── 5. Bootstrap _public ──────────────────────────────────────────────────

INSERT INTO knowledge_libraries (slug, display_name, sensitivity, storage_kind)
    VALUES ('_public', 'Public Knowledge', 'public', 'shared')
ON CONFLICT (slug) DO NOTHING;

-- ── 6. Owner DB cognee schema + role ──────────────────────────────────────

CREATE SCHEMA IF NOT EXISTS cognee;

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS cognee.documents (
    library_slug    TEXT         NOT NULL,
    path            TEXT         NOT NULL,
    content_hash    VARCHAR(64)  NOT NULL,                  -- SHA-256 hex
    source_agent_id UUID         NULL,                      -- audit metadata; NULL for admin ingest
    ingested_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (library_slug, path)
);

CREATE TABLE IF NOT EXISTS cognee.chunks (
    id              BIGSERIAL PRIMARY KEY,
    library_slug    TEXT NOT NULL,
    path            TEXT NOT NULL,
    chunk_idx       INT  NOT NULL,
    chunk_text      TEXT NOT NULL,
    embedding       vector(384) NOT NULL,                   -- bge-small-en-v1.5
    FOREIGN KEY (library_slug, path)
        REFERENCES cognee.documents(library_slug, path) ON DELETE CASCADE,
    UNIQUE (library_slug, path, chunk_idx)
);

CREATE INDEX IF NOT EXISTS cognee_chunks_library_idx ON cognee.chunks (library_slug);

-- ivfflat over cosine ops. lists=100 is the v1 default; tune later.
CREATE INDEX IF NOT EXISTS cognee_chunks_embedding_cos_idx
    ON cognee.chunks
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- ── 7. Grant cognee role login + password, then schema privileges ──────────

DO $$
BEGIN
    EXECUTE 'ALTER ROLE cognee WITH LOGIN PASSWORD ' || :cognee_password;
END;
$$;

GRANT USAGE ON SCHEMA cognee TO cognee;
GRANT SELECT, INSERT, UPDATE, DELETE ON cognee.documents, cognee.chunks TO cognee;
GRANT USAGE, SELECT ON SEQUENCE cognee.chunks_id_seq TO cognee;
