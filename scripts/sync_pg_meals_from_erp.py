#!/usr/bin/env python3
"""PG nursing_meals 从 ERP WeekMenu 全量同步（口径：chat 菜单 = 看板菜单）。

背景（2026-10-08）：PG 侧 nursing_meals 的行来自 ERP meals_weekmenu 拼菜名。
中英文演示切换（switch_demo_lang.sh）会重写 ERP 菜名的语言，PG 必须跟着
重导，否则英文演示时 chat 查菜单回另一种语言的菜名。也可单独跑（新增
未来周菜单后补 PG）。

语义：以 ERP WeekMenu 生成的日期范围为准——范围内 DELETE+INSERT（幂等），
范围之外的更早历史行保留不动。唯一索引 uq_meals_date_meal 天然防叠灌。

用法：python3 scripts/sync_pg_meals_from_erp.py [--erp-db PATH] [--dry-run]
"""

from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import subprocess
import sys

PG_CONTAINER = "dato-postgres"
PG_USER = "dato"
PG_DB = "dato"

_DAY_OFFSET = {"周一": 0, "周二": 1, "周三": 2, "周四": 3,
               "周五": 4, "周六": 5, "周日": 6}

# sqlite date() 的修饰符只认数字，CASE 展开写在 SQL 里
_MENU_SQL = """
SELECT date(w.week_start, '+' || (
    CASE w.day
        WHEN '周一' THEN 0 WHEN '周二' THEN 1 WHEN '周三' THEN 2
        WHEN '周四' THEN 3 WHEN '周五' THEN 4 WHEN '周六' THEN 5 ELSE 6 END
) || ' days'),
       w.meal_type,
       group_concat(d.name, '、')
FROM meals_weekmenu w
JOIN meals_weekmenu_dishes wd ON wd.weekmenu_id = w.id
JOIN meals_dish d ON d.id = wd.dish_id
GROUP BY w.id
ORDER BY 1, 2
"""


def build_rows(erp_db: str) -> list[tuple[str, str, str]]:
    con = sqlite3.connect(f"file:{erp_db}?mode=ro", uri=True)
    try:
        rows = con.execute(_MENU_SQL).fetchall()
    finally:
        con.close()
    if not rows:
        sys.exit("✗ ERP WeekMenu 为空——先跑 rebuild_demo_data.py 再同步")
    return [(a, b, c) for a, b, c in rows]


def _psql(sql: str, stdin: str | None = None) -> None:
    cmd = ["docker", "exec", "-i", PG_CONTAINER, "psql", "-U", PG_USER, "-d", PG_DB,
           "-v", "ON_ERROR_STOP=1", "-c", sql]
    proc = subprocess.run(cmd, input=stdin, text=True)
    if proc.returncode != 0:
        sys.exit(f"✗ psql 失败：{sql[:80]}…")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--erp-db",
                    default="/home/nursing-home/huha-project/nursing-erp/db.sqlite3")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rows = build_rows(args.erp_db)
    dates = {r[0] for r in rows}
    lo, hi = min(dates), max(dates)
    print(f"ERP WeekMenu → PG nursing_meals：{len(rows)} 行（{lo} ～ {hi}）")

    if args.dry_run:
        for r in rows[:5]:
            print("  ", r)
        print("  …（dry-run，未写库）")
        return

    tsv = "\n".join("\t".join(r) for r in rows)
    _psql("DROP TABLE IF EXISTS _staging_meals; "
          "CREATE TABLE _staging_meals (date date, meal_type text, menu text)")
    _psql("\\copy _staging_meals FROM STDIN", stdin=tsv)
    _psql(f"DELETE FROM nursing_meals WHERE date >= DATE '{lo}'; "
          "INSERT INTO nursing_meals (date, meal_type, menu) "
          "SELECT date, meal_type, menu FROM _staging_meals; "
          "DROP TABLE _staging_meals;")
    print("✓ 同步完成（范围内 DELETE+INSERT，幂等）")


if __name__ == "__main__":
    main()
