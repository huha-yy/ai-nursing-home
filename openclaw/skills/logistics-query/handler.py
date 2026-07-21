"""logistics-query skill handler — 后勤查询.

Queries ``nursing_meals`` for meal plans and ``nursing_inventory``
for individual item details.

The agent container provides DATABASE_URL in its environment;
pass it as ``db_url`` to every function.
"""

from __future__ import annotations

from datetime import date as _date
from typing import Optional

import asyncpg


async def query_meals(
    db_url: str,
    date: Optional[str] = None,
) -> list[dict]:
    """Return meal entries for a given date (default today).

    Returns a list of dicts with keys ``date``, ``meal_type``, ``menu``.
    """
    conn = await asyncpg.connect(db_url)
    try:
        if date is None:
            date = _date.today().isoformat()

        rows = await conn.fetch(
            "SELECT date, meal_type, menu FROM nursing_meals "
            "WHERE date = $1 ORDER BY meal_type",
            date,
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


async def query_item(db_url: str, item_name: str) -> dict | None:
    """Return detail for a specific inventory item searched by name (LIKE match).

    Returns a dict with keys ``item_name``, ``category``, ``quantity``,
    ``unit``, ``safety_stock``, ``alert``, ``suggestion``, or None if
    no match.
    """
    conn = await asyncpg.connect(db_url)
    try:
        row = await conn.fetchrow(
            "SELECT item_name, category, quantity, unit, safety_stock "
            "FROM nursing_inventory WHERE item_name LIKE $1 LIMIT 1",
            f"%{item_name}%",
        )
        if row is None:
            return None

        alert = row["quantity"] < row["safety_stock"]
        suggestion = ""
        if alert:
            suggestion = (
                f"建议采购：{row['item_name']} 当前库存 {row['quantity']}{row['unit']}，"
                f"安全库存 {row['safety_stock']}{row['unit']}，"
                f"缺 {row['safety_stock'] - row['quantity']}{row['unit']}"
            )
        return {
            "item_name": row["item_name"],
            "category": row["category"],
            "quantity": row["quantity"],
            "unit": row["unit"],
            "safety_stock": row["safety_stock"],
            "alert": alert,
            "suggestion": suggestion,
        }
    finally:
        await conn.close()
