"""Re-embed all existing cognee chunks with bge-m3 (1024-dim).

Run as a one-shot after migration 0014_cognee_v2_migration.sql has been applied.
Reads stored chunk_text from the old table and re-embeds each document in batch.

Usage:
    python -m dl_cognee.scripts.reembed
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("reembed")


async def main() -> None:
    dsn = os.environ["DL_COGNEE_OWNER_DB_DSN"]
    model_name = os.environ.get("DL_COGNEE_EMBEDDING_MODEL", "BAAI/bge-m3")

    from dl_cognee.embedder import Embedder

    embedder = Embedder(model_name=model_name)
    await embedder.warm_up()

    import psycopg

    conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True)
    try:
        # Step 1: Dump all chunk text from shared library chunks.
        cur = await conn.execute(
            "SELECT library_slug, path, chunk_idx, chunk_text "
            "FROM cognee.chunks ORDER BY library_slug, path, chunk_idx"
        )
        rows = await cur.fetchall()
        logger.info("dumped %d old chunks", len(rows))

        if not rows:
            logger.info("no chunks to re-embed, skipping")
            return

        # Step 2: Group by (library_slug, path) for document-level batching.
        groups: dict[tuple[str, str], list[tuple[int, str]]] = {}
        for r in rows:
            key = (r[0], r[1])
            groups.setdefault(key, []).append((r[2], r[3]))

        logger.info("re-embedding %d documents across %d chunks", len(groups), len(rows))

        # Step 3: Re-embed each document.
        reembedded = 0
        for (lib_slug, path), chunks in groups.items():
            chunks.sort(key=lambda x: x[0])
            texts = [t for _, t in chunks]
            embeddings = embedder.embed_batch(texts)

            for (chunk_idx, _text), emb in zip(chunks, embeddings):
                await conn.execute(
                    "UPDATE cognee.chunks SET embedding = %s "
                    "WHERE library_slug = %s AND path = %s AND chunk_idx = %s",
                    (emb, lib_slug, path, chunk_idx),
                )

            reembedded += 1
            if reembedded % 10 == 0:
                logger.info("progress: %d / %d documents re-embedded", reembedded, len(groups))

        logger.info("re-embed complete: %d chunks across %d documents", len(rows), reembedded)

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
