# logistics-inventory — 库存管理

> Python handler 不注册为可调用工具。不能直接调用 `logistics_inventory.check_inventory()`。
> 必须通过 **process 工具**执行 `python3 -c` 调用 handler。

## 功能

- **库存总览** — `check_inventory(db_url)` 查询全院库存清单，自动标注预警状态
- **低库存预警** — `check_low_stock(db_url)` 列出所有低于安全库存的物品及采购建议

## 预警规则

| 规则 | 说明 |
|---|---|
| 预警判断 | `quantity < safety_stock` 即为预警状态 |
| 采购建议 | 自动生成包含缺少数量的中文采购建议文本 |

## 触发词

当用户提到以下内容时，使用此技能：

- "库存情况"、"查库存"、"库存清单"
- "低库存"、"缺货"、"库存预警"、"需要采购什么"
- "inventory"、"low stock"、"check stock"

## 如何调用 handler

handler 函数接受 `db_url` 作为第一参数，agent 容器内通过环境变量 `DATABASE_URL` 获取数据库连接串。

### 查看全院库存（含预警状态）

```bash
python3 -c "
import sys, os, asyncio
sys.path.insert(0, '/opt/openclaw/skills/custom/logistics-inventory')
from handler import check_inventory
db_url = os.environ['DATABASE_URL']
result = asyncio.run(check_inventory(db_url))
import json
print(json.dumps(result, indent=2, ensure_ascii=False))
"
```

返回示例：
```json
[
  {
    "item_name": "胃管",
    "category": "医疗器械",
    "quantity": 45,
    "unit": "根",
    "safety_stock": 10,
    "alert": false,
    "suggestion": ""
  },
  {
    "item_name": "医用胶带",
    "category": "护理耗材",
    "quantity": 55,
    "unit": "卷",
    "safety_stock": 10,
    "alert": false,
    "suggestion": ""
  }
]
```

### 只看低库存物品

```bash
python3 -c "
import sys, os, asyncio
sys.path.insert(0, '/opt/openclaw/skills/custom/logistics-inventory')
from handler import check_low_stock
db_url = os.environ['DATABASE_URL']
result = asyncio.run(check_low_stock(db_url))
import json
print(json.dumps(result, indent=2, ensure_ascii=False))
"
```

返回示例：
```json
[
  {
    "item_name": "消毒液",
    "category": "清洁消毒",
    "quantity": 3,
    "unit": "瓶",
    "safety_stock": 10,
    "alert": true,
    "suggestion": "建议采购：消毒液 当前库存 3瓶，安全库存 10瓶，缺 7瓶"
  }
]
```
