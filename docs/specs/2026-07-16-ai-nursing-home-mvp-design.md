# AI 养老院院长 — MVP 设计文档（v3）

> 日期：2026-07-17
> 客户：杭州市第三社会福利院
> 演示日期：2026 年 7 月底
> 目标：现场 Demo，推动签订正式合同
> 架构基础：复用 dato 多智能体系统

---

## 一、项目定位

### 一句话定位

面向养老院管理层的 AI 运营助手——让院长和科室负责人用自然语言驱动多 Agent 协作，自动完成排班、盘点、报表等日常运营工作。

### MVP 边界

| 范围 | 内容 |
|------|------|
| ✅ 做 | 网页端对话 + 数据分析大屏、10 个独立 Agent 容器、护理科&总务科核心场景（深度）、6 栋楼独立 Agent、其余 4 科室轻量覆盖、Mock 数据库（基于真实规则生成）、健康监测预警（管理员模拟老人提问触发） |
| ❌ 不做 | APP 端、真实系统对接（排班软件/ERP/财务系统需线下调研后确认）、安保机器人/送餐硬件、安防联动 |
| 🔮 二期 | 真实系统对接、安防 Agent（巡逻机器人联动）、送餐 Agent、老人端"小戴机器人"、26 栋全量 Agent 扩展 |

### 演示目标

"一句话生成全院下周排班表 → 总务自动同步物资配送计划 → 大屏实时刷新运营总览 → 管理员模拟老人咨询触发健康预警"

---

## 二、目标机构概况

**杭州市第三社会福利院**，杭州市民政局直属事业单位，坐落于上城区皋亭山风景区。

| 指标 | 数值 |
|------|------|
| 占地面积 | 169 亩 |
| 设计床位 | 1,752 张 |
| 居住楼栋 | 26 栋 |
| 员工总数 | ~350 人（护理人员 ~300 + 科室职能 ~50） |
| 内设科室 | 综合办、护理科、医务康复科、总务科、财务科、安全保卫科 |

### 组织架构关键信息

- **6 职能科室统筹 26 栋楼**，楼栋无独立科室编制。职能科室统一后台统筹，护工分片派驻各楼栋。
- **每栋楼一个总负责人，每层一个分管负责人**。护理片区直管楼栋。
- 护理科统一排班，**12 小时两班倒 + 做六休一**，区分自理区/半护全护/失智专区。
- 夜班每 2 小时全覆盖巡房，安全保卫科安防系统同步联动。

### 六科室 → MVP 场景映射

| 科室 | 核心程度 | MVP 场景 |
|------|:--------:|---------|
| **护理科** | 🔥 核心 | 自动生成护工排班、抓取护理工单、统计护理完成率 |
| **总务科** | 🔥 核心 | 自动盘点 26 栋耗材、生成采购申请、预警库存不足 |
| **综合办** | 覆盖 | 员工查询、文档生成（月报/通知） |
| **医务康复科** | 覆盖 | 老人健康档案查询、重点关注名单 |
| **财务科** | 覆盖 | 费用结算查询、参与协同报表 |
| **安全保卫科** | 覆盖 | 夜班巡房记录查询、告警记录 |

---

## 三、系统架构

### MVP Agent 容器清单：10 个

| # | Agent | 容器数 | 使用者 | 定位 |
|---|-------|:---:|------|------|
| 1 | **院长 Agent** | 1 | 院长 | 全局视角，报表生成，跨科室统筹 |
| 2 | **护理科 Agent** | 1 | 护理科全员 | 🔥 核心——自动排班+工单+完成率 |
| 3 | **总务科 Agent** | 1 | 总务科全员 | 🔥 核心——自动盘点+采购+库存预警 |
| 4 | **1 号楼 Agent** | 1 | 1 号楼楼栋+楼层负责人 | 本楼数据+预警处理 |
| 5 | **2 号楼 Agent** | 1 | 2 号楼楼栋+楼层负责人 | 本楼数据+预警处理 |
| 6 | **3 号楼 Agent** | 1 | 3 号楼楼栋+楼层负责人 | 本楼数据+预警处理 |
| 7 | **4 号楼 Agent** | 1 | 4 号楼楼栋+楼层负责人 | 本楼数据+预警处理 |
| 8 | **5 号楼 Agent** | 1 | 5 号楼楼栋+楼层负责人 | 本楼数据+预警处理 |
| 9 | **6 号楼 Agent** | 1 | 6 号楼楼栋+楼层负责人 | 本楼数据+预警处理 |
| 10 | **通用助手 Agent** | 1 | 综合办/医务康复科/财务科/安全保卫科 | 其余 4 科室统一入口，dept 标签路由 |

