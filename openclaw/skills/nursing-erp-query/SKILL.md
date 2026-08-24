# nursing-erp-query — 养老院业务系统数据查询与写入

> 通过 REST API 读写 `nursing-erp` 的真实业务数据。**替代旧的 Mock 数据查询。**
> Python handler 不注册为可调用工具。必须通过 **process 工具**执行 `python3 -c` 调用。

## 核心概念

旧的 `nursing-*` 技能（nursing-schedule, nursing-work-order, resident-query, staff-query 等）查询的是 `dato-postgres` 数据库中的 Mock 种子数据。

**从本技能生效起，所有养老院业务数据查询和写入都应通过 nursing-erp REST API**，不再直接查 Mock 数据库。

API 地址通过环境变量 `NURSING_ERP_URL` 获取，默认为 `http://nursing-erp:8080`。

---

## 触发词

当用户提到以下内容时，使用此技能查询/写入真实业务数据：

- **老人相关**: "张国栋的情况"、"1号楼有哪些老人"、"查老人档案"、"xxx老人的护理日志"
- **护理日志**: "记录xxx"、"今天xxx做了xxx"、"写护理日志"
- **排班**: "今天谁当班"、"3号楼排班"、"查看排班"
- **员工**: "护理科有哪些人"、"张护士的联系方式"
- **异常上报**: "上报异常"、"xxx老人摔倒了"、"一键上报"
- **库存**: "尿不湿还有多少"、"库存不够了"、"盘点库存"
- **点餐**: "今天吃什么"、"午餐菜单"、"xxx老人点餐"、"退餐"
- **健康**: "血压记录"、"用药情况"、"血糖多少"
- **评估定级**: "谁该复评了"、"待评估名单"、"评估盘点"、"张国栋评估结果"、"能力等级"、"定级"
- **财务**: "这个月应收多少"、"谁欠费"、"欠费名单"、"缴费情况"、"账单"、"出账"、"核销"

---

## 何时使用本技能（替代旧技能）

| 旧查询方式 | 新查询方式 |
|-----------|-----------|
| `nursing-schedule` handler 直接查 Postgres | 本技能查 nursing-erp `/api/schedules/` |
| `resident-query` handler 查 Mock 数据 | 本技能查 `/api/residents/` |
| `staff-query` handler 查 Mock 数据 | 本技能查 `/api/employees/` |
| `logistics-inventory` handler | 本技能查 `/api/inventory/` |
| 旧的 `nursing-work-order` handler | 本技能查 `/api/incidents/`（异常上报替代了工单） |
| `finance-query` handler 查 Mock `nursing_finances` 表 | 本技能查 `/api/billing/`（应收月账单：床位费+护理费+餐费） |

---

## API 端点速查表

### 老人照护

| 操作 | 端点 | 参数 |
|------|------|------|
| 查老人列表 | `GET /api/residents/` | `?building=1号楼&care_level=全护&search=张` |
| 查老人详情 | `GET /api/residents/{id}/` | — |
| 查护理日志 | `GET /api/residents/{id}/logs/` | `?log_date=2026-08-04` |
| 查健康数据 | `GET /api/residents/{id}/health/` | — |
| 查用药记录 | `GET /api/residents/{id}/medications/` | — |

### 入住评估（国标 GB/T 42195-2022）

| 操作 | 端点 | 参数 |
|------|------|------|
| 查评估单列表 | `GET /api/assessments/` | `?resident_id=7&status=draft`（draft=待定级 / confirmed=已定级） |
| 查评估单详情 | `GET /api/assessments/{id}/` | —（含 26 项明细、总分 0-100、能力等级 0-4、建议/定级护理档） |
| 评估状态盘点 | `GET /api/assessments/review/` | —（rows=待评估/待复评名单，state 字段区分 + 三态计数） |

> 评估口径：总分 0-100 **越高越差**（能力受损程度）；等级 0-4 档；
> 护理档映射 0/1→自理、2→半护、3/4→全护（失智只能定级时人工改判）。
> 国标要求**每 12 个月复评一次**——回答"谁该复评/谁还没评"用 review 端点；
> 回答"某老人的评估结果"用列表（带 resident_id）或详情。

### 人员管理

| 操作 | 端点 | 参数 |
|------|------|------|
| 查员工列表 | `GET /api/employees/` | `?dept=护理科&is_caregiver=true` |
| 查考勤 | `GET /api/employees/{id}/attendance/` | `?start_date=2026-08-01` |
| 查排班 | `GET /api/schedules/` | `?date=2026-08-04&building=3号楼` |

