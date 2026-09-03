# nursing-schedule — 护工排班管理

> Python handler 不注册为可调用工具。不能直接调用 `nursing_schedule.generate_weekly_schedule()`。
> 必须通过 **process 工具**执行 `python3 -c` 调用 handler。

## 功能

- **生成周排班** — `generate_weekly_schedule(building, start_date)` 自动为一栋楼的护工生成一周排班
- **查询排班** — `query_schedule(building, date)` 查询某日某楼排班明细

## 排班规则

| 规则 | 说明 |
|---|---|
| 班制 | 12 小时两班倒 — 白班 7:00-19:00 / 夜班 19:00-7:00 |
| 班次值 | 库内只写 `白班`/`夜班` 裸值（大屏按精确字符串统计，带时段后缀的行不可见） |
| 轮休 | 做六休一（≥3 人时每人每周轮休一天）；每人每天至多一班，绝不叠第二班 |
| 人员范围 | 优先取本楼近 28 天排班在册人员（真名册在排班史里）；无历史退回 `nursing_users` 的楼长/楼层组长 |
| 插入策略 | `ON CONFLICT DO NOTHING`，已存在的排班不会重复插入 |
| 回读口径 | 返回的汇总数与 `schedule` 明细一律从库内回读（含预铺/重跑的既有行），不是生成清单 |

## 触发词

当用户提到以下内容时，使用此技能：

- "生成排班"、"排班表"、"排班"
- "这周谁值班"、"下周谁值班"、"今天谁白班/夜班"
- "3号楼这周排班"、"查看排班"
- "building 3 schedule"、"nursing roster"

## 如何调用 handler

handler 函数接受 `db_url` 作为第一参数，agent 容器内通过环境变量 `DATABASE_URL` 获取数据库连接串。

### 生成周排班

```bash
python3 -c "
import sys, os, asyncio
sys.path.insert(0, '/opt/openclaw/skills/custom/nursing-schedule')
from handler import generate_weekly_schedule
db_url = os.environ['DATABASE_URL']
result = asyncio.run(generate_weekly_schedule(db_url, building='3号楼'))
import json
print(json.dumps(result, indent=2, ensure_ascii=False))
"
```

参数说明：
- `building` (string, required): 楼号，如 `"3号楼"`、`"1号楼"`
- `start_date` (string, optional): 排班起始日期，格式 `YYYY-MM-DD`，默认今天

返回示例：
```json
{
  "week": "2026-09-03-2026-09-09",
  "building": "3号楼",
  "staff_count": 3,
  "total_shifts": 17,
  "day_shifts": 10,
  "night_shifts": 7,
  "schedule": [
    {"date": "2026-09-03", "白班": "吴秀丽、孙志明", "夜班": "周玉英"},
    {"date": "2026-09-04", "白班": "吴秀丽、孙志明", "夜班": "周玉英"},
    {"date": "2026-09-05", "白班": "吴秀丽、孙志明", "夜班": "周玉英"},
    {"date": "2026-09-06", "白班": "吴秀丽、孙志明", "夜班": "周玉英"},
    {"date": "2026-09-07", "白班": "吴秀丽、孙志明", "夜班": "周玉英"},
    {"date": "2026-09-08", "白班": "孙志明", "夜班": "周玉英"},
    {"date": "2026-09-09", "白班": "吴秀丽、孙志明", "夜班": "周玉英"}
  ]
}
```

错误返回：
```json
{"error": "未找到3号楼的护工人员"}
```

### 查询排班

```bash
python3 -c "
import sys, os, asyncio
sys.path.insert(0, '/opt/openclaw/skills/custom/nursing-schedule')
from handler import query_schedule
db_url = os.environ['DATABASE_URL']
result = asyncio.run(query_schedule(db_url, building='3号楼'))
import json
print(json.dumps(result, indent=2, ensure_ascii=False))
"
```

参数说明：
- `building` (string, required): 楼号
- `target_date` (string, optional): 查询日期，格式 `YYYY-MM-DD`，默认今天

返回示例：
```json
[
  {
    "staff_name": "孙志明",
    "shift": "白班(7-19)",
    "building": "3号楼",
    "floor": "2层",
    "zone": "A区"
  },
  {
    "staff_name": "周玉英",
    "shift": "夜班(19-7)",
    "building": "3号楼",
    "floor": "2层",
    "zone": "A区"
  }
]
```