> 6 栋楼 Agent 配置完全相同（同一 YAML 模板，仅楼号不同）。楼层负责人通过所在楼栋 Agent 的 context 注入区分（`{building: "3号楼", floor: "2层"}`），不单独部署 Agent 容器。

### 整体架构图

```
┌──────────────────────────────────────────────────────────────┐
│                        用户层                                  │
│   ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐    │
│   │   院长    │  │ 楼栋负责人 │  │ 楼层负责人 │  │  科室人员  │    │
│   └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘    │
│        │              │            │              │           │
│        └──────────────┼────────────┼──────────────┘           │
│                       │            │                          │
│                ┌──────┴────────────┴──────┐                   │
│                │      Web UI 网页          │                   │
│                │  ┌──────────┐┌─────────┐ │                   │
│                │  │ 对话界面   ││ 大屏     │ │                   │
│                │  │(魔改OpenClaw)│(独立新增)│ │                   │
│                └──────────┬───────────────┘                   │
└───────────────────────────┼──────────────────────────────────┘
                            │
┌───────────────────────────┼──────────────────────────────────┐
│                    openclaw Agent 层 (10 容器)                  │
│                            │                                   │
│  ┌────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐          │
│  │ 院长    │ │ 护理科    │ │ 总务科    │ │ 通用助手  │          │
│  │ Agent   │ │ Agent    │ │ Agent    │ │ Agent    │          │
│  └───┬────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘          │
│      │           │            │             │                 │
│  ┌───┴────┐┌───┴────┐┌───┴────┐┌───┴────┐┌───┴────┐┌───┴──┐│
│  │1号楼   ││2号楼   ││3号楼   ││4号楼   ││5号楼   ││6号楼 ││
│  │Agent   ││Agent   ││Agent   ││Agent   ││Agent   ││Agent ││
│  └───┬────┘└───┬────┘└───┬────┘└───┬────┘└───┬────┘└───┬──┘│
│      │         │         │         │         │         │    │
│      └───────────┼───────────────────────────┘               │
│                  │                                           │
│         ┌────────┴─────────────┐                              │
│         │    养老院技能包        │                              │
│         │  nursing-schedule    │                              │
│         │  nursing-work-order  │                              │
│         │  logistics-inventory │                              │
│         │  resident-query      │                              │
│         │  staff-query         │                              │
│         │  activity-query      │                              │
│         │  finance-query       │                              │
│         │  alert-query         │                              │
│         │  report-generate     │                              │
│         └──────────────────────┘                              │
│                                                                │
│   ┌──────────────────────────┐                                │
│   │  health-signal (中间件)   │  ← 所有消息自动扫描健康信号       │
│   └──────────────────────────┘                                │
└───────────────────────────┬────────────────────────────────────┘
                            │
┌───────────────────────────┼────────────────────────────────────┐
│                    数据 & 知识层                                 │
│                            │                                    │
│   ┌────────────┐  ┌───────┴───────┐  ┌──────────────┐         │
│   │  Mock DB   │  │   dl-gbrain   │  │  dl-cognee   │         │
│   │ (Postgres) │  │ (结构化知识)   │  │ (非结构化RAG) │         │
│   └────────────┘  └───────────────┘  └──────────────┘         │
│                                                                   │
│   ┌──────────────────────────────────────────────────┐          │
│   │              dl-llm-proxy (本地模型优先)           │          │
│   └──────────────────────────────────────────────────┘          │
└──────────────────────────────────────────────────────────────────┘
```

### dato 模块复用矩阵

