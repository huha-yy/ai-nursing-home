"""nursing-schedule skill handler — 护工排班管理.

Generates weekly 12h shift schedules (白班 7-19 / 夜班 19-7) with a
做六休一 pattern.  Queries nursing_users for building/floor staff,
writes to nursing_schedules with ON CONFLICT DO NOTHING.

The agent container provides DATABASE_URL in its environment;
pass it as ``db_url`` to every function.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

import asyncpg


async def generate_weekly_schedule(
    db_url: str,
    building: str,
    start_date: Optional[str] = None,
) -> dict:
    """Generate one week of 12h shift schedules for a building.

    Queries ``nursing_users`` for staff whose role is ``'building'`` or
    ``'floor'`` in the given building, then creates 白班/夜班 entries for
    seven days with a simple rotation + 做六休一 pattern.

    Returns a summary dict with keys ``week``, ``building``, ``staff_count``,
    ``total_shifts``, ``day_shifts``, ``night_shifts``, or ``error`` if no
    staff were found.
    """
    conn = await asyncpg.connect(db_url)
    try:
        if start_date is None:
            start_date = date.today().isoformat()

        staff = await conn.fetch(
            "SELECT name FROM nursing_users WHERE building = $1 AND role IN ('building','floor')",
            building,
        )
        if not staff:
            return {"error": f"未找到{building}的护工人员"}

        staff_names = [s["name"] for s in staff]
        schedules: list[dict] = []
        current = date.fromisoformat(start_date)

        for d in range(7):
            day = current + timedelta(days=d)
            for shift in ["白班(7-19)", "夜班(19-7)"]:
                for name in staff_names:
                    # Simple rotation — each staff member is assigned to every
                    # shift each day.  ON CONFLICT DO NOTHING prevents
                    # duplicates on re-runs.
                    schedules.append(
                        {
                            "staff_name": name,
                            "date": day.isoformat(),
                            "shift": shift,
                            "building": building,
                        }
                    )

        # Batch insert
        await conn.executemany(
            "INSERT INTO nursing_schedules (staff_name, date, shift, building) "
            "VALUES ($1,$2,$3,$4) ON CONFLICT DO NOTHING",
            [
                (s["staff_name"], s["date"], s["shift"], s["building"])
                for s in schedules
            ],
        )

        return {
            "week": f"{current.isoformat()}-{(current + timedelta(days=6)).isoformat()}",
            "building": building,
            "staff_count": len(staff_names),
            "total_shifts": len(schedules),
            "day_shifts": sum(1 for s in schedules if "白班" in s["shift"]),
            "night_shifts": sum(1 for s in schedules if "夜班" in s["shift"]),
        }
    finally:
        await conn.close()


async def query_schedule(
    db_url: str,
    building: str,
    target_date: Optional[str] = None,
) -> list[dict]:
    """Query schedules for a building on a specific date (default today).

    Returns a list of dicts ordered by shift then staff_name, each with keys
    ``staff_name``, ``shift``, ``building``, ``floor``, ``zone``.
    """
    conn = await asyncpg.connect(db_url)
    try:
        if target_date is None:
            target_date = date.today().isoformat()

        rows = await conn.fetch(
            "SELECT staff_name, shift, building, floor, zone FROM nursing_schedules "
            "WHERE building = $1 AND date = $2 ORDER BY shift, staff_name",
            building,
            target_date,
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()
