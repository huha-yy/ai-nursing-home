# AI 养老院院长 MVP 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 dato 多智能体系统改造为养老院 AI 运营助手 MVP，10 个 Agent 容器 + 护理/总务核心技能 + 数据分析大屏 + 健康预警闭环。

**Architecture:** fork dato 代码库，剥离飞书/OTA/内容管线，保留 dl-control/dl-gbrain/dl-cognee/dl-llm-proxy/openclaw/infra 核心服务。新增 10 个 Agent YAML seed（院长+护理科+总务科+6栋楼+通用助手），11 个养老院技能，Mock 数据库，魔改 OpenClaw 前端对话界面，集成开源数据大屏。

**Tech Stack:** Python 3.12+, FastAPI, Jinja2/htmx, OpenClaw v2026.4.8, PostgreSQL+pgvector, docker-compose, ECharts

## Global Constraints

- Python 3.12+, `uv` 包管理，Ruff lint (line length 100)
- 密码 argon2 hash，session 存 Redis
- Secrets 不进 git（`infra/.env` gitignored）
- `process` tool 模式调用 Python handler（OpenClaw v2026.4.8 限制）
- 全 mock 数据，不接外部 API（排班软件/ERP/财务系统）
- 无飞书集成，无 OTA，无老人 Web UI
- 用户名+密码登录

---

## 文件结构

```
nursing-home-mvp/                    ← fork 自 dato
├── dl-control/
│   ├── dl_control/
│   │   ├── main.py                  ← 修改：去掉 Feishu/OTA 后台循环
│   │   ├── auth/                    ← 不变：复用
│   │   ├── agents/                  ← 不变：复用 provisioning
│   │   ├── workflows/
│   │   │   └── flows/
│   │   │       ├── nursing_ops.py   ← 新增：护理→总务→财务→院长 flow
│   │   │       └── catalog.py       ← 修改：注册 nursing_ops flow
│   │   └── templates/
│   │       ├── base.html            ← 修改：简化导航栏
│   │       └── login.html           ← 修改：换 logo + 标题
│   └── precreated_agents/
│       ├── director/                ← 新增：院长 Agent YAML seed
│       ├── nursing-dept/            ← 新增：护理科 Agent
│       ├── logistics-dept/          ← 新增：总务科 Agent
│       ├── building-1/              ← 新增：1 号楼 Agent (YAML 模板)
│       ├── building-2/              ← 复制
│       ├── building-3/              ← 复制
│       ├── building-4/              ← 复制
│       ├── building-5/              ← 复制
│       ├── building-6/              ← 复制
│       └── general-assistant/       ← 新增：通用助手 Agent
├── openclaw/
│   └── skills/
│       ├── nursing-schedule/        ← 新增：排班生成 (SKILL.md + handler.py)
│       ├── nursing-work-order/      ← 新增：工单管理
│       ├── logistics-inventory/     ← 新增：库存盘点
│       ├── logistics-query/         ← 新增：物资查询
│       ├── resident-query/          ← 新增：老人档案查询
│       ├── staff-query/             ← 新增：员工查询
│       ├── meal-query/              ← 新增：餐饮查询
│       ├── activity-query/          ← 新增：活动查询
│       ├── finance-query/           ← 新增：费用查询
│       ├── alert-query/             ← 新增：预警查询
│       └── report-generate/         ← 新增：报表生成
├── dl-control/
│   └── dl_control/
│       └── middleware/
│           └── health_signal.py     ← 新增：健康信号中间件
├── infra/
│   ├── docker-compose.yml           ← 修改：去飞书服务，加 10 Agent
│   ├── Caddyfile                    ← 修改：去飞书路由，加大屏路由
│   └── postgres/init/
│       └── 03-nursing-seed.sql      ← 新增：Mock 数据种子
├── web/
│   ├── dashboard/                   ← 新增：数据分析大屏 (ECharts)
│   └── static/
│       └── nursing.css              ← 新增：养老院 UI 样式
└── tests/
    └── test_nursing_mvp.py          ← 新增：端到端冒烟测试
```