| 模块 | dato 中的角色 | 养老院 MVP 中的角色 | 改动 |
|------|-------------|-------------------|------|
| **dl-control** | Agent 管理 + 工作流引擎 + 内部 API | 管理 10 Agent + 多 Agent 协作工作流 | 新增养老院 flow 定义 |
| **dl-gbrain** | 结构化知识平台（Schema+Lint） | 护理规范、管理制度等结构化知识 | 新增 brain-repo/nursing-home/ |
| **dl-cognee** | 非结构化 RAG（向量+精排） | 老人日常记录等非结构化检索 | 保持不变 |
| **dl-llm-proxy** | LLM 代理层 | 统一 LLM 入口 | 保持不变 |
| **openclaw** | Agent 运行时 + 技能系统 | Agent 运行时 + 养老院技能包 | 技能替换 |
| **infra** | Docker Compose + Caddy + Postgres | 去飞书/OTA，配 10 Agent 容器 + 路由 | 微调配网 |
| **dl_shared** | 共享库（限流/密钥/校验） | 直接复用 | 不改 |

---

## 四、用户与权限模型

### Agent 与使用者映射

| Agent | 容器 | 使用者 | 数据范围 | 核心能力 |
|-------|:---:|------|---------|---------|
| **院长 Agent** | 独立 | 院长 | 全院全量 + 全局预警 | 运营报表、跨科室统筹 |
| **护理科 Agent** | 独立 | 护理科全员 | 全院护理数据 | 🔥 排班生成、工单抓取、完成率统计 |
| **总务科 Agent** | 独立 | 总务科全员 | 全院物资数据 | 🔥 耗材盘点、采购申请、库存预警 |
| **1-6 号楼 Agent** | 独立 × 6 | 各楼栋负责人 + 楼层负责人 | 各自楼栋全量 + 公共信息 | 本楼查询、预警处理，配置完全相同仅楼号不同 |
| **通用助手 Agent** | 独立 | 综合办/医务康复科/财务科/安全保卫科 | 按 dept 标签 | 4 科室统一入口，dept 自动路由技能 |

> 楼层负责人不单独部署 Agent——通过所在楼栋 Agent 的登录 context 注入 `{role: "floor", building: "3号楼", floor: "2层"}` 区分数据范围。

### 登录 + 身份识别

所有角色统一**用户名+密码**登录（复用 dl-control auth：argon2 + session + Redis）。登录后注入 context：

```
院长:         {role: "director"}
护理科员工:    {role: "nursing_dept", dept: "护理科"}
总务科员工:    {role: "logistics_dept", dept: "总务科"}
3号楼楼栋负责人:{role: "building", building: "3号楼"}
3号楼2层负责人:{role: "floor", building: "3号楼", floor: "2层"}
财务科员工:    {role: "general", dept: "财务科"}
```

### 会话隔离设计

Web 层注入用户上下文 + 无状态查询：

```
用户 李卫东 发来消息 → Web 层查出身份
  → 路由到对应 Agent 容器
  → 拼装 prompt: "[会话:s_7a3f | 用户:李卫东 | role:building | 管辖:3号楼] 张三今天在哪层？"
  → Agent 调用 skill，附加 building='3号楼'
  → 返回 → Web 层按 session ID 路由回李卫东
```

每个 Agent 容器独立运行，不同 Agent 之间天然隔离。同 Agent 内多人并发时排队处理。MVP 演示场景下（1-2 人同时操作）完全够用。

---

## 五、Agent 协作模型

### 多 Agent 顺序协作：护理 → 总务 → 财务 → 院长

利用 dato 工作流引擎的 `CallAgent`，每个步骤指向不同 Agent 容器：

```python
# nursing_ops flow
Step 1: call_agent → 护理科 Agent
  "根据护理科排班规则（12h两班倒、做六休一、四区分区），
   生成本周护工排班表"

Step 2: call_agent → 总务科 Agent
  "根据排班结果 {out['step1']}，按护工班次错峰安排耗材配送，
   检查库存安全线，生成采购申请"

Step 3: call_agent → 通用助手 Agent (dept=财务科)  [注：财务科暂无独立 Agent，暂由通用助手替代]
  "读取排班 {out['step1']} 和耗材采购 {out['step2']}，
   生成本周运营成本预估"

Step 4: call_agent → 院长 Agent
  "汇总: 排班={out['step1']} + 物资={out['step2']} + 成本={out['step3']}，
   生成本周运营总览报表"
```

### 已有能力，无需新设计

