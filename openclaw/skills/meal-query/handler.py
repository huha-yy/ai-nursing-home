"""meal-query skill handler — 餐饮查询.

Queries ``nursing_meals`` for today's or this week's meal plan.

The agent container provides DATABASE_URL in its environment;
pass it as ``db_url`` to every function.
"""

from __future__ import annotations

from datetime import date as _date, timedelta
from typing import Optional

import asyncpg


async def query_today_meals(
    db_url: str,
    meal_type: Optional[str] = None,
) -> list[dict]:
    """Return today's meals, optionally filtered by meal_type (早餐/午餐/晚餐).

    Returns a list of dicts with keys ``date``, ``meal_type``, ``menu``.
    """
    conn = await asyncpg.connect(db_url)
    try:
        today = _date.today().isoformat()

        if meal_type:
            rows = await conn.fetch(
                "SELECT date, meal_type, menu FROM nursing_meals "
                "WHERE date = $1 AND meal_type = $2 ORDER BY meal_type",
                today,
                meal_type,
            )
        else:
            rows = await conn.fetch(
                "SELECT date, meal_type, menu FROM nursing_meals "
                "WHERE date = $1 ORDER BY meal_type",
                today,
            )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


async def query_week_meals(db_url: str) -> list[dict]:
    """Return this week's (Monday–Sunday) meal plan.

    Returns a list of dicts with keys ``date``, ``meal_type``, ``menu``,
    ordered by date then meal_type.
    """
    conn = await asyncpg.connect(db_url)
    try:
        today = _date.today()
        monday = today - timedelta(days=today.weekday())
        sunday = monday + timedelta(days=6)

        rows = await conn.fetch(
            "SELECT date, meal_type, menu FROM nursing_meals "
            "WHERE date >= $1 AND date <= $2 ORDER BY date, meal_type",
            monday.isoformat(),
            sunday.isoformat(),
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()