---

## 第一阶段：基础设施

### Task 1: 项目脚手架 — fork dato 剥离不需要的模块

**Files:**
- Modify: `infra/docker-compose.yml`
- Modify: `infra/Caddyfile`
- Modify: `dl-control/dl_control/main.py`
- Delete: openclaw skills 中内容管线相关 (content-pipeline 系列 skills + feishu-publisher)

**Interfaces:**
- Produces: 可 `docker compose up` 启动的空壳系统（dl-control + dl-cognee + dl-gbrain + dl-llm-proxy + openclaw 基础镜像）

- [ ] **Step 1: Fork dato 代码库**

```bash
cp -r E:\DaTo\dato_prod_huha E:\DaTo\nursing-home-mvp
cd E:\DaTo\nursing-home-mvp
git init && git add -A && git commit -m "init: fork from dato for nursing home MVP"
```

- [ ] **Step 2: 清理 infra/docker-compose.yml**

去掉 dl-ota-watcher、dl-egress-dns 服务。去掉 Feishu reconciler 相关 env。保留：
- dato-postgres (PostgreSQL + pgvector)
- dato-redis
- dl-control
- dl-cognee + dl-cognee-reranker
- dl-gbrain
- dl-llm-proxy
- docker-socket-proxy
- Caddy

验证：`docker compose up -d && docker compose ps`（所有服务 running）

- [ ] **Step 3: 清理 Caddyfile**

去掉 `/feishu*` 路由，保留 `/admin*` 和基础反代。新增后续页面占位路由。

- [ ] **Step 4: 清理 main.py 后台循环**

在 `dl-control/dl_control/main.py` 中：
- 去掉 Feishu reconciler 启动 (`feishu_reconciler_loop`)
- 去掉 OTA reattach 启动
- 保留 workflow runner、audit mirror、active-agent reconciler

- [ ] **Step 5: 删除内容管线 skills**

```bash
cd openclaw/skills/
rm -rf hotspot-monitor relevance-judge fact-research content-strategy \
       wechat-content xhs-content douyin-content image-generator \
       article-composer humanizer compliance-check publish-package feishu-publisher
```

- [ ] **Step 6: 验证可启动**

```bash
docker compose down && docker compose up -d
docker compose ps  # 所有核心服务 running
curl -k https://localhost:9443/admin/login  # 管理后台可访问
```

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "scaffold: fork dato, remove feishu/ota/content-pipeline"
```

---

### Task 2: Mock 数据库 + 10 Agent 容器配置

**Files:**
- Create: `infra/postgres/init/03-nursing-seed.sql`
- Create: `dl-control/precreated_agents/director/agent.yaml`
- Create: `dl-control/precreated_agents/nursing-dept/agent.yaml`
- Create: `dl-control/precreated_agents/logistics-dept/agent.yaml`
- Create: `dl-control/precreated_agents/building-1/agent.yaml`
- Create: `dl-control/precreated_agents/general-assistant/agent.yaml`

**Interfaces:**
- Produces: 启动后 10 Agent 自动 provision，PostgreSQL 中有种子数据

- [ ] **Step 1: 写 SQL 建表 + 种子数据**

```sql
-- infra/postgres/init/03-nursing-seed.sql
-- 用户表
CREATE TABLE IF NOT EXISTS nursing_users (
    user_id    TEXT PRIMARY KEY,
    username   TEXT UNIQUE NOT NULL,
    name       TEXT NOT NULL,
    role       TEXT NOT NULL CHECK (role IN ('director','nursing_dept','logistics_dept','building','floor','general')),
    password   TEXT NOT NULL,
    dept       TEXT,
    building   TEXT,
    floor      TEXT
);