### 院内事务

| 操作 | 端点 | 参数 |
|------|------|------|
| 查库存 | `GET /api/inventory/` | `?category=护理耗材` |
| 低库存预警 | `GET /api/inventory/low-stock/` | — |
| 查报修 | `GET /api/maintenance/` | `?status=pending` |

### 异常上报

| 操作 | 端点 | 参数 |
|------|------|------|
| 查异常 | `GET /api/incidents/` | `?severity=danger&handled=false` |

### 写入操作

| 操作 | 端点 | 请求体 |
|------|------|------|
| 写护理日志 | `POST /api/nursing-logs/` | `{resident_id, category, detail, staff_name, log_date?}` |
| 写健康记录 | `POST /api/health-records/` | `{resident_id, blood_pressure?, blood_sugar?, heart_rate?, ...}` |
| 上报异常 | `POST /api/incidents/` | `{resident_id, category, severity?, description?}` |

### 点餐送餐

| 操作 | 端点 | 参数 |
|------|------|------|
| 查菜单 | `GET /api/meal-plans/` | `?date=2026-08-04` |
| 查点餐订单 | `GET /api/meal-orders/` | `?date=2026-08-04&status=preparing` |
| 查餐费月结 | `GET /api/meal-finance/` | `?month=2026-08` |

### 财务账单（应收月账单 = 床位费+护理费+餐费）

| 操作 | 端点 | 参数 |
|------|------|------|
| 查账单列表 | `GET /api/billing/` | `?month=2026-08&status=pending` |
| 查三额汇总 | `GET /api/billing/summary/` | `?month=2026-08`（缺省当月）→ 应收/已收/欠缴 |
| 查欠费名单 | `GET /api/billing/arrears/` | `?month=2026-08`（截止月，**含更早账期**，跨月累计） |
| 生成月账单 | `POST /api/billing/generate/` | `?month=2026-09&building=1号楼&resident_id=7` |
| 核销账单 | `POST /api/billing/{id}/settle/` | `{"note": "减免后全额", "settled_by": "刘主任"}` |
| 撤销核销 | `POST /api/billing/{id}/unsettle/` | —（回待缴费，可再生成刷新金额） |

> 欠费口径：`arrears` 榜单是**跨月累计**（如"吴桂英欠 3 个月共 12,840"），
> 不是单月；`summary` 是单月三额勾稽。回答"谁欠费"用 arrears，
> 回答"这个月应收多少"用 summary。

---

## 如何调用 API

所有调用通过 `process` 工具执行 `python3 -c` 一行命令：

### 查询类操作

```bash
python3 -c "
import httpx, os, json
url = os.environ.get('NURSING_ERP_URL', 'http://nursing-erp:8080')
r = httpx.Client(base_url=url, timeout=10)
resp = r.get('/api/residents/', params={'building': '1号楼', 'care_level': '全护'})
data = resp.json()
for item in data.get('items', data):
    print(json.dumps(item, ensure_ascii=False, indent=2))
"
```

### 写入类操作

```bash
python3 -c "
import httpx, os
url = os.environ.get('NURSING_ERP_URL', 'http://nursing-erp:8080')
r = httpx.Client(base_url=url, timeout=10)
resp = r.post('/api/nursing-logs/', json={
    'resident_id': 1,
    'category': 'vital_signs',
    'detail': '血压 135/85，正常',
    'staff_name': '李芳',
})
print(resp.json())
"
```

### 异常上报

```bash
python3 -c "
import httpx, os
url = os.environ.get('NURSING_ERP_URL', 'http://nursing-erp:8080')
r = httpx.Client(base_url=url, timeout=10)
resp = r.post('/api/incidents/', json={
    'resident_id': 1,
    'category': 'fall',
    'severity': 'danger',
    'description': '老人在走廊摔倒',
})
print(resp.json())
"
```

---

## 回答时注意事项

1. **数据真实性** — API 返回的是护理员实际录入的数据，不是 Mock 数据。如果查不到结果，说明该数据尚未录入。
2. **分页** — API 默认每页 50 条，返回格式为 `{"items": [...], "count": N}`。告诉用户时提取 `items` 列表。
3. **中文友好** — 回答用户时使用中文，老人名字、楼栋名称等中文内容直接展示，不做翻译。
4. **关联查询** — 先查到老人 ID，再查其护理日志/健康数据。分两步调用 API。
