# activity-query — 活动查询

> Python handler 不注册为可调用工具。不能直接调用 `activity_query.query_week_activities()`。
> 必须通过 **process 工具**执行 `python3 -c` 调用 handler。

## 功能

- **本周活动** — `query_week_activities(db_url)` 查询本周周一至周日院内活动安排

## 触发词

当用户提到以下内容时，使用此技能：

- "这周有什么活动"、"本周活动安排"、"活动表"
- "今天有什么活动"、"明天活动"
- "activities this week"、"event schedule"

## 如何调用 handler

handler 函数接受 `db_url` 作为第一参数，agent 容器内通过环境变量 `DATABASE_URL` 获取数据库连接串。

### 查询本周活动

```bash
python3 -c "
import sys, os, asyncio
sys.path.insert(0, '/opt/openclaw/skills/custom/activity-query')
from handler import query_week_activities
db_url = os.environ['DATABASE_URL']
result = asyncio.run(query_week_activities(db_url))
import json
print(json.dumps(result, indent=2, ensure_ascii=False))
"
```

返回示例：
```json
[
  {
    "title": "太极拳晨练",
    "date": "2026-07-21",
    "time": "07:00-07:40",
    "location": "1号楼前广场"
  },
  {
    "title": "书法兴趣班",
    "date": "2026-07-22",
    "time": "14:30-16:00",
    "location": "活动中心书画室"
  },
  {
    "title": "红歌大家唱",
    "date": "2026-07-23",
    "time": "15:00-16:30",
    "location": "多功能厅"
  }
]
```
