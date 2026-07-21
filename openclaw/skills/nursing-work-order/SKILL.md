# nursing-work-order — 护理工单管理

> Python handler 不注册为可调用工具。不能直接调用 `nursing_work_order.query_work_orders()`。
> 必须通过 **process 工具**执行 `python3 -c` 调用 handler。

## 功能

- **查询工单完成率** — `query_work_orders(building, target_date)` 按护理类型统计完成情况，返回完成率汇总

## 触发词

当用户提到以下内容时，使用此技能：

- "工单完成情况"、"护理完成率"、"完成率"
- "今天任务完成了吗"、"今天工单"
- "3号楼护理完成情况"、"查询工单"
- "work order status"、"completion rate"

## 如何调用 handler

handler 函数接受 `db_url` 作为第一参数，agent 容器内通过环境变量 `DATABASE_URL` 获取数据库连接串。

### 查询全院工单完成率

```bash
python3 -c "
import sys, os, asyncio
sys.path.insert(0, '/opt/openclaw/skills/custom/nursing-work-order')
from handler import query_work_orders
db_url = os.environ['DATABASE_URL']
result = asyncio.run(query_work_orders(db_url))
import json
print(json.dumps(result, indent=2, ensure_ascii=False))
"
```

### 查询指定楼栋工单完成率

```bash
python3 -c "
import sys, os, asyncio
sys.path.insert(0, '/opt/openclaw/skills/custom/nursing-work-order')
from handler import query_work_orders
db_url = os.environ['DATABASE_URL']
result = asyncio.run(query_work_orders(db_url, building='3号楼'))
import json
print(json.dumps(result, indent=2, ensure_ascii=False))
"
```

参数说明：
- `building` (string, optional): 楼号过滤（如 `"3号楼"`），不传则为全院汇总
- `target_date` (string, optional): 查询日期，格式 `YYYY-MM-DD`，默认今天

返回示例：
```json
{
  "date": "2026-07-21",
  "building": "3号楼",
  "overall_rate": "85.7%",
  "by_type": [
    {
      "type": "鼻饲",
      "total": 2,
      "completed": 2,
      "rate": "100.0%"
    },
    {
      "type": "翻身护理",
      "total": 2,
      "completed": 2,
      "rate": "100.0%"
    },
    {
      "type": "协助排便",
      "total": 1,
      "completed": 1,
      "rate": "100.0%"
    },
    {
      "type": "吸氧",
      "total": 1,
      "completed": 1,
      "rate": "100.0%"
    },
    {
      "type": "进食鼓励",
      "total": 1,
      "completed": 0,
      "rate": "0.0%"
    }
  ]
}
```

### 查询历史某天的完成率

```bash
python3 -c "
import sys, os, asyncio
sys.path.insert(0, '/opt/openclaw/skills/custom/nursing-work-order')
from handler import query_work_orders
db_url = os.environ['DATABASE_URL']
result = asyncio.run(query_work_orders(db_url, target_date='2026-07-20'))
import json
print(json.dumps(result, indent=2, ensure_ascii=False))
"
```
