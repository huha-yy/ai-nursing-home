# report-generate — 院长周报生成

汇总各部门输出数据，生成结构化的运营周报。

## 触发词

- 生成周报 / 运营报表 / 汇总报表
- generate weekly report / ops report

## 调用方式

使用 `process` 工具执行 Python 代码调用 handler：

```python
import sys; sys.path.insert(0, '/opt/openclaw/skills/custom/report-generate')
from handler import generate_weekly_report
result = generate_weekly_report(
    db_url=os.environ['DATABASE_URL'],
    schedule_data='''<排班JSON>''',
    logistics_data='''<物资JSON>''',
    finance_data='''<成本JSON>'''
)
```

## 输出格式

```json
{
  "report_type": "weekly_ops",
  "title": "养老院运营周报",
  "period": "2026-07-21 - 2026-07-27",
  "sections": {
    "排班概况": { ... },
    "物资配送": { ... },
    "成本预估": { ... },
    "重点关注": [ ... ]
  }
}
```

## 依赖

- `DATABASE_URL` 环境变量
- asyncpg