- `CallAgent.prepare()` 独立指定每步的目标 Agent UUID（不同 Agent 各自独立容器，零冲突）
- `outputs` dict 累积所有前置步骤结果，Step N 可读 Step 1..N-1 的全部输出
- 内置指数退避重试 + side effect ledger 幂等

### MVP 不做的

- 并行 fan-out（护理和总务同时执行 → 汇合）：引擎暂不支持，MVP 不需要
- 事件驱动跨 Agent 触发：二期可选

---

## 六、Mock 数据库设计

### 数据基于真实规则生成

| 真实信息 | Mock 数据体现 |
|---------|-------------|
| 1,752 床位、26 栋楼 | residents 表 36 条采样（6 栋楼 × 每栋 6 人） |
| ~350 员工（护工300 + 科室50） | users 表 22 条（10 Agent × 相应人数） |
| 12h 两班倒 + 做六休一 | schedules 表按规则自动生成一周排班 |
| 自理/半护/全护/失智四区 | residents 表 care_level 字段区分 |
| 夜班每 2h 巡房 | health_alerts 表含巡房异常记录 |

### 用户 seeds

| Agent | 用户数 | 用户名示例 |
|-------|:---:|------|
| 院长 Agent | 1 | `wang_jianguo` |
| 护理科 Agent | 3 | `nurse_zhang`, `nurse_li`, `nurse_wang` |
| 总务科 Agent | 2 | `logi_chen`, `logi_zhao` |
| 1-6 号楼 Agent | 每栋 2 人（楼栋负责人 + 楼层负责人） | `b1_liu`, `b1f1_wang`, `b2_zhang`, `b2f1_chen`, `b3_li_weidong`, `b3f2_zhao_xiaoming`, `b4_wu`, `b4f1_sun`, `b5_liu_zhuren`, `b5f2_qian_xiaohong`, `b6_zhou`, `b6f1_huang` |
| 通用助手 Agent | 4 | `admin_liu` (综合办), `med_feng` (医务), `fin_sun` (财务), `sec_zhou` (安全) |
| **合计** | **22** | |

### 核心表结构（同 v2，略作调整）

```sql
-- 用户表（增加 role 枚举值）
CREATE TABLE users (
    user_id    TEXT PRIMARY KEY,
    username   TEXT UNIQUE NOT NULL,
    name       TEXT NOT NULL,
    role       TEXT NOT NULL CHECK (role IN ('director','nursing_dept','logistics_dept','building','floor','general')),
    password   TEXT NOT NULL,
    dept       TEXT,              -- 科室标签
    building   TEXT,
    floor      TEXT
);

-- 老人档案
CREATE TABLE residents (
    id         TEXT PRIMARY KEY, name TEXT, building TEXT, floor TEXT, room TEXT,
    age        INTEGER, diagnosis TEXT, care_level TEXT, notes TEXT
);

-- 排班表（核心 🔥）
CREATE TABLE schedules (
    id         SERIAL PRIMARY KEY, staff_name TEXT, date DATE,
    shift      TEXT NOT NULL,    -- 白班(7-19)/夜班(19-7)
    building   TEXT, floor TEXT, zone TEXT, task_note TEXT
);

-- 护理工单（核心 🔥）
CREATE TABLE nursing_work_orders (
    id          SERIAL PRIMARY KEY, resident_id TEXT, date DATE DEFAULT CURRENT_DATE,
    type        TEXT,            -- 洗漱/助餐/康复/用药/巡房
    completed   BOOLEAN DEFAULT FALSE, staff_name TEXT, note TEXT
);

-- 库存表（核心 🔥）
CREATE TABLE inventory (
    id SERIAL PRIMARY KEY, item_name TEXT, category TEXT,
    quantity INTEGER, unit TEXT, safety_stock INTEGER, updated_at TIMESTAMP DEFAULT NOW()
);

-- 健康预警
CREATE TABLE health_alerts (
    id SERIAL PRIMARY KEY, resident_id TEXT, content TEXT,
    category TEXT, severity TEXT DEFAULT 'info',
    created_at TIMESTAMP DEFAULT NOW(), handled BOOLEAN DEFAULT FALSE
);

-- 辅助表
CREATE TABLE meals (id SERIAL PRIMARY KEY, date DATE, meal_type TEXT, menu TEXT);
CREATE TABLE activities (id SERIAL PRIMARY KEY, title TEXT, date DATE, time TEXT, location TEXT);
CREATE TABLE finances (id SERIAL PRIMARY KEY, resident_id TEXT, month TEXT, amount DECIMAL, paid BOOLEAN);
```

