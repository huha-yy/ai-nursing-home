"""P8 precreated agents — suppressions DAL.

Thin data-access layer for the precreated_suppressions table.
Callers manage their own transactions — `conn` is an already-open
async DB connection.
"""

from __future__ import annotations

import psycopg


async def suppress(
    conn: psycopg.AsyncConnection,
    *,
    precreated_id: str,
    user_id: str | None,
) -> bool:
    cur = await conn.execute(
        "INSERT INTO precreated_suppressions (precreated_id, suppressed_by) "
        "VALUES (%s, %s) ON CONFLICT (precreated_id) DO NOTHING",
        (precreated_id, user_id),
    )
    return cur.rowcount == 1


async def unsuppress(conn: psycopg.AsyncConnection, *, precreated_id: str) -> bool:
    cur = await conn.execute(
        "DELETE FROM precreated_suppressions WHERE precreated_id = %s",
        (precreated_id,),
    )
    return cur.rowcount == 1


async def list_suppressed(
    conn: psycopg.AsyncConnection,
) -> list[dict]:
    cur = await conn.execute(
        "SELECT precreated_id, suppressed_at, suppressed_by "
        "FROM precreated_suppressions ORDER BY precreated_id"
    )
    return [
        {
            "precreated_id": r[0],
            "suppressed_at": r[1],
            "suppressed_by": str(r[2]) if r[2] else None,
        }
        for r in await cur.fetchall()
    ]


async def is_suppressed(conn: psycopg.AsyncConnection, *, precreated_id: str) -> bool:
    cur = await conn.execute(
        "SELECT 1 FROM precreated_suppressions WHERE precreated_id = %s",
        (precreated_id,),
    )
    return (await cur.fetchone()) is not None
