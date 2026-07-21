-- 0001_cognee_iso.sql — per-library schema, run inside the isolated DB
-- as the DB owner. Forward-only; do not edit after landed.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE SCHEMA IF NOT EXISTS cognee_iso AUTHORIZATION current_user;

CREATE TABLE IF NOT EXISTS cognee_iso.documents (
    path            TEXT         PRIMARY KEY,
    content_hash    VARCHAR(64)  NOT NULL,                  -- SHA-256 hex
    source_agent_id TEXT         NOT NULL,                  -- agent UUID string; nil UUID for admin ingest
    ingested_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS cognee_iso.chunks (
    id              BIGSERIAL PRIMARY KEY,
    path            TEXT NOT NULL REFERENCES cognee_iso.documents(path)
                    ON DELETE CASCADE,
    chunk_idx       INT  NOT NULL,
    chunk_text      TEXT NOT NULL,
    embedding       vector(384) NOT NULL,
    UNIQUE (path, chunk_idx)
);

CREATE INDEX IF NOT EXISTS cognee_chunks_embedding_cos_idx
    ON cognee_iso.chunks
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- The per-library role is granted DML + sequence on the schema.
-- PUBLIC is safe here because only the single per-library role can log in.
GRANT USAGE ON SCHEMA cognee_iso TO PUBLIC;
GRANT SELECT, INSERT, UPDATE, DELETE
    ON cognee_iso.documents, cognee_iso.chunks
    TO PUBLIC;
GRANT USAGE, SELECT ON SEQUENCE cognee_iso.chunks_id_seq TO PUBLIC;
