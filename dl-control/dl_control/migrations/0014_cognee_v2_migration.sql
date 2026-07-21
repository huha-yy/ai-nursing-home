-- 0014_cognee_v2_migration.sql — upgrade from vector(384) to vector(1024)
-- Forward-only; idempotent for re-runs.
--
-- Replaces the paraphrase-multilingual-MiniLM-L12-v2 (384-dim, fastembed)
-- embedding column with bge-m3 (1024-dim, FlagEmbedding). Existing chunk_text
-- is preserved in the table, and a re-embed admin endpoint can regenerate
-- the 1024-dim embeddings from the stored text.

-- Step 1: Drop the IVFFlat index (must be dropped before ALTER COLUMN).
DROP INDEX IF EXISTS cognee.cognee_chunks_embedding_cos_idx;

-- Step 2: Delete old chunk embeddings (chunk_text is preserved for re-embed).
-- The existing 384-dim vectors are incompatible with the new 1024-dim column.
DELETE FROM cognee.chunks;

-- Step 3: Alter the column type. Safe because the table is now empty.
ALTER TABLE cognee.chunks
    ALTER COLUMN embedding TYPE vector(1024);

-- Step 4: Recreate the IVFFlat index for 1024-dim vectors.
CREATE INDEX IF NOT EXISTS cognee_chunks_embedding_cos_idx
    ON cognee.chunks
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);