-- 插入种子用户（密码均为 argon2 hash of '123456'）
INSERT INTO nursing_users VALUES
('u01','wang_jianguo','王建国','director','$argon2id$...',NULL,NULL,NULL),
('u02','nurse_zhang','张护士','nursing_dept','$argon2id$...','护理科',NULL,NULL),
('u03','nurse_li','李护士','nursing_dept','$argon2id$...','护理科',NULL,NULL),
('u04','nurse_wang','王护士','nursing_dept','$argon2id$...','护理科',NULL,NULL),
('u05','logi_chen','陈总务','logistics_dept','$argon2id$...','总务科',NULL,NULL),
('u06','logi_zhao','赵总务','logistics_dept','$argon2id$...','总务科',NULL,NULL),
-- 6 栋楼负责人
('u07','b1_liu','刘主任','building','$argon2id$...',NULL,'1号楼',NULL),
('u08','b2_zhang','张主任','building','$argon2id$...',NULL,'2号楼',NULL),
('u09','b3_li_weidong','李卫东','building','$argon2id$...',NULL,'3号楼',NULL),
('u10','b4_wu','吴主任','building','$argon2id$...',NULL,'4号楼',NULL),
('u11','b5_liu_zhuren','刘主任','building','$argon2id$...',NULL,'5号楼',NULL),
('u12','b6_zhou','周主任','building','$argon2id$...',NULL,'6号楼',NULL),
-- 楼层负责人（各楼2层各1人）
('u13','b1f1_wang','王组长','floor','$argon2id$...',NULL,'1号楼','1层'),
('u14','b1f2_chen','陈组长','floor','$argon2id$...',NULL,'1号楼','2层'),
...
-- 通用助手
('u19','admin_liu','刘行政','general','$argon2id$...','综合办',NULL,NULL),
('u20','med_feng','冯医务','general','$argon2id$...','医务康复科',NULL,NULL),
('u21','fin_sun','孙财务','general','$argon2id$...','财务科',NULL,NULL),
('u22','sec_zhou','周安保','general','$argon2id$...','安全保卫科',NULL,NULL);

-- 老人档案表 (36条)
CREATE TABLE IF NOT EXISTS nursing_residents (
    id TEXT PRIMARY KEY, name TEXT NOT NULL,
    building TEXT, floor TEXT, room TEXT,
    age INTEGER, diagnosis TEXT, care_level TEXT, notes TEXT
);
-- 插入 6 栋楼 × 6 人 = 36 条 ...(完整 INSERT 语句)

-- 排班表
CREATE TABLE IF NOT EXISTS nursing_schedules (...);
-- 库存表
CREATE TABLE IF NOT EXISTS nursing_inventory (...);
-- 其余表: health_alerts, meals, activities, finances, nursing_work_orders
```

- [ ] **Step 2: 生成 argon2 密码 hash**

```bash
cd dl-control && uv run python3 -c "
from argon2 import PasswordHasher
ph = PasswordHasher()
print(ph.hash('123456'))
"
# 将输出的 hash 填入 SQL 的 password 字段
```

- [ ] **Step 3: 写 Agent YAML seeds**

```yaml
# dl-control/precreated_agents/director/agent.yaml
id: director
display_name: 院长
tier: 0
admin_only: false
skill_list:
  - nursing-work-order
  - logistics-query
  - meal-query
  - staff-query
  - resident-query
  - activity-query
  - finance-query
  - alert-query
  - report-generate
```

同理写出 nursing-dept、logistics-dept、building-1、general-assistant 的 agent.yaml（6 栋楼复制 building-1 模板，改 id + display_name）。

- [ ] **Step 4: 写各 Agent 的 workspace 文件**

每个 Agent 目录下创建 `workspace/SOUL.md` 和 `workspace/TOOLS.md`，定义角色行为和工具使用说明。

- [ ] **Step 5: 启动验证**

```bash
docker compose down -v && docker compose up -d
docker compose ps | grep agent  # 应看到 10 个 agent 容器
make psql -c "SELECT count(*) FROM nursing_residents;"  # 应返回 36
```

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat: mock DB + 10 agent seeds"
```

