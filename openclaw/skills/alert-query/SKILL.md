# alert-query — 预警查询

> Python handler 不注册为可调用工具。不能直接调用 `alert_query.query_alerts()`。
> 必须通过 **process 工具**执行 `python3 -c` 调用 handler。

## 功能

- **健康预警查询** — `query_alerts(db_url, building, handled)` 查询全院或分楼栋的健康预警信息

## 查询参数

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `building` | string | 否 | 楼号过滤（如 `"3号楼"`），不传返回全院 |
| `handled` | bool | 否 | 是否已处理，不传返回全部 |

预警严重级别：`info`（信息）< `warning`（警示）< `danger`（危险）

## 触发词

当用户提到以下内容时，使用此技能：

- "预警"、"健康预警"、"警告"、"alert"
- "有哪些告警"、"未处理的预警"、"3号楼有什么问题"
- "查看风险"、"health alerts"、"warnings"

## 如何调用 handler

handler 函数接受 `db_url` 作为第一参数，agent 容器内通过环境变量 `DATABASE_URL` 获取数据库连接串。

### 查询全院未处理预警

```bash
python3 -c "
import sys, os, asyncio
sys.path.insert(0, '/opt/openclaw/skills/custom/alert-query')
from handler import query_alerts
db_url = os.environ['DATABASE_URL']
result = asyncio.run(query_alerts(db_url, handled=False))
import json
print(json.dumps(result, indent=2, ensure_ascii=False))
"
```

### 查询某楼栋所有预警

```bash
python3 -c "
import sys, os, asyncio
sys.path.insert(0, '/opt/openclaw/skills/custom/alert-query')
from handler import query_alerts
db_url = os.environ['DATABASE_URL']
result = asyncio.run(query_alerts(db_url, building='3号楼'))
import json
print(json.dumps(result, indent=2, ensure_ascii=False))
"
```

返回示例：
```json
[
  {
    "resident_id": "R004",
    "content": "右侧臀部皮肤出现红肿，需加强翻身和皮肤护理，防止压疮形成",
    "category": "皮肤护理",
    "severity": "danger",
    "created_at": "2026-07-21T00:00:00",
    "handled": false
  },
  {
    "resident_id": "R001",
    "content": "连续3日血压偏高（收缩压>150mmHg），建议调整降压药方案",
    "category": "高血压",
    "severity": "warning",
    "created_at": "2026-07-21T00:00:00",
    "handled": false
  }
]
```
