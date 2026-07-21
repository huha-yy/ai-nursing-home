"""Per-agent migration runner — applies *.sql against a per-agent database.

Migrations are forward-only, idempotent (IF NOT EXISTS), tracked in
_per_agent_schema_migrations. The runner is called at Tier 1 provision time
(spec §5.1 step 3c) and re-run (idempotently) on re-provision.
"""

from __future__ import annotations

import logging
from pathlib import Path

import psycopg

logger = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).resolve().parent


async def apply_per_agent_migrations(dsn: str) -> None:
    """Apply every per-agent migration in version order, skipping already-applied
    rows. The DSN must be a full connection string with the owner role (or
    equivalent) that has CREATE permission on the per-agent database."""
    sql_files = sorted(p for p in _MIGRATIONS_DIR.glob("*.sql") if p.name[0].isdigit())
    if not sql_files:
        return

    conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True)
    try:
        # Ensure the tracking table exists before we query it.
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS _per_agent_schema_migrations ("
            "  version INT PRIMARY KEY,"
            "  applied_at TIMESTAMPTZ NOT NULL DEFAULT now()"
            ")"
        )
        for path in sql_files:
            version = int(path.name.split("_", 1)[0])
            cur = await conn.execute(
                "SELECT 1 FROM _per_agent_schema_migrations WHERE version = %s",
                (version,),
            )
            if await cur.fetchone():
                continue
            sql = path.read_text()
            await conn.execute(sql)
            await conn.execute(
                "INSERT INTO _per_agent_schema_migrations (version) VALUES (%s)",
                (version,),
            )
            logger.info("per-agent migration %d applied", version)
    finally:
        await conn.close()