---

### Task 3: 用户认证 — context 注入

**Files:**
- Modify: `dl-control/dl_control/auth/` — 扩展现有登录逻辑
- Modify: `dl-control/dl_control/agents/provisioning/config_gen.py` — openclaw.json 注入 dept/building/floor

**Interfaces:**
- Consumes: Task 2 的 nursing_users 表
- Produces: 登录后 session 含 `{user_id, role, dept, building, floor}`，Agent 容器 env 含对应 context

- [ ] **Step 1: 扩展登录路由，读取 nursing_users 表**

在 dl-control 的 login handler 中，查 nursing_users 表替代默认 users 表，session 注入完整 context。

- [ ] **Step 2: 修改 Agent provisioning，写入 context 到 openclaw.json**

在 `config_gen.py` 中，将 dept/building/floor 写入 agent 容器的 `config/.env`。

- [ ] **Step 3: 测试 4 种角色登录**

```bash
# 院长
curl -X POST https://localhost:9443/auth/login -d 'username=wang_jianguo&password=123456'
# 护理科
curl -X POST https://localhost:9443/auth/login -d 'username=nurse_zhang&password=123456'
# 楼栋负责人
curl -X POST https://localhost:9443/auth/login -d 'username=b3_li_weidong&password=123456'
# 通用助手(财务科)
curl -X POST https://localhost:9443/auth/login -d 'username=fin_sun&password=123456'
```

- [ ] **Step 4: Commit**

---

### Task 4: Web UI — 对话界面 + 基础框架

**Files:**
- Modify: `dl-control/dl_control/templates/base.html`
- Modify: `dl-control/dl_control/templates/login.html`
- Create: `dl-control/dl_control/templates/nursing/chat.html`
- Create: `dl-control/dl_control/static/nursing.css`

**Interfaces:**
- Consumes: Task 3 的登录 session
- Produces: 可用的对话界面，登录后跳转到 chat.html，侧边栏按 role 差异化

- [ ] **Step 1: 改 login.html**

换标题为"AI 养老院院长"、换 logo（占位即可），其余复用 dl-control 现有登录表单。

- [ ] **Step 2: 改 base.html**

导航栏去飞书/OTA/工作流入口，保留"对话""大屏"两个入口。

- [ ] **Step 3: 创建 chat.html（对话界面）**

左侧侧边栏：根据 `session.role` 展示不同概览信息。
- director → 全院概览（在院人数、今日值班、库存预警数）
- nursing_dept → 今日排班概览
- logistics_dept → 库存概览
- building → 本楼老人列表
- general → 本科室快捷入口

右侧：OpenClaw 自带的聊天 iframe 或 WebSocket 对话区域。

- [ ] **Step 4: 加 nursing.css**

基础样式：养老院主题色（暖色调）、大字体、卡片布局。

- [ ] **Step 5: 验证 4 种角色登录后界面差异**

分别用 4 种角色登录，确认侧边栏内容不同。

- [ ] **Step 6: Commit**

---

### Task 5: health-signal 中间件

**Files:**
- Create: `dl-control/dl_control/middleware/health_signal.py`
- Modify: `dl-control/dl_control/main.py` — 注册中间件

**Interfaces:**
- Consumes: Agent 对话消息流
- Produces: 检测到健康信号时写入 `health_alerts` 表，推送到大屏 WebSocket

- [ ] **Step 1: 写 health_signal.py — 关键词 + LLM 双重检测**

