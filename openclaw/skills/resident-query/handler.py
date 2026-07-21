"""resident-query skill handler — 老人信息查询.

Queries ``nursing_residents`` by name, room, or building.

The agent container provides DATABASE_URL in its environment;
pass it as ``db_url`` to every function.
"""

from __future__ import annotations

from typing import Optional

import asyncpg


async def query_resident(
    db_url: str,
    name: Optional[str] = None,
    room: Optional[str] = None,
    building: Optional[str] = None,
) -> list[dict]:
    """Query residents with optional filters.

    Returns a list of dicts with keys ``name``, ``building``, ``floor``,
    ``room``, ``age``, ``diagnosis``, ``care_level``, ``notes``.
    """
    conn = await asyncpg.connect(db_url)
    try:
        query = (
            "SELECT name, building, floor, room, age, diagnosis, care_level, notes "
            "FROM nursing_residents WHERE 1=1"
        )
        params: list = []

        if name:
            query += " AND name LIKE $" + str(len(params) + 1)
            params.append(f"%{name}%")

        if room:
            query += " AND room = $" + str(len(params) + 1)
            params.append(room)

        if building:
            query += " AND building = $" + str(len(params) + 1)
            params.append(building)

        query += " ORDER BY building, floor, room"

        rows = await conn.fetch(query, *params)
        return [
            {
                "name": r["name"],
                "building": r["building"],
                "floor": r["floor"],
                "room": r["room"],
                "age": r["age"],
                "diagnosis": r["diagnosis"],
                "care_level": r["care_level"],
                "notes": r["notes"],
            }
            for r in rows
        ]
    finally:
        await conn.close()
