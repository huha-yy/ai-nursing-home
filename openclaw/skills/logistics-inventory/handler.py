"""logistics-inventory skill handler — 库存管理.

Queries ``nursing_inventory`` to list all items with alert status
(quantity < safety_stock = alert) and to list low-stock items with
replenishment suggestions.

The agent container provides DATABASE_URL in its environment;
pass it as ``db_url`` to every function.
"""

from __future__ import annotations

import asyncpg


async def check_inventory(db_url: str) -> list[dict]:
    """List all inventory items with alert status.

    Returns a list of dicts, each with keys ``item_name``, ``category``,
    ``quantity``, ``unit``, ``safety_stock``, ``alert``, ``suggestion``.
    The ``alert`` field is True when quantity < safety_stock.
    """
    conn = await asyncpg.connect(db_url)
    try:
        rows = await conn.fetch(
            "SELECT item_name, category, quantity, unit, safety_stock "
            "FROM nursing_inventory ORDER BY category, item_name"
        )
        results: list[dict] = []
        for r in rows:
            alert = r["quantity"] < r["safety_stock"]
            suggestion = ""
            if alert:
                suggestion = (
                    f"建议采购：{r['item_name']} 当前库存 {r['quantity']}{r['unit']}，"
                    f"安全库存 {r['safety_stock']}{r['unit']}，"
                    f"缺 {r['safety_stock'] - r['quantity']}{r['unit']}"
                )
            results.append(
                {
                    "item_name": r["item_name"],
                    "category": r["category"],
                    "quantity": r["quantity"],
                    "unit": r["unit"],
                    "safety_stock": r["safety_stock"],
                    "alert": alert,
                    "suggestion": suggestion,
                }
            )
        return results
    finally:
        await conn.close()


async def check_low_stock(db_url: str) -> list[dict]:
    """List only items below safety_stock with suggestion text.

    Returns a subset of ``check_inventory`` filtered to ``alert == True``.
    """
    conn = await asyncpg.connect(db_url)
    try:
        rows = await conn.fetch(
            "SELECT item_name, category, quantity, unit, safety_stock "
            "FROM nursing_inventory "
            "WHERE quantity < safety_stock "
            "ORDER BY (safety_stock - quantity) DESC"
        )
        results: list[dict] = []
        for r in rows:
            results.append(
                {
                    "item_name": r["item_name"],
                    "category": r["category"],
                    "quantity": r["quantity"],
                    "unit": r["unit"],
                    "safety_stock": r["safety_stock"],
                    "alert": True,
                    "suggestion": (
                        f"建议采购：{r['item_name']} 当前库存 {r['quantity']}{r['unit']}，"
                        f"安全库存 {r['safety_stock']}{r['unit']}，"
                        f"缺 {r['safety_stock'] - r['quantity']}{r['unit']}"
                    ),
                }
            )
        return results
    finally:
        await conn.close()