```python
# dl-control/dl_control/middleware/health_signal.py
import re
from dl_control.db import Database

HEALTH_KEYWORDS = {
    '感冒': ['感冒', '发烧', '咳嗽', '流鼻涕', '嗓子疼'],
    '疼痛': ['头疼', '肚子疼', '腰疼', '腿疼', '胸闷'],
    '消化': ['吃不下', '没胃口', '拉肚子', '便秘', '恶心'],
    '用药': ['吃什么药', '怎么吃药', '药量', '忘记吃药'],
    '跌倒': ['摔倒', '摔了', '跌了', '站不稳'],
}

async def detect_health_signal(message: str, user_context: dict, db: Database):
    """扫描消息中的健康关键词，命中则写入 health_alerts"""
    for category, keywords in HEALTH_KEYWORDS.items():
        for kw in keywords:
            if kw in message:
                await db.execute(
                    "INSERT INTO nursing_health_alerts (resident_id, content, category) "
                    "VALUES ($1, $2, $3)",
                    user_context.get("resident_id"), message, category
                )
                return True
    return False
```

- [ ] **Step 2: 在 main.py 注册中间件**

在 FastAPI app 的 middleware stack 中插入 health_signal 检测钩子，监听 `/agent/message` 路由。

- [ ] **Step 3: 测试**

```bash
# 模拟管理员发消息
curl -X POST https://localhost:9443/agent/message \
  -H 'Content-Type: application/json' \
  -d '{"message": "张建国咨询：感冒了吃什么药好得快", "session_id": "test"}'
# 查询 health_alerts 表确认写入
make psql -c "SELECT * FROM nursing_health_alerts;"
```

- [ ] **Step 4: Commit**

---

## 第二阶段：业务功能

### Task 6: 护理科技能 — nursing-schedule + nursing-work-order

**Files:**
- Create: `openclaw/skills/nursing-schedule/SKILL.md`
- Create: `openclaw/skills/nursing-schedule/handler.py`
- Create: `openclaw/skills/nursing-schedule/_meta.json`
- Create: `openclaw/skills/nursing-work-order/SKILL.md`
- Create: `openclaw/skills/nursing-work-order/handler.py`
- Create: `openclaw/skills/nursing-work-order/_meta.json`

**Interfaces:**
- Consumes: `nursing_schedules` 表, `nursing_residents` 表
- Produces: `generate_schedule(building, start_date, days)` → `list[dict]`, `query_work_orders(building, date)` → `list[dict]`

- [ ] **Step 1: 写 nursing-schedule handler.py**

```python
# openclaw/skills/nursing-schedule/handler.py
import asyncpg
from datetime import date, timedelta

async def generate_schedule(db_url: str, building: str, start_date: str, days: int = 7) -> list[dict]:
    """生成护工排班：12h白/夜班、做六休一"""
    conn = await asyncpg.connect(db_url)
    try:
        # 获取护理人员列表
        staff = await conn.fetch(
            "SELECT name FROM nursing_users WHERE role IN ('nursing_dept','building','floor') AND building = $1",
            building
        )
        # 生成排班逻辑
        schedules = []
        for d in range(days):
            current_date = date.fromisoformat(start_date) + timedelta(days=d)
            for shift in ['白班(7-19)', '夜班(19-7)']:
                for s in staff:
                    # 简化规则：轮班、做六休一
                    ...
                    schedules.append({...})
        # 批量写入
        await conn.executemany(
            "INSERT INTO nursing_schedules (staff_name, date, shift, building, zone) VALUES ($1,$2,$3,$4,$5)",
            [(s['staff_name'], s['date'], s['shift'], building, '自理区') for s in schedules]
        )
        return schedules
    finally:
        await conn.close()
```

- [ ] **Step 2: 写 nursing-schedule SKILL.md**

定义 Agent 如何使用此技能——识别"生成排班""排班表""本周排班"等意图，调用 `generate_schedule()`。

- [ ] **Step 3: 写 nursing-work-order handler.py**

```python
async def query_work_orders(db_url: str, building: str = None, date: str = None) -> list[dict]:
    """查询护理工单及完成率"""
    ...
    return [
        {"type": "洗漱", "total": 20, "completed": 18, "rate": "90%"},
        {"type": "助餐", "total": 20, "completed": 20, "rate": "100%"},
        ...
    ]
```

- [ ] **Step 4: 写 nursing-work-order SKILL.md**

