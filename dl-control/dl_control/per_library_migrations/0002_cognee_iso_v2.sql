-- 0002_cognee_iso_v2.sql — upgrade isolated lib from vector(384) to vector(1024)
-- Forward-only; idempotent for re-runs.

-- Step 1: Drop old IVFFlat index.
DROP INDEX IF EXISTS cognee_iso.cognee_chunks_embedding_cos_idx;

-- Step 2: Delete old chunk embeddings (chunk_text preserved).
DELETE FROM cognee_iso.chunks;

-- Step 3: Alter column type to 1024-dim.
ALTER TABLE cognee_iso.chunks
    ALTER COLUMN embedding TYPE vector(1024);

-- Step 4: Recreate index.
CREATE INDEX IF NOT EXISTS cognee_chunks_embedding_cos_idx
    ON cognee_iso.chunks
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- Re-grant permissions (safe to re-run).
GRANT USAGE ON SCHEMA cognee_iso TO PUBLIC;
GRANT SELECT, INSERT, UPDATE, DELETE
    ON cognee_iso.documents, cognee_iso.chunks
    TO PUBLIC;
GRANT USAGE, SELECT ON SEQUENCE cognee_iso.chunks_id_seq TO PUBLIC;
