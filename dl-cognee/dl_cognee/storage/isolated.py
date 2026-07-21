"""Isolated-storage ingest + search (per-library DB cognee_iso schema).

Each isolated library has its own database. The DSN comes from the
verify response (per_library_db_dsn field). Pools are managed per-DSN.
"""

from __future__ import annotations

import hashlib


async def ingest_isolated(
    pool,
    *,
    path: str,
    content: str,
    chunks: list[str],
    embeddings: list[list[float]],
    source_agent_id: str,
) -> dict:
    """Ingest content into an isolated library. No library_slug discriminator."""
    content_hash = hashlib.sha256(content.encode()).hexdigest()

    async with pool.connection() as conn, conn.transaction():
        await conn.execute(
            """INSERT INTO cognee_iso.documents
                   (path, content_hash, source_agent_id)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (path) DO UPDATE
                   SET content_hash = EXCLUDED.content_hash,
                       source_agent_id = EXCLUDED.source_agent_id,
                       ingested_at = now()""",
            (path, content_hash, source_agent_id),
        )

        await conn.execute(
            "DELETE FROM cognee_iso.chunks WHERE path = %s",
            (path,),
        )

        for i, (chunk, emb) in enumerate(zip(chunks, embeddings, strict=False)):
            await conn.execute(
                """INSERT INTO cognee_iso.chunks
                       (path, chunk_idx, chunk_text, embedding)
                       VALUES (%s, %s, %s, %s)""",
                (path, i, chunk, emb),
            )

        cur = await conn.execute(
            "SELECT ingested_at FROM cognee_iso.documents WHERE path = %s",
            (path,),
        )
        row = await cur.fetchone()

    return {
        "library_slug": None,  # filled by caller
        "path": path,
        "chunk_count": len(chunks),
        "content_hash": content_hash,
        "ingested_at": row[0].isoformat() if row else None,
    }


async def search_isolated(
    pool,
    *,
    embedding: list[float],
    limit: int = 5,
) -> list[dict]:
    """Search an isolated library."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            """SELECT chunk_text, path, chunk_idx,
                      embedding <=> %s::vector AS cosine_distance
               FROM cognee_iso.chunks
               ORDER BY embedding <=> %s::vector
               LIMIT %s""",
            (embedding, embedding, limit),
        )
        rows = await cur.fetchall()

    return [
        {
            "text": row[0],
            "path": row[1],
            "chunk_idx": row[2],
            "cosine_distance": float(row[3]),
        }
        for row in rows
    ]
