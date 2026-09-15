#!/usr/bin/env python3
"""nursing_activities / nursing_complaints 演示内容双语重播（P5 广交会英文演示）。

口径与 seed_work_orders_demo.py 一致：
- 幂等：两表只有 PK、无业务唯一索引 → 整表 DELETE 再 INSERT（本两表是纯
  演示数据面，无生产语义；03-nursing-seed.sql 首装基线不动）
- 写入走 docker exec psql；--dry-run 只生成并预览 SQL，不写库
- 楼栋引用在自由文本里写作 Building N（en）/N号楼（zh）

楼栋/楼层值双语 UPDATE（P5b 2026-09-15 追加，重播前先跑）：
- nursing_users / nursing_residents / nursing_schedules 三表的 building/
  floor 枚举值 en↔zh 切换（幂等 UPDATE，WHERE 旧值命中才改）
- 与 ERP rebuild_demo_data.py --lang 的 BUILDING_ZH_EN/FLOOR_ZH_EN 同值
  （X-Building 权限链：nursing_users.building → dl-control 发头 → ERP
  Building 表校验，两边必须同语言，否则楼长 400 越权失败）
- 恢复中文：--lang zh 反向回译（或重跑 zh keepfresh）

activities 生成规则（对齐库内既有演示形态：每天 4 场固定时段，讲座/手工
主题按日轮换）：过去 7 天 ～ 未来 15 天（--until YYYY-MM-DD 可延长/缩短）。
complaints：3 条真实感演示行（对齐 03-seed 口径：pending 2 / resolved 1）。

用法：python3 scripts/seed_pg_demo_en.py [--lang zh|en] [--until 2026-09-30]
      python3 scripts/seed_pg_demo_en.py --dry-run            # 预览 SQL，不写库
"""

from __future__ import annotations

import subprocess
import sys
from datetime import date, timedelta


def _parse_lang() -> str:
    if "--lang" in sys.argv:
        i = sys.argv.index("--lang")
        val = sys.argv[i + 1] if i + 1 < len(sys.argv) else "en"
    else:
        val = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--lang=")), "en")
    if val not in ("zh", "en"):
        sys.exit(f"--lang 只支持 zh|en，收到 {val!r}")
    return val


LANG = _parse_lang()
DRY = "--dry-run" in sys.argv
BACK_DAYS = 7
AHEAD_DAYS = 15

ACTIVITIES = {
    "zh": {
        "morning": ("晨间操·八段锦", "09:00-09:30", "各楼栋一层大厅"),
        "lecture": "健康讲座：{topic}",
        "lecture_topics": ["防跌倒居家改造", "慢病用药安全", "秋季养生与润肺",
                           "膳食营养搭配", "记忆保护训练", "睡眠卫生"],
        "lecture_room": ("10:00-11:00", "3号楼会议室"),
        "chess": ("棋牌友谊赛", "14:00-16:00", "活动中心棋牌室"),
        "craft": "手工课：{topic}",
        "craft_topics": ["编绳挂饰", "剪纸窗花", "黏土画", "折纸花", "绘画涂色"],
        "craft_room": ("16:00-17:00", "2号楼活动室"),
    },
    "en": {
        "morning": ("Morning Exercise · Baduanjin", "09:00-09:30",
                    "Ground-floor lobbies, all buildings"),
        "lecture": "Health Talk: {topic}",
        "lecture_topics": ["Fall-Proofing the Home", "Safe Use of Chronic-Disease Medications",
                           "Autumn Health & Lung Care", "Balanced Nutrition",
                           "Memory-Preserving Training", "Sleep Hygiene"],
        "lecture_room": ("10:00-11:00", "Building 3 Meeting Room"),
        "chess": ("Friendship Chess & Card Tournament", "14:00-16:00",
                  "Activity Center Games Room"),
        "craft": "Handcraft Session: {topic}",
        "craft_topics": ["Braided Cord Pendants", "Paper-Cut Window Flowers",
                         "Clay Painting", "Origami Flowers", "Painting & Coloring"],
        "craft_room": ("16:00-17:00", "Building 2 Activity Room"),
    },
}

COMPLAINTS = {
    "zh": [
        ("2号楼1层晚上走廊灯光太亮，影响睡眠", "家属反馈", "pending"),
        ("餐厅早餐粥太稀，希望增加稠度", "老人代表", "resolved"),
        ("5号楼2层空调制冷不足，已连续3天报修未处理", "护工上报", "pending"),
    ],
    "en": [
        ("Corridor lights on floor 1 of Building 2 are too bright at night, "
         "affecting residents' sleep", "Family feedback", "pending"),
        ("Breakfast porridge in the dining hall is too thin; residents would "
         "like it thicker", "Resident representative", "resolved"),
        ("Air conditioning on floor 2 of Building 5 is not cooling; reported 3 "
         "days in a row with no repair yet", "Caregiver report", "pending"),
    ],
}