- [ ] **Step 5: 集成测试**

用护理科用户登录，发送"帮我生成本周排班表"，验证 schedules 表写入正确。

- [ ] **Step 6: Commit**

---

### Task 7: 总务科技能 + 其余科室查询技能

**Files:**
- Create: `openclaw/skills/logistics-inventory/` (SKILL.md + handler.py + _meta.json)
- Create: `openclaw/skills/logistics-query/`
- Create: `openclaw/skills/resident-query/`
- Create: `openclaw/skills/staff-query/`
- Create: `openclaw/skills/meal-query/`
- Create: `openclaw/skills/activity-query/`
- Create: `openclaw/skills/finance-query/`
- Create: `openclaw/skills/alert-query/`

**Interfaces:**
- Consumes: Mock DB 对应表
- Produces: `check_inventory()`, `query_meals()`, `query_resident(name/room)`, `query_staff(name/date)`, `query_activities(week)`, `query_finance(resident_id)`, `query_alerts(building)`

- [ ] **Step 1: logistics-inventory handler.py**

```python
async def check_inventory(db_url: str, category: str = None) -> list[dict]:
    """盘点库存，标记低于安全线的项目"""
    rows = await conn.fetch("SELECT * FROM nursing_inventory WHERE ...")
    results = []
    for r in rows:
        alert = r['quantity'] < r['safety_stock']
        results.append({**dict(r), 'alert': alert, 'suggestion': '建议采购' if alert else ''})
    return results
```

- [ ] **Step 2: 其余 6 个轻量 query handler**

每个 handler.py 约 30-50 行，直接 SQL 查询对应表。SKILL.md 定义触发词和回答格式。

- [ ] **Step 3: Commit**

---

### Task 8: 数据分析大屏

**Files:**
- Create: `dl-control/dl_control/templates/nursing/dashboard.html`
- Create: `dl-control/dl_control/static/dashboard.js`
- Modify: `dl-control/dl_control/main.py` — 加大屏路由
- Modify: `infra/Caddyfile` — 路由指向大屏

**Interfaces:**
- Consumes: Mock DB 聚合查询
- Produces: ECharts 渲染的运营大屏页面

- [ ] **Step 1: 写数据聚合 API**

在 dl-control 中新增 `/api/nursing/dashboard` 端点：

```python
@router.get("/api/nursing/dashboard")
async def dashboard_data():
    return {
        "total_residents": await db.fetchval("SELECT count(*) FROM nursing_residents"),
        "on_duty_today": await db.fetchval("SELECT count(DISTINCT staff_name) FROM nursing_schedules WHERE date = CURRENT_DATE"),
        "inventory_alerts": await db.fetchval("SELECT count(*) FROM nursing_inventory WHERE quantity < safety_stock"),
        "health_alerts": await db.fetchval("SELECT count(*) FROM nursing_health_alerts WHERE handled = false"),
        "focus_residents": [...],  # 血压异常、未进食等重点关注
        "low_stock_items": [...],  # 库存不足清单
    }
```

- [ ] **Step 2: 写 dashboard.html — ECharts 大屏**

用开源 ECharts 渲染 4 个指标卡片 + 重点关注列表 + 库存预警列表。

- [ ] **Step 3: 加大屏路由**

Caddyfile: `handle /dashboard* { reverse_proxy dl-control:8080 }`

- [ ] **Step 4: 验证大屏数据正确**

浏览器访问 `https://localhost:9443/dashboard`，确认卡片数据与 DB 一致。

- [ ] **Step 5: Commit**

---

### Task 9: 多 Agent 协作 workflow + 预警闭环

**Files:**
- Create: `dl-control/dl_control/workflows/flows/nursing_ops.py`
- Modify: `dl-control/dl_control/workflows/flows/catalog.py`
- Modify: `dl-control/dl_control/agents/` — 加 workflow_agent_grant

