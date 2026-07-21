"""finance-query skill handler — 费用查询.

Queries ``nursing_finances`` joined with ``nursing_residents`` to
return per-resident monthly fee records.

The agent container provides DATABASE_URL in its environment;
pass it as ``db_url`` to every function.
"""

from __future__ import annotations

import asyncpg


async def query_resident_finance(
    db_url: str,
    resident_name: str,
) -> list[dict]:
    """Query finance records for a resident by name (LIKE match).

    Returns a list of dicts with keys ``name``, ``month``, ``amount``,
    ``paid``, ``status`` where status is "已结清" or "未结清".
    """
    conn = await asyncpg.connect(db_url)
    try:
        rows = await conn.fetch(
            "SELECT r.name, f.month, f.amount, f.paid "
            "FROM nursing_finances f "
            "JOIN nursing_residents r ON f.resident_id = r.id "
            "WHERE r.name LIKE $1 "
            "ORDER BY f.month DESC",
            f"%{resident_name}%",
        )

        results: list[dict] = []
        for row in rows:
            results.append(
                {
                    "name": row["name"],
                    "month": row["month"],
                    "amount": float(row["amount"]) if row["amount"] else 0.0,
                    "paid": row["paid"],
                    "status": "已结清" if row["paid"] else "未结清",
                }
            )
        return results
    finally:
        await conn.close()
