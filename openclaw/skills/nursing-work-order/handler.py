"""nursing-work-order skill handler — 护理工单管理.

Queries ``nursing_work_orders`` and computes completion rates grouped by
care type.  Supports optional building filtering via a JOIN on
``nursing_residents``.

The agent container provides DATABASE_URL in its environment;
pass it as ``db_url`` to every function.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

import asyncpg


async def query_work_orders(
    db_url: str,
    building: Optional[str] = None,
    target_date: Optional[str] = None,
) -> dict:
    """Query work orders and compute completion rate.

    Returns a summary dict with keys ``date``, ``building``, ``overall_rate``,
    and ``by_type`` (a list of per-type dicts with ``type``, ``total``,
    ``completed``, ``rate``).
    """
    conn = await asyncpg.connect(db_url)
    try:
        if target_date is None:
            target_date = date.today().isoformat()

        query = """
            SELECT type, COUNT(*) as total,
                   SUM(CASE WHEN completed THEN 1 ELSE 0 END) as done
            FROM nursing_work_orders
            WHERE date = $1
        """
        params: list = [target_date]

        if building:
            query += (
                " AND resident_id IN "
                "(SELECT id FROM nursing_residents WHERE building = $2)"
            )
            params.append(building)

        query += " GROUP BY type ORDER BY type"

        rows = await conn.fetch(query, *params)

        by_type: list[dict] = []
        total_all = 0
        done_all = 0
        for r in rows:
            rate = round(r["done"] / r["total"] * 100, 1) if r["total"] > 0 else 0
            by_type.append(
                {
                    "type": r["type"],
                    "total": r["total"],
                    "completed": r["done"],
                    "rate": f"{rate}%",
                }
            )
            total_all += r["total"]
            done_all += r["done"]

        overall_rate = (
            round(done_all / total_all * 100, 1) if total_all > 0 else 0
        )

        return {
            "date": target_date,
            "building": building or "全院",
            "overall_rate": f"{overall_rate}%",
            "by_type": by_type,
        }
    finally:
        await conn.close()
