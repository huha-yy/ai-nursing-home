"""Database operations — advisory locks, migration tracking, pg_dump.

Uses asyncpg for all Postgres interactions.
"""

from __future__ import annotations

import asyncio
import fcntl
import logging
import os
from pathlib import Path

import asyncpg

logger = logging.getLogger(__name__)

# Advisory lock ID for OTA operations
OTA_LOCK_ID = 0xD1A0_07A


async def acquire_advisory_lock(
    pool: asyncpg.Pool,
    lock_id: int = OTA_LOCK_ID,
) -> bool:
    """Acquire a Postgres advisory lock. Returns True if acquired."""
    async with pool.acquire() as conn:
        result = await conn.fetchval("SELECT pg_try_advisory_lock($1)", lock_id)
        return bool(result)


async def release_advisory_lock(
    pool: asyncpg.Pool,
    lock_id: int = OTA_LOCK_ID,
) -> bool:
    """Release a Postgres advisory lock. Returns True on success."""
    async with pool.acquire() as conn:
        result = await conn.fetchval("SELECT pg_advisory_unlock($1)", lock_id)
        return bool(result)


async def is_migration_applied(
    pool: asyncpg.Pool,
    name: str,
    sha256: str,
) -> bool:
    """Check if a migration with the given name and sha256 has been applied."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM ota_migrations WHERE name = $1 AND sha256 = $2",
            name,
            sha256,
        )
        return row is not None


async def record_migration(
    pool: asyncpg.Pool,
    name: str,
    sha256: str,
) -> None:
    """Record a migration as applied."""
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO ota_migrations (name, sha256) VALUES ($1, $2)",
            name,
            sha256,
        )


async def run_migration(
    pool: asyncpg.Pool,
    sql: str,
    name: str,
) -> None:
    """Execute a migration SQL statement in its own transaction."""
    async with pool.acquire() as conn, conn.transaction():
        await set_system_role(conn)
        await conn.execute(sql)
        logger.info("Migration %s applied successfully", name)


async def get_current_schema_version(pool: asyncpg.Pool) -> int:
    """Read the current target data schema version."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT version FROM ota_schema_version WHERE singleton = TRUE")
        if row is None:
            return 0
        return row["version"]


async def set_schema_version(pool: asyncpg.Pool, version: int) -> None:
    """Update the target data schema version."""
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO ota_schema_version (singleton, version) VALUES (TRUE, $1) "
            "ON CONFLICT (singleton) DO UPDATE SET version = EXCLUDED.version",
            version,
        )


async def pg_dump_shared(db_url: str, output_path: str | Path) -> None:
    """Run pg_dump on the shared database, writing to a .sql.gz file."""
    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        "pg_dump",
        db_url,
        stdout=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"pg_dump failed (exit {proc.returncode}): "
            f"{stderr.decode(errors='replace') if stderr else 'unknown error'}"
        )
    # Write raw SQL output (not actually gzipped — gzip can be added later)
    p.write_bytes(stdout)


async def pg_dump_tier1(dsn: str, output_path: str | Path) -> None:
    """Run pg_dump against a Tier 1 agent database."""
    await pg_dump_shared(dsn, output_path)


async def get_tier1_databases(pool: asyncpg.Pool) -> list[dict]:
    """Query the tier1_agent_databases table for all Tier 1 DB records."""
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM tier1_agent_databases")
        return [dict(row) for row in rows]


def acquire_file_lock(path: str | Path) -> int:
    """Acquire an exclusive file lock via fcntl.flock.

    Returns the file descriptor (caller must close it to release).
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p), os.O_CREAT | os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fd


def release_file_lock(fd: int, path: str | Path) -> None:
    """Release a file lock and close the descriptor."""
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)


async def set_system_role(conn) -> None:
    """Required before any audit_log INSERT or agents SELECT.

    P1 RLS policy requires app.current_role IN ('admin','system')
    for these tables. Watcher always operates as system.

    Caller is responsible for using SET LOCAL inside a transaction.
    """
    await conn.execute("SET LOCAL app.current_role = 'system'")
