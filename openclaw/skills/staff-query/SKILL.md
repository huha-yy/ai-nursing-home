# staff-query — 员工查询

> Python handler 不注册为可调用工具。不能直接调用 `staff_query.query_staff()`。
> 必须通过 **process 工具**执行 `python3 -c` 调用 handler。

## 功能

- **查询员工信息** — `query_staff(db_url, name, building)` 按姓名或楼栋查询员工

## 查询参数

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | string | 否 | 姓名模糊搜索 |
| `building` | string | 否 | 楼号精确匹配（如 `"3号楼"`） |

两个参数均可选，可组合使用。不传任何参数返回全院所有员工。

## 触发词

当用户提到以下内容时，使用此技能：

- "查一下李芳"、"3号楼有哪些员工"、"护工信息"
- "员工列表"、"staff list"、"who works in building 3"
- "楼长是谁"、"护理部有谁"

## 如何调用 handler

handler 函数接受 `db_url` 作为第一参数，agent 容器内通过环境变量 `DATABASE_URL` 获取数据库连接串。

### 按楼栋查询

```bash
python3 -c "
import sys, os, asyncio
sys.path.insert(0, '/opt/openclaw/skills/custom/staff-query')
from handler import query_staff
db_url = os.environ['DATABASE_URL']
result = asyncio.run(query_staff(db_url, building='3号楼'))
import json
print(json.dumps(result, indent=2, ensure_ascii=False))
"
```

### 按姓名查询

```bash
python3 -c "
import sys, os, asyncio
sys.path.insert(0, '/opt/openclaw/skills/custom/staff-query')
from handler import query_staff
db_url = os.environ['DATABASE_URL']
result = asyncio.run(query_staff(db_url, name='李'))
import json
print(json.dumps(result, indent=2, ensure_ascii=False))
"
```

返回示例：
```json
[
  {
    "name": "李卫东",
    "role": "building",
    "dept": null,
    "building": "3号楼",
    "floor": null
  },
  {
    "name": "王护士",
    "role": "nursing_dept",
    "dept": "护理科",
    "building": null,
    "floor": null
  }
]
```