# ── 楼栋/楼层枚举值 zh↔en（P5b）─────────────────────────────────────
# 与 ERP rebuild_demo_data.py 的 BUILDING_ZH_EN / FLOOR_ZH_EN 逐值一致
# （X-Building 权限链同语言要求）；UPDATE 幂等——WHERE 旧值不命中即 0 行。
BUILDING_ZH_EN = {f"{i}号楼": f"Building {i}" for i in range(1, 7)}
FLOOR_ZH_EN = {"1层": "Floor 1", "2层": "Floor 2"}
assert len(set(BUILDING_ZH_EN.values())) == len(BUILDING_ZH_EN)
assert len(set(FLOOR_ZH_EN.values())) == len(FLOOR_ZH_EN)


def building_overlay_sql() -> str:
    """三表 building/floor 枚举值切换 SQL（en 正向 / zh 反向回译）。"""
    bmap = BUILDING_ZH_EN if LANG == "en" else {v: k for k, v in BUILDING_ZH_EN.items()}
    fmap = FLOOR_ZH_EN if LANG == "en" else {v: k for k, v in FLOOR_ZH_EN.items()}
    stmts = []
    for table in ("nursing_users", "nursing_residents", "nursing_schedules"):
        for col, mapping in (("building", bmap), ("floor", fmap)):
            for old, new in mapping.items():
                stmts.append(f"UPDATE {table} SET {col}='{new}' WHERE {col}='{old}';")
    return "\n".join(stmts) + "\n"


def build_activity_rows(today: date, until: date) -> list[tuple]:
    spec = ACTIVITIES[LANG]
    rows: list[tuple] = []
    d = today - timedelta(days=BACK_DAYS)
    while d <= until:
        rot = d.toordinal()
        rows.append((spec["morning"][0], d.isoformat(), spec["morning"][1],
                     spec["morning"][2]))
        rows.append((spec["lecture"].format(
            topic=spec["lecture_topics"][rot % len(spec["lecture_topics"])]),
            d.isoformat(), spec["lecture_room"][0], spec["lecture_room"][1]))
        rows.append((spec["chess"][0], d.isoformat(), spec["chess"][1],
                     spec["chess"][2]))
        rows.append((spec["craft"].format(
            topic=spec["craft_topics"][rot % len(spec["craft_topics"])]),
            d.isoformat(), spec["craft_room"][0], spec["craft_room"][1]))
        d += timedelta(days=1)
    return rows


def _lit(v: object) -> str:
    """SQL 字面量：单引号 + '' 转义（repr() 遇撇号换双引号会被 psql 主变量替换）"""
    if isinstance(v, str):
        return "'" + v.replace("'", "''") + "'"
    return str(v)


def main() -> None:
    today = date.today()
    until = today + timedelta(days=AHEAD_DAYS)
    if "--until" in sys.argv:
        i = sys.argv.index("--until")
        try:
            until = date.fromisoformat(sys.argv[i + 1])
        except (IndexError, ValueError):
            sys.exit("--until 需要 YYYY-MM-DD 参数")
    acts = build_activity_rows(today, until)
    comps = COMPLAINTS[LANG]
    days = sorted({r[1] for r in acts})
    print(f"[lang={LANG}] 楼栋/楼层枚举值 UPDATE（users/residents/schedules）"
          f" + activities {len(acts)} 行（{days[0]} ～ {days[-1]}，每天 4 场）；"
          f"complaints {len(comps)} 行")
    for r in acts[:4]:
        print(f"  {r}")

    act_values = ",\n  ".join("(" + ", ".join(_lit(v) for v in r) + ")" for r in acts)
    comp_values = ",\n  ".join("(" + ", ".join(_lit(v) for v in r) + ")" for r in comps)
    sql = (
        building_overlay_sql()
        + "DELETE FROM nursing_activities;\n"
        + "INSERT INTO nursing_activities (title, date, time, location) VALUES\n"
        f"  {act_values};\n"
        "DELETE FROM nursing_complaints;\n"
        "INSERT INTO nursing_complaints (content, source, status) VALUES\n"
        f"  {comp_values};\n"
    )
    if DRY:
        print(f"[dry-run] SQL 已生成（{len(sql)} 字符），头部预览"
              "（楼栋/楼层 UPDATE 在前，activities/complaints 重播在后）：")
        print("\n".join("  " + ln for ln in sql.splitlines()[:6]))
        return
    proc = subprocess.run(
        ["docker", "exec", "-i", "dato-postgres", "psql", "-U", "dato", "-d", "dato",
         "-v", "ON_ERROR_STOP=1"],
        input=sql, text=True, capture_output=True,
    )
    if proc.returncode != 0:
        sys.exit(f"psql 失败：{proc.stderr[:500]}")
    print(proc.stdout.strip())


if __name__ == "__main__":
    main()
