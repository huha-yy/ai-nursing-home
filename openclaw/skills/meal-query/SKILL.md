# meal-query — 餐饮查询

> Python handler 不注册为可调用工具。不能直接调用 `meal_query.query_today_meals()`。
> 必须通过 **process 工具**执行 `python3 -c` 调用 handler。

## 功能

- **今日菜单** — `query_today_meals(db_url, meal_type)` 查询当天三餐（可按餐别过滤）
- **本周菜单** — `query_week_meals(db_url)` 查询本周周一至周日全部菜单

## 触发词

当用户提到以下内容时，使用此技能：

- "今天吃什么"、"今天早餐/午餐/晚餐"、"今日菜单"
- "这周吃什么"、"本周菜单"、"一周菜谱"
- "meal plan"、"weekly menu"、"today's food"

## 如何调用 handler

handler 函数接受 `db_url` 作为第一参数，agent 容器内通过环境变量 `DATABASE_URL` 获取数据库连接串。

### 今日菜单

```bash
python3 -c "
import sys, os, asyncio
sys.path.insert(0, '/opt/openclaw/skills/custom/meal-query')
from handler import query_today_meals
db_url = os.environ['DATABASE_URL']
result = asyncio.run(query_today_meals(db_url))
import json
print(json.dumps(result, indent=2, ensure_ascii=False))
"
```

参数说明：
- `meal_type` (string, optional): 餐别过滤（`"早餐"` / `"午餐"` / `"晚餐"`），不传返回全部

### 本周菜单

```bash
python3 -c "
import sys, os, asyncio
sys.path.insert(0, '/opt/openclaw/skills/custom/meal-query')
from handler import query_week_meals
db_url = os.environ['DATABASE_URL']
result = asyncio.run(query_week_meals(db_url))
import json
print(json.dumps(result, indent=2, ensure_ascii=False))
"
```

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
  },
  {
    "date": "2026-07-21",
    "meal_type": "晚餐",
    "menu": "肉末蒸蛋、香菇炖鸡、清炒西蓝花、馒头、红豆汤"
  }
]
```
