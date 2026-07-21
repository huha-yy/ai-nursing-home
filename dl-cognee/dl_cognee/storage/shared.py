"""Shared-storage ingest + search (owner DB cognee schema).

All operations use the library_slug discriminator column.
"""

from __future__ import annotations

import hashlib


async def ingest_shared(
    pool,
    *,
    library_slug: str,
    path: str,
    content: str,
    chunks: list[str],
    embeddings: list[list[float]],
    source_agent_id: str | None = None,
) -> dict:
    """Ingest content into a shared library.

    1. Compute content_hash.
    2. Upsert documents row.
    3. Delete existing chunks for (library_slug, path).
    4. Insert new chunks with embeddings.
    Returns {library_slug, path, chunk_count, content_hash, ingested_at}.
    """
    content_hash = hashlib.sha256(content.encode()).hexdigest()

    async with pool.connection() as conn, conn.transaction():
        # Upsert document.
        await conn.execute(
            """INSERT INTO cognee.documents
                   (library_slug, path, content_hash, source_agent_id)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (library_slug, path) DO UPDATE
                   SET content_hash = EXCLUDED.content_hash,
                       source_agent_id = EXCLUDED.source_agent_id,
                       ingested_at = now()""",
            (library_slug, path, content_hash, source_agent_id),
        )

        # Delete existing chunks.
        await conn.execute(
            "DELETE FROM cognee.chunks WHERE library_slug = %s AND path = %s",
            (library_slug, path),
        )

        # Insert new chunks.
        for i, (chunk, emb) in enumerate(zip(chunks, embeddings, strict=False)):
            await conn.execute(
                """INSERT INTO cognee.chunks
                       (library_slug, path, chunk_idx, chunk_text, embedding)
                       VALUES (%s, %s, %s, %s, %s)""",
                (library_slug, path, i, chunk, emb),
            )

        # Get ingested_at from the document row.
        cur = await conn.execute(
            "SELECT ingested_at FROM cognee.documents WHERE library_slug = %s AND path = %s",
            (library_slug, path),
        )
        row = await cur.fetchone()

    return {
        "library_slug": library_slug,
        "path": path,
        "chunk_count": len(chunks),
        "content_hash": content_hash,
        "ingested_at": row[0].isoformat() if row else None,
    }


async def search_shared(
    pool,
    *,
    library_slug: str,
    embedding: list[float],
    limit: int = 5,
) -> list[dict]:
    """Search a shared library. Returns list of {text, path, chunk_idx, cosine_distance}."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            """SELECT chunk_text, path, chunk_idx,
                      embedding <=> %s::vector AS cosine_distance
               FROM cognee.chunks
               WHERE library_slug = %s
               ORDER BY embedding <=> %s::vector
               LIMIT %s""",
            (embedding, library_slug, embedding, limit),
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