### 种子数据规模

| 表 | MVP 条数 |
|---|:---:|
| users | 22 |
| residents | 36 (6栋 × 6人) |
| schedules | ~280 (20人 × 14天) |
| nursing_work_orders | ~100 |
| inventory | 15 |
| health_alerts | 10 |
| meals | 21 |

---

## 七、技能分配矩阵

每个 Agent 容器通过 `skill_list` 配置可用技能子集：

| 技能 | 院长 | 护理科 | 总务科 | 1-6号楼 | 通用助手 |
|------|:---:|:---:|:---:|:---:|:---:|
| `nursing-schedule` | — | ✅ | — | — | — |
| `nursing-work-order` | ✅ | ✅ | — | ✅ | 按 dept |
| `logistics-inventory` | — | — | ✅ | — | — |
| `logistics-query` | ✅ | ✅ | ✅ | ✅ | 按 dept |
| `meal-query` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `staff-query` | ✅ | ✅ | ✅ | ✅ | 按 dept |
| `resident-query` | ✅ | ✅ | — | ✅ | 按 dept |
| `activity-query` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `finance-query` | ✅ | — | — | — | 按 dept |
| `alert-query` | ✅ | ✅ | — | ✅ | 按 dept |
| `report-generate` | ✅ | — | — | — | — |

> "按 dept" = 根据 dept 标签自动路由：`护理科` → `nursing-work-order`，`总务科` → `logistics-inventory`，`财务科` → `finance-query` 依此类推。跨科室查询允许但不做默认路由。

---

## 八、核心技能设计

### 护理科核心技能 🔥

| 技能 | 做什么 | 工作方式 |
|------|--------|---------|
| `nursing-schedule` | 自动生成护工排班表 | Agent 根据排班规则（12h 两班倒、做六休一、四区人力配比）通过 LLM 编排，handler 写入 schedules 表 |
| `nursing-work-order` | 抓取各楼栋护理工单、统计完成率 | handler 查询 `nursing_work_orders`，Agent 组织统计报表 |

### 总务科核心技能 🔥

| 技能 | 做什么 | 工作方式 |
|------|--------|---------|
| `logistics-inventory` | 自动盘点耗材、生成采购申请 | handler 查询 `inventory` + 安全线比对，Agent 生成采购建议 |
| `logistics-query` | 物资/餐饮状态查询 | handler 查询 inventory + meals 表 |

### 院长专属技能

| 技能 | 做什么 | 工作方式 |
|------|--------|---------|
| `report-generate` | 调用多 Agent 输出，LLM 合成周报/月报 | 通过工作流引擎拉取护理+总务+财务数据，LLM 汇总 |

### 技能工作模式示例

```
护理科员工: "生成本周 3 号楼护工排班表"
  → 护理科 Agent 调用 nursing-schedule skill
    → Agent 用 LLM 根据排班规则生成排班
    → handler.py 写入 schedules 表 (INSERT ~20 records)
      → 返回: "已生成 7/21-7/27 排班，白班 12 人，夜班 8 人，覆盖自理区/半护区"
```

---

## 九、Web UI 设计

### 页面来源

| 页面 | 来源 | 方案 |
|------|------|------|
| **登录页** | 魔改 `dl-control/templates/login.html` | 换 logo + 标题，复用用户名密码登录 |
| **对话界面** | 魔改 OpenClaw 聊天 UI | 按 Agent 类型显示不同侧边栏 |
| **整体框架** | 魔改 `dl-control/templates/base.html` | 导航栏简化，去飞书/OTA 入口 |
| **数据分析大屏** | 独立集成 | 开源数据大屏项目（备选：ECharts 静态页） |

### 页面结构

```
登录页 ──→ 对话界面（根据角色路由到对应 Agent 容器）
              │
              └──→ 大屏入口 → 数据分析大屏
```

### 各 Agent 对话界面差异

| | 院长 | 护理科 | 总务科 | 1-6号楼 | 通用助手 |
|---|------|-------|-------|----------|------|
| 侧边栏 | 全院运营概览 | 排班+工单概览 | 库存+采购概览 | 本楼栋概览 | 本科室概览 |
| 预警可见 | 全部 | 护理相关 | 库存预警 | 本楼栋 | 按 dept |
| 大屏入口 | ✅ | ✅ | ✅ | ✅ | ✅ |

