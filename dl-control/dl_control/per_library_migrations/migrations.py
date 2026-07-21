"""Per-library migration runner — applies *.sql against a per-library database.

Migrations are forward-only, idempotent (IF NOT EXISTS), tracked in
_per_library_schema_migrations. Called at library provision time for both
admin-created isolated libraries and Tier 1 auto-private libraries.
"""

from __future__ import annotations

import logging
from pathlib import Path

import psycopg

logger = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).resolve().parent


async def apply_per_library_migrations(dsn: str) -> None:
    conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True)
    try:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS _per_library_schema_migrations ("
            "  version INT PRIMARY KEY,"
            "  applied_at TIMESTAMPTZ NOT NULL DEFAULT now()"
            ")"
        )
        sql_files = sorted(p for p in _MIGRATIONS_DIR.glob("*.sql") if p.name[0].isdigit())
        for path in sql_files:
            version = int(path.name.split("_", 1)[0])
            cur = await conn.execute(
                "SELECT 1 FROM _per_library_schema_migrations WHERE version = %s",
                (version,),
            )
            if await cur.fetchone():
                continue
            sql = path.read_text()
            await conn.execute(sql)
            await conn.execute(
                "INSERT INTO _per_library_schema_migrations (version) VALUES (%s)",
                (version,),
            )
            logger.info("per-library migration %d applied", version)
    finally:
        await conn.close()
