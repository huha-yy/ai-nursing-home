# finance-query — 费用查询

> Python handler 不注册为可调用工具。不能直接调用 `finance_query.query_resident_finance()`。
> 必须通过 **process 工具**执行 `python3 -c` 调用 handler。

## 功能

- **费用查询** — `query_resident_finance(db_url, resident_name)` 按老人姓名查询月度费用及缴纳状态

## 触发词

当用户提到以下内容时，使用此技能：

- "查一下张国栋的费用"、"费用交了吗"、"缴费情况"
- "哪些人没交费"、"欠费查询"
- "finance"、"payment status"、"fee query"

## 如何调用 handler

handler 函数接受 `db_url` 作为第一参数，agent 容器内通过环境变量 `DATABASE_URL` 获取数据库连接串。

### 查询老人费用

```bash
python3 -c "
import sys, os, asyncio
sys.path.insert(0, '/opt/openclaw/skills/custom/finance-query')
from handler import query_resident_finance
db_url = os.environ['DATABASE_URL']
result = asyncio.run(query_resident_finance(db_url, resident_name='张国栋'))
import json
print(json.dumps(result, indent=2, ensure_ascii=False))
"
```

参数说明：
- `resident_name` (string, required): 老人姓名（支持模糊匹配）

返回示例：
```json
[
  {
    "name": "张国栋",
    "month": "2026-07",
    "amount": 3800.0,
    "paid": true,
    "status": "已结清"
  }
]
```

未找到记录时返回空列表 `[]`。
