"""alert-query skill handler — 预警查询.

Queries ``nursing_health_alerts`` with optional building and handled
filters.  Joins ``nursing_residents`` for name info.

The agent container provides DATABASE_URL in its environment;
pass it as ``db_url`` to every function.
"""

from __future__ import annotations

from typing import Optional

import asyncpg


async def query_alerts(
    db_url: str,
    building: Optional[str] = None,
    handled: Optional[bool] = None,
) -> list[dict]:
    """Query health alerts with optional filters.

    Returns a list of dicts with keys ``resident_id``, ``content``,
    ``category``, ``severity``, ``created_at``, ``handled``,
    ordered by created_at descending.
    """
    conn = await asyncpg.connect(db_url)
    try:
        query = (
            "SELECT a.resident_id, a.content, a.category, a.severity, "
            "a.created_at, a.handled "
            "FROM nursing_health_alerts a "
        )
        params: list = []

        if building:
            query += (
                "JOIN nursing_residents r ON a.resident_id = r.id "
                "WHERE r.building = $" + str(len(params) + 1)
            )
            params.append(building)
        else:
            query += "WHERE 1=1"

        if handled is not None:
            query += " AND a.handled = $" + str(len(params) + 1)
            params.append(handled)

        query += " ORDER BY a.created_at DESC"

        rows = await conn.fetch(query, *params)
        return [
            {
                "resident_id": r["resident_id"],
                "content": r["content"],
                "category": r["category"],
                "severity": r["severity"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "handled": r["handled"],
            }
            for r in rows
        ]
    finally:
        await conn.close()
