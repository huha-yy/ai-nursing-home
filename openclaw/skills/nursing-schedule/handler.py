"""nursing-schedule skill handler — 护工排班管理.

Generates weekly 12h shift schedules (白班 7:00-19:00 / 夜班 19:00-7:00) with
做六休一 rotation, then reads the week back from the database so the returned
detail always reflects what is actually scheduled (pre-existing rows included).

Two canonical rules (2026-09-03 fix):

1. Shift values are the plain "白班"/"夜班" — dashboards count on the exact
   strings (dl-control main.py schedule_today). The old "(7-19)"-suffixed
   writes were invisible to the dashboard yet polluted chat/report queries.
2. No double-booking: each staff member gets at most one shift per day, and
   never a second shift on top of rows already in the database. The old
   "simple rotation" put every nurse on both shifts every single day,
   violating the 做六休一 rule the docstring promised.

The agent container provides DATABASE_URL in its environment; pass it as
``db_url`` to every function.
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

    Roster basis: staff already on this building's schedule in the past 28
    days (nursing_users only holds login accounts — 楼长/楼层组长 — not the
    care workers), falling back to nursing_users building/floor roles for a
    building with no schedule history.

    Returns a summary dict with keys ``week``, ``building``, ``staff_count``,
    ``total_shifts``, ``day_shifts``, ``night_shifts`` and a ``schedule``
    array (one entry per day: ``{"date", "白班", "夜班"}`` with staff names
    joined by 、) read back from the database — or ``error`` if no staff
    were found.
    """
    conn = await asyncpg.connect(db_url)
    try:
        if start_date is None:
            start_date = date.today().isoformat()
        current = date.fromisoformat(start_date)
        end = current + timedelta(days=6)

        # 名册：近 28 天在本楼排过班的护理员（真名册在排班史里，不在
        # nursing_users——那张表只有楼长/组长登录账号）；空历史退回账号表。
        staff_names = [
            r["staff_name"]
            for r in await conn.fetch(
                "SELECT DISTINCT staff_name FROM nursing_schedules "
                "WHERE building = $1 AND date >= $2 AND date < $3 "
                "ORDER BY staff_name",
                building,
                current - timedelta(days=28),
                current,
            )
        ]
        if not staff_names:
            staff_names = [
                s["name"]
                for s in await conn.fetch(
                    "SELECT name FROM nursing_users "
                    "WHERE building = $1 AND role IN ('building','floor') "
                    "ORDER BY name",
                    building,
                )
            ]
        if not staff_names:
            return {"error": f"未找到{building}的护工人员"}

        # 本周既有行按 (天, 班次) 聚合：补缺口用——绝不给已有班的人叠第二班
        existing: dict = {}
        for r in await conn.fetch(
            "SELECT date, shift, staff_name FROM nursing_schedules "
            "WHERE building = $1 AND date BETWEEN $2 AND $3",
            building,
            current,
            end,
        ):
            existing.setdefault(r["date"], {}).setdefault(r["shift"], set()).add(
                r["staff_name"])

        n = len(staff_names)
        per_shift = max(1, min(2, n // 3))  # ≤3 人楼每班 1 名，6 人楼 2 名
        rows: list[tuple] = []
        for d in range(7):
            day = current + timedelta(days=d)
            day_rows = existing.get(day, {})
            booked = {name for names in day_rows.values() for name in names}
            pool = [s for s in staff_names if s not in booked]
            deficits = {
                shift: max(0, per_shift - len(day_rows.get(shift, ())))
                for shift in ("白班", "夜班")
            }
            needed = deficits["白班"] + deficits["夜班"]
            if not pool or needed == 0:
                continue  # 满编或无人可派——现有行已覆盖，回读见真相
            # 做六休一：人手够补缺口才放轮休（覆盖优先于人休，缺人时取消轮休）
            resters = {staff_names[i] for i in range(n)
                       if n >= 3 and (i + d) % 7 == 6}
            resting = {s for s in pool if s in resters}
            if len(pool) - len(resting) >= needed:
                pool = [s for s in pool if s not in resting]
            rot = pool[d % len(pool):] + pool[: d % len(pool)]
            # 缺口大的班次先补（夜班整段真空时不能默认往白班塞人）
            for shift in sorted(deficits, key=deficits.get, reverse=True):
                for name in rot[: deficits[shift]]:
                    rows.append((name, day, shift, building))
                rot = rot[deficits[shift]:]

        await conn.executemany(
            "INSERT INTO nursing_schedules (staff_name, date, shift, building) "
            "VALUES ($1,$2,$3,$4) ON CONFLICT DO NOTHING",
            rows,
        )

        # 回读本周实际排班（含预铺/重跑的既有行）——返回口径 = 库内真相
        detail = await conn.fetch(
            "SELECT date, shift, string_agg(staff_name, '、' ORDER BY staff_name) AS staff "
            "FROM nursing_schedules WHERE building = $1 AND date BETWEEN $2 AND $3 "
            "GROUP BY date, shift ORDER BY date, shift",
            building,
            current,
            end,
        )
        by_day: dict[str, dict] = {}
        for r in detail:
            slot = by_day.setdefault(
                r["date"].isoformat(), {"date": r["date"].isoformat()}
            )
            slot[r["shift"]] = r["staff"]
        tally = await conn.fetchrow(
            "SELECT count(*) FILTER (WHERE shift = '白班') AS day_n, "
            "count(*) FILTER (WHERE shift = '夜班') AS night_n, "
            "count(DISTINCT staff_name) AS people "
            "FROM nursing_schedules WHERE building = $1 AND date BETWEEN $2 AND $3",
            building,
            current,
            end,
        )
        return {
            "week": f"{current.isoformat()}-{end.isoformat()}",
            "building": building,
            "staff_count": tally["people"],
            "total_shifts": tally["day_n"] + tally["night_n"],
            "day_shifts": tally["day_n"],
            "night_shifts": tally["night_n"],
            "schedule": [by_day[k] for k in sorted(by_day)],
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