---

## 十、数据流

### 管理员查询

```
用户 → Web UI → 对应 Agent 容器 → skill handler → Mock DB → Agent 组织回答 → Web UI
```

### Agent 产出（护理排班为例）

```
护理科员工: "生成本周排班" → 护理科 Agent
  → nursing-schedule skill: LLM 生成排班 + handler 写入 schedules 表
    → "已生成本周排班，白班12人，夜班8人"
    → 大屏同步刷新
```

### 多 Agent 协作

```
管理员: "跑一次本周运营计划" → workflow engine

  Step 1: 护理科 Agent 生成排班 → output →
  Step 2: 总务科 Agent 读取排班 → 生成配送+采购 → output →
  Step 3: 通用助手 Agent (dept=财务科, 暂替代) 读取排班+采购 → 成本预估 → output →
  Step 4: 院长 Agent 读取全部 outputs → 运营汇总报表 → 返回
```

### 健康预警流（管理员模拟老人提问）

```
管理员: "张建国咨询：感冒了吃什么药好？"
  → Agent 正常回答 (LLM)
  → health-signal 中间件检测: "感冒"+"张建国"
    → 匹配 resident 表 → INSERT INTO health_alerts
      → 大屏预警刷新 + 对应楼栋 Agent 可见

演示话术: "未来老人通过小戴机器人语音提问，效果就是这样的——"
```

---

## 十一、MVP 两周排期

> 7 月 21 日 ～ 7 月 31 日（10 天，7/26 周日休息）

### 第一阶段：基础设施（7/21–7/25）

| 日 | 模块 | 内容 | 产出 | 状态 |
|---|------|------|------|:--:|
| **D1** (7/21) | 项目脚手架 | fork dato，剥离飞书/OTA/内容管线，保留核心服务 | 可启动空壳 | ⬜ 待进行 |
| **D2** (7/22) | Mock DB + 10 Agent | 建表 + 种子数据 + 配置 10 Agent YAML seed | 表+数据+容器就绪 | ⬜ 待进行 |
| **D3** (7/23) | 用户认证 | 用户名密码登录，注入 context（role/building/floor/dept） | 10 Agent 可登录 | ⬜ 待进行 |
| **D4** (7/24) | 对话界面 | 魔改 OpenClaw UI + base.html，按 Agent 差异化侧边栏 | 对话可用 | ⬜ 待进行 |
| **D5** (7/25) | health-signal 中间件 | 后端消息扫描 + LLM 分类，写入 health_alerts | 预警检测可用 | ⬜ 待进行 |

### 第二阶段：业务功能（7/27–7/31）

| 日 | 模块 | 内容 | 产出 | 状态 |
|---|------|------|------|:--:|
| **D6** (7/27) | 护理科技能 | nursing-schedule + nursing-work-order | 排班生成+工单统计 | ⬜ 待进行 |
| **D7** (7/28) | 总务科技能 + 其余科室 | logistics-inventory + logistics-query + 其余 query | 盘点+采购+全科室覆盖 | ⬜ 待进行 |
| **D8** (7/29) | 数据分析大屏 | 集成开源大屏，接入 mock 数据 | 大屏可用 | ⬜ 待进行 |
| **D9** (7/30) | 多 Agent 协作 workflow | 护理→总务→财务→院长 flow + 预警闭环 | 完整协作链路 | ⬜ 待进行 |
| **D10** (7/31) | 联调+演示脚本 | 端到端测试、修 bug、准备演示流程 | 可演示版本 | ⬜ 待进行 |

### 风险与缓释

| 风险 | 缓释 |
|------|------|
| OpenClaw 前端魔改比预期复杂 | 只改必要部分，不做美化 |
| 开源大屏集成不顺利 | 降级方案：纯 ECharts 静态 HTML |
| LLM 返回不稳定 | 关键业务逻辑走 handler.py，LLM 只做编排和文本 |
| 排班规则实现复杂 | MVP 按基本规则生成排班（12h两班倒+做六休一），复杂场景（失智专区加密排班、节假日调休）留二期 |