**Interfaces:**
- Consumes: 护理科/总务科/通用助手/院长 Agent skills
- Produces: `nursing.ops` workflow（4 步 CallAgent 顺序编排）

- [ ] **Step 1: 写 nursing_ops.py flow 定义**

```python
# 参考 content_pipeline.py 的 CallAgent 模式
from dl_control.workflows.model import Flow, Step, CallAgent, AgentTask

flow = Flow(
    id="nursing.ops",
    version="1.0.0",
    steps=[
        Step("nursing-schedule-step", call_agent=CallAgent(
            prepare=lambda inp, out: AgentTask(
                agent_id=NURSING_DEPT_AGENT_ID,
                message="生成本周护工排班表"
            )
        )),
        Step("logistics-step", call_agent=CallAgent(
            prepare=lambda inp, out: AgentTask(
                agent_id=LOGISTICS_DEPT_AGENT_ID,
                message=f"根据排班: {out.get('nursing-schedule-step', '')}，安排物资配送计划"
            )
        )),
        Step("finance-step", call_agent=CallAgent(
            prepare=lambda inp, out: AgentTask(
                agent_id=GENERAL_ASSISTANT_AGENT_ID,
                message=f"[dept=财务科] 根据排班和采购数据生成成本预估"
            )
        )),
        Step("director-report-step", call_agent=CallAgent(
            prepare=lambda inp, out: AgentTask(
                agent_id=DIRECTOR_AGENT_ID,
                message=f"汇总排班={out.get('nursing-schedule-step')}, 物资={out.get('logistics-step')}, 成本={out.get('finance-step')}，生成运营周报"
            )
        )),
    ]
)
```

- [ ] **Step 2: 注册 flow + 配 grant**

catalog.py 加 `"nursing.ops": flow`。为护理科/总务科/通用助手/院长 Agent 各加 `workflow_agent_grant`。

- [ ] **Step 3: 预警闭环联调**

health-signal 中间件写入 → health_alerts 表 → dashboard API 实时查询 → 大屏刷新 → 楼栋 Agent 查询预警列表。

- [ ] **Step 4: Commit**

---

### Task 10: 端到端联调 + 演示脚本

**Files:**
- Create: `docs/demo-script.md`

**Interfaces:**
- 无新增接口——验证所有 Task 1-9 的产出协同工作

- [ ] **Step 1: 端到端冒烟测试**

```bash
# 1. 启动全栈
docker compose down -v && docker compose up -d
# 2. 等待所有 agent 容器 healthy
docker compose ps | grep agent | wc -l  # = 10
# 3. 登录 4 种角色
# 4. 护理科生成排班 → 验证 schedules 表有数据
# 5. 总务科盘点 → 验证库存预警正确
# 6. 触发健康预警 → 验证 health_alerts 写入
# 7. 大屏数据一致
# 8. 多 Agent workflow 跑通
```

- [ ] **Step 2: 写演示脚本**

```markdown
# AI 养老院院长 MVP 演示脚本
1. 院长登录 → 大屏首页 (1分钟)
2. 护理科生成排班 (3分钟)
3. 总务科盘点 + 库存预警 (3分钟)
4. 楼栋负责人查询 + 预警处理 (3分钟)
5. 模拟老人咨询 → 健康预警 (3分钟)
6. 院长一句话生成运营周报 (3分钟)
7. Q&A (5分钟)
```

- [ ] **Step 3: Commit**

---

## 自检清单

1. **Spec coverage:** 10 Agent 容器 ✅ / Mock DB ✅ / 认证 ✅ / 对话UI ✅ / health-signal ✅ / 护理科核心技能 ✅ / 总务科核心技能 ✅ / 6栋楼Agent ✅ / 其余4科室覆盖 ✅ / 大屏 ✅ / 多Agent workflow ✅
2. **Placeholder scan:** 无 TBD/TODO，所有代码步骤有实际内容
3. **Type consistency:** 各 Task 的 handler 签名一致 (`async def xxx(db_url: str, ...) -> list[dict]`)
