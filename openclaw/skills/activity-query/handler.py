"""activity-query skill handler — 活动查询.

Queries ``nursing_activities`` for this week's activity schedule.

The agent container provides DATABASE_URL in its environment;
pass it as ``db_url`` to every function.
"""

from __future__ import annotations

from datetime import date as _date, timedelta

import asyncpg


async def query_week_activities(db_url: str) -> list[dict]:
    """Return this week's (Monday–Sunday) activities.

    Returns a list of dicts with keys ``title``, ``date``, ``time``,
    ``location``, ordered by date then time.
    """
    conn = await asyncpg.connect(db_url)
    try:
        today = _date.today()
        monday = today - timedelta(days=today.weekday())
        sunday = monday + timedelta(days=6)

        rows = await conn.fetch(
            "SELECT title, date, time, location FROM nursing_activities "
            "WHERE date >= $1 AND date <= $2 ORDER BY date, time",
            monday.isoformat(),
            sunday.isoformat(),
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()
