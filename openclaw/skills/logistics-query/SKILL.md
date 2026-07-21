# logistics-query — 后勤查询

> Python handler 不注册为可调用工具。不能直接调用 `logistics_query.query_meals()`。
> 必须通过 **process 工具**执行 `python3 -c` 调用 handler。

## 功能

- **查询餐食** — `query_meals(db_url, date)` 查询指定日期的三餐菜单
- **查询库存物品** — `query_item(db_url, item_name)` 按物品名称模糊搜索库存详情

## 触发词

当用户提到以下内容时，使用此技能：

- "今天吃什么"、"今天菜单"、"明天吃什么"
- "查一下尿不湿"、"血糖试纸还有多少"、"库存里有没有xxx"
- "meals"、"item search"、"food menu"

## 如何调用 handler

handler 函数接受 `db_url` 作为第一参数，agent 容器内通过环境变量 `DATABASE_URL` 获取数据库连接串。

### 查询某天餐食

```bash
python3 -c "
import sys, os, asyncio
sys.path.insert(0, '/opt/openclaw/skills/custom/logistics-query')
from handler import query_meals
db_url = os.environ['DATABASE_URL']
result = asyncio.run(query_meals(db_url))
import json
print(json.dumps(result, indent=2, ensure_ascii=False))
"
```

参数说明：
- `date` (string, optional): 查询日期，格式 `YYYY-MM-DD`，默认今天

返回示例：
```json
[
  {
    "date": "2026-07-21",
    "meal_type": "早餐",
    "menu": "小米粥、鸡蛋、葱花卷、凉拌黄瓜、牛奶"
  },
  {
    "date": "2026-07-21",
    "meal_type": "午餐",
    "menu": "清蒸鲈鱼、西红柿炒蛋、炒青菜、米饭、紫菜汤"
  }
]
```

### 查询单个库存物品

```bash
python3 -c "
import sys, os, asyncio
sys.path.insert(0, '/opt/openclaw/skills/custom/logistics-query')
from handler import query_item
db_url = os.environ['DATABASE_URL']
result = asyncio.run(query_item(db_url, item_name='尿不湿'))
import json
print(json.dumps(result, indent=2, ensure_ascii=False))
"
```

参数说明：
- `item_name` (string, required): 物品名称（支持模糊匹配）

返回示例：
```json
{
  "item_name": "尿不湿 L码",
  "category": "护理耗材",
  "quantity": 480,
  "unit": "包",
  "safety_stock": 50,
  "alert": false,
  "suggestion": ""
}
```

未找到时返回 `null`。
