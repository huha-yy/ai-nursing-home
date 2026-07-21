# resident-query — 老人信息查询

> Python handler 不注册为可调用工具。不能直接调用 `resident_query.query_resident()`。
> 必须通过 **process 工具**执行 `python3 -c` 调用 handler。

## 功能

- **查询老人档案** — `query_resident(db_url, name, room, building)` 按姓名/房号/楼栋查询老人信息

## 查询参数

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | string | 否 | 姓名模糊搜索 |
| `room` | string | 否 | 房间号精确匹配 |
| `building` | string | 否 | 楼号精确匹配 |

所有参数均可选，可组合使用。不传任何参数返回全院所有老人。

## 触发词

当用户提到以下内容时，使用此技能：

- "查一下张国栋"、"101房间住的是谁"、"1号楼有哪些老人"
- "老人信息"、"住户档案"、"resident info"
- "全护的老人有哪些"、"失智老人"

## 如何调用 handler

handler 函数接受 `db_url` 作为第一参数，agent 容器内通过环境变量 `DATABASE_URL` 获取数据库连接串。

### 按姓名查询

```bash
python3 -c "
import sys, os, asyncio
sys.path.insert(0, '/opt/openclaw/skills/custom/resident-query')
from handler import query_resident
db_url = os.environ['DATABASE_URL']
result = asyncio.run(query_resident(db_url, name='张国栋'))
import json
print(json.dumps(result, indent=2, ensure_ascii=False))
"
```

### 按楼栋查询

```bash
python3 -c "
import sys, os, asyncio
sys.path.insert(0, '/opt/openclaw/skills/custom/resident-query')
from handler import query_resident
db_url = os.environ['DATABASE_URL']
result = asyncio.run(query_resident(db_url, building='3号楼'))
import json
print(json.dumps(result, indent=2, ensure_ascii=False))
"
```

返回示例：
```json
[
  {
    "name": "张国栋",
    "building": "1号楼",
    "floor": "1层",
    "room": "101",
    "age": 78,
    "diagnosis": "高血压, 糖尿病",
    "care_level": "自理",
    "notes": "每日自测血压，清淡饮食"
  }
]
```
