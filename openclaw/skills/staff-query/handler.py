"""staff-query skill handler — 员工查询.

Queries ``nursing_users`` for staff info by name or building.

The agent container provides DATABASE_URL in its environment;
pass it as ``db_url`` to every function.
"""

from __future__ import annotations

from typing import Optional

import asyncpg


async def query_staff(
    db_url: str,
    name: Optional[str] = None,
    building: Optional[str] = None,
) -> list[dict]:
    """Query staff (nursing_users) with optional filters.

    Returns a list of dicts with keys ``name``, ``role``, ``dept``,
    ``building``, ``floor``.
    """
    conn = await asyncpg.connect(db_url)
    try:
        query = (
            "SELECT name, role, dept, building, floor "
            "FROM nursing_users WHERE 1=1"
        )
        params: list = []

        if name:
            query += " AND name LIKE $" + str(len(params) + 1)
            params.append(f"%{name}%")

        if building:
            query += " AND building = $" + str(len(params) + 1)
            params.append(building)

        query += " ORDER BY role, building, floor, name"

        rows = await conn.fetch(query, *params)
        return [dict(r) for r in rows]
    finally:
        await conn.close()
