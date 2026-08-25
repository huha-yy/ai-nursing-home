# dato 项目决策日志

本项目使用 CLAUDE.md + JOURNAL.md 做跨会话记忆管理。JOURNAL.md 记录关键决策、修复原因、待办事项，新会话通过它快速恢复上下文。

---

## 📋 目录索引

| 日期 | 主题 | 状态 |
|------|------|:----:|
| [06-23~25 早期基建](#anchor-early) | 镜像/技能/凭证/cognee 搭建 | ✅ |
| [06-26 Agent 架构](#anchor-agents) | Agent 从 2→4 个：知识库+通用助手 | ✅ |
| [06-26 管线修复](#anchor-pipeline) | 重复推送/图片丢失/凭证规范 | ✅ |
| [06-26 关键认知](#anchor-workflow) | workflow 技能归属澄清 | ✅ |
| [06-29 多品牌改造](#anchor-multibrand) | 品牌配置 YAML 化，prompt 去硬编码 | ✅ |
| [06-30 全面修复](#anchor-0630) | 排版/图片/品牌资料/identity 规范 | ✅ |
| [06-30 Token hash/Webhook](#anchor-fixes) | 代码层修复 + 通知链路设计 | ✅ |
| [07-01 Phase 2 完成](#anchor-phase2) | 配对/语音/OCR/PPT 四大功能 | ✅ |
| [07-07 运营 Agent 上线](#anchor-0707) | 家乐+宇婷专属Agent + 6 Agent 时代 | ✅ |
| 07-07 cognee Phase 2 | bge-m3 + reranker 升级完成 | ✅ |
| [07-09 文件入库](#anchor-file-parse) | 文件上传→自动解析→GBrain 入库 | ✅ |
| [07-09 Schema+Lint](#anchor-schema-lint) | Schema 规则 + lint CLI + 集成到入库 | ✅ |
| [下一阶段清单](#anchor-next-phase) | 内容填充 → 同步脚本 | 📋 |
| 07-09 下一阶段 | 🅰 内容填充 🔵 同步脚本 | 📋 待实施 |
| [07-08 GBrain 引入计划](#anchor-gbrain) | GBrain 作为一线人员知识平台，6 周实施计划 | 📋 待实施 |
| [07-28 养老院第二家客户](#anchor-nursing-home2) | 杭州市社会福利中心调研问卷（88 问） | 📋 |
| [08-13 nursing-erp OCR 完成](#anchor-ocr) | 菜单OCR+点餐OCR+LLM纠错+跨页+断电恢复 | ✅ |
| [08-20 OpenClaw 升级评估](#anchor-openclaw-upgrade) | 4.8→7.1，handler 未修复，验证清单 | 📋 |
| [08-21 公网访问架构](#anchor-public-access) | admin/chat.eldcare.cn + frpc 隧道（08-15 上线） | ✅ |
| [08-21 ERP API 认证加固](#anchor-erp-api-auth) | /api/ 匿名裸奔 → API key + session 双认证 | ✅ |
| [08-21 DeepSeek→Moonshot 切换](#anchor-moonshot) | 全栈换 kimi-k2.6 + openclaw 鉴权三坑 | ✅ |
| [待办汇总](#anchor-todo) | 🔲 未来工作 | 🔲 |

---

## <a id="anchor-early"></a>早期基建（06-23 ~ 06-25）

### 镜像与技能
- OpenClaw Dockerfile 加中国镜像加速，15 个自定义技能
- 飞书扩展补丁、`internal_routes.py` 11 个管理端点、Caddy 配置
- 引入 humanizer/self-improving/web-content-fetcher/openai-whisper/nano-pdf
- `admin-mgmt` skill 做 HTTP shim（Python handler 不注册为 callable tool）
- **踩坑：** `make build` 慢（torch 2GB），临时用 `docker cp`；飞书插件 TS 源码必须完整重建（npm postinstall），镜像 14.3GB

### 凭证与图片
- entrypoint 映射规范（`_DN*` → 无后缀）、send_image.py 换 Drive API→im/v1/images
- Agent Manager 发图修复：注入 `[FeishuChatId]` 和 `[FeishuSenderOpenId]` 到 prompt

### cognee 升级
- 模型：`bge-small-en-v1.5` → `paraphrase-multilingual-MiniLM-L12-v2`（384维，中英文）
- **踩坑：** `uv run` 写入 uv.lock 失败（read_only）→ 直接调 `.venv/bin/uvicorn`；`refs/main` 尾随换行符 → `printf "%s" hash`；hf-mirror.com 403 → 镜像内预嵌入 + `HF_HUB_OFFLINE=1`

### 项目踩坑合集
1. IDENTITY.md bind-mount 路径：Agent Manager 的 WORKSPACE 是 `/home/node/.openclaw/workspace/` 而非 `/home/node/.openclaw/`
2. cognee admin ingest 路径：`/v1/admin/ingest`（带 `/v1` 前缀）
3. 内容运营容器缺 DL_INTERNAL_TOKEN 环境变量：在 `/app/config/.env` 但未 export，需 `docker exec -e` 传入
4. easyocr 模型下载被内网阻断 → 降级到 LLM Vision API
5. `/opt/openclaw/` 目录 root 所有，`docker cp` 不需要 root（node 用户）
6. insert_images.py 品牌资产路径硬编码 → YAML 路径推导+fallback
7. Agent Manager 搜错品牌（戴恩→头盔）→ IDENTITY.md 禁止搜品牌资料

---

## <a id="anchor-agents"></a>06-26 Agent 架构：知识库 + 通用助手（Agent 2→4）

### 创建的知识库 Agent（4 个 seed 文件）
- `knowledge-base/agent.yaml`：`skill_list=[cognee, web-content-fetcher, workflow, self-improving]`
- `workspace/SOUL.md` + `IDENTITY.md` + `TOOLS.md`：知识管家身份、cognee 调用方式
- 内容管线 fact-research/content-strategy 步骤追加 cognee.search("company_knowledge")

### 创建的通用助手 Agent（4 个 seed 文件）
- `general-assistant/agent.yaml`：`skill_list=[cognee, web-content-fetcher, workflow, self-improving]`
- 定位：第一个人工台，能查能记的百事通+引路人，复杂请求路由到对应 Agent
- 路由表：写文章→内容运营、管 Agent→Agent Manager、存知识→知识库

### 架构决策
| 决策 | 选择 | 原因 |
|------|------|------|
| Agent 数量 | 4 个 | 职责分离 |
| account_id 命名 | `dn1`~`dn4`（后改为 `agent1`~`agent4`） | 后续统一规范 |
| 飞书 Bot | 1:1 对应，每个 Agent 独立机器人 | 直接对话，无路由逻辑 |
| 知识存储 | cognee 共享库 `company_knowledge` | 知识库写，内容运营读 |
| 内容运营 Bot 权限 | im:message + im:resource + docx:document + drive:drive | 聊天+创建飞书文档 |

### dn4 飞书 Bot 注册
- 4 个 Agent bot 全部就绪后改为 agent1~agent4 统一命名

---

## <a id="anchor-pipeline"></a>06-26 管线修复：重复推送 + 图片丢失 + 凭证规范

### 问题 1：推送 4 篇（2 有图 + 2 无图）
**根因三叠加：**
1. feishu-publisher SKILL.md 第 4 节重跑 article-composer
2. humanizer 没保留图片标记 `![](file://...)`
3. `push_to_feishu.py` `--platform` 默认 `all`

**修复：** 删 publisher 第 4 节、humanizer 加保留图片标记、`--platform` 改为 `required=True`

### 问题 2：公众号无图（仅封面）
**根因：** insert_images.py 关键词匹配与章节标题不匹配，静默跳过
**修复：** 扩宽关键词 + 中文模糊兜底 + 顺序插入兜底

### 问题 3：小红书无图
**根因：** `_prepare_images` 没要求 LLM 做小红书 plan
**修复：** 明确要求双平台，传 xhs-content 供参考

### 问题 4：文章含"封面图建议"表格
**修复：** output 指令去掉"封面图建议"字段

### 凭证规范修复
- 清除无后缀 FEISHU_APP_ID 污染、PEXELS_API_KEY 推广到所有 Agent
- 残留 Agent 容器 `b297d2e8` 目录清理

### 验证
2 次管线运行验证全部 9 项问题修复确认 ✅

---

## <a id="anchor-workflow"></a>06-26 关键认知澄清：workflow 技能该给谁

**误区：** 以为内容运营需要 `workflow` 技能才能参与管线执行。
**纠正：** 启动和执行是两层——Agent Manager 调 `start_workflow()`（需要 workflow 技能），dato-control P13b 引擎派活，内容运营只执行步骤（不需要 workflow）。

**三条启动管线途径：**
- 管理后台手动触发 ✅
- 定时任务调度器 ✅
- Agent Manager 飞书对话（需给 dn1 加 workflow 技能）

---

## <a id="anchor-multibrand"></a>06-29/30 多品牌通用化改造（戴恩 + 永和）

### 方案
品牌配置从代码内联改为纯 YAML 数据文件，加品牌 YAML 不改 Python。

| 配置位置 | 存储内容 |
|---------|---------|
| `brand_configs/<slug>.yaml` | 品牌文案（使命/赛道/标签/合规） |
| `configs/<slug>/brand_images.yaml` | 品牌图片资产映射 |
| `configs/<slug>/brand_guidelines.md` | 品牌口径 |
| `configs/<slug>/brand_assets/` | logo/产品图 |

### 改动关键文件
`content_pipeline.py`（内联→`brand_config.py` YAML 加载）、`insert_images.py`（品牌资产动态构造）、entrypoint（BRAND env overlay）、3 个 SKILL.md 去硬编码、agent-manager IDENTITY.md 禁止自写

### 加新品牌步骤（零 Python 代码）
```bash
cp brand_configs/_template.yaml brand_configs/<slug>.yaml
cp configs/_template/brand_images.yaml configs/<slug>/brand_images.yaml
# 创建 brand_guidelines.md + brand_assets/ + company_keywords.yaml
```

### 当前品牌
| 品牌 | Slug | 状态 |
|------|------|------|
| 戴恩医疗科技 | `daien` | ✅ 完整 |
| 永和大健康 | `yonghe` | ✅ 文案齐全，产品图待客户提供 |

---

## <a id="anchor-0630"></a>06-30 内容管线全面修复 + 品牌配置传递修复

### 永和管线问题
- "配图建议表"等元数据出现在正文 → Output Format 从 JSON 改纯 markdown
- logo/产品图与摘要前连续堆放 → logo 插入位置移到文章最开头
- 没有永和 logo → brand_images.yaml 已就绪，等产品图
- BRAND 环境变量误导 → `_resolve_brand()` 4 层兜底（CLI > pipeline_context.json > BRAND env > daien）

### 其他修复
- Pexels 图片缩小到 600px、中英文双搜索、评分逻辑优化
- Agent 返回空内容 → 追加"必须以 text 形式包含完整执行结果摘要"
- Agent Manager 禁止汇报中间进度、只用自己的 Bot 回复、品牌识别规则
- 所有品牌资料写入 `company_knowledge` 库

### 改动文件
`insert_images.py`、`content_pipeline.py`、`entrypoint-wrapper.sh`（移除 brand_assets overlay）、3 个 SKILL.md、`run_image_pipeline.py`、`pexels_search.py`、agent-manager IDENTITY.md

---

## <a id="anchor-fixes"></a>06-30 Token hash 不同步 + 通知链路设计

### Token hash 不同步
**根因：** `restart_agent()` carry forward 旧 `.env` 的 `DL_INTERNAL_TOKEN`，但从未更新 DB 的 `internal_token_hash`。
**修复：** `service.py` 新增 `_env_dl_internal_token_hash()`，liveness 通过后自动重算 hash → UPDATE DB。
**覆盖：** 正常重启、手动改 token、旧 Agent 首次 restart、容器启动失败（不写入）。

### 管线结果通知链路
**方案：** 飞书群 webhook + `--no-webhook` 参数区分场景。

| 场景 | no_webhook | 行为 |
|------|-----------|------|
| Agent Manager 对话启动 | ✅ true | 直接回复用户，不发群通知 |
| 管理后台/定时任务 | ❌ 没传 | 发群卡片通知 |

---

## <a id="anchor-phase2"></a>07-01 Phase 2 四大功能完成

### 实施结果
| 功能 | 状态 | 关键点 |
|------|:----:|--------|
| 配对验证 | ✅ | 模板 `admin_only` 条件判断；feishu-bot.ts 中文配对提示；dn2/dn3/dn4 改 pairing，dn1 保持 open |
| 语音转写 | ✅ | `transcribe_audio.py` + feishu-bot.ts audio 分支；whisper 预装 |
| 图片 OCR | ✅ | `vision-ocr` skill（easyocr→LLM Vision 双轨）；图片消息自动 OCR 注入 |
| PPT 生成 | ✅ | `ppt-generator` skill；3 种配色方案 |

**新增 10 文件：** `transcribe_audio.py`、`download_image.py`、`vision-ocr/`（3个）、`ppt-generator/`（3个）
**修改 4 文件：** openclaw.json.j2、feishu-bot.ts、skill_catalog.py、JOURNAL.md

---

## <a id="anchor-0707"></a>07-07 运营 Agent 上线 + 全面修复 + ECC 采纳

### 背景
为戴恩公司提升内部效率，上线 2 个专属运营 Agent 给家乐和宇婷使用。同时修复 dn→agent 重命名遗留问题、采纳 everything-claude-code-zh 提升 Claude Code 开发效率。

### 运营 Agent 创建（8 文件）
| 文件 | 说明 |
|------|------|
| `operations-a/agent.yaml` | 运营助手-家乐 seed：`account_id=agent5`，10 个技能 |
| `operations-a/workspace/SOUL.md` + `IDENTITY.md` | 专属运营助手定位：主动学习、偏好记录、能做直接做 |
| `operations-b/agent.yaml` | 运营助手-宇婷 seed：`account_id=agent6` |
| `operations-b/workspace/SOUL.md` + `IDENTITY.md` | 同上，Owner=宇婷 |
| `memory/ecc-zh-adoption-plan.md` | 42 个文件采纳清单 |

### 修复
| 问题 | 根因 | 修复 |
|------|------|------|
| 4 Agent 显示"已移除该源" | docker-compose 未挂载 precreated_agents/ | 加 volume，重建 dl-control |
| 4 Agent accounts 不匹配 | dn→agent 只改 bindings，漏 accounts key | 修正 openclaw.json + 重启 |
| 运营 Agent 缺 API Key | 新 Agent 默认无 PEXELS/TAVILY/XIAOMI_MIMO | 从内容运营复制 |
| Session TTL | 24h 不够长 | `.env`：86400→604800（7天） |

### 当前 6 个 Agent
| Agent | UUID | Bot | 状态 |
|-------|------|-----|:----:|
| Agent Manager | `748ffcbc` | ✅ agent1 | ✅ |
| 内容运营 | `7c90fc88` | ✅ agent2 | ✅ |
| 知识库 | `cc1acc65` | ✅ agent3 | ✅ |
| 通用助手 | `eacdbc0e` | ✅ agent4 | ✅ |
| 运营助手-家乐 | `ecf605c0` | ✅ agent5 | ✅ |
| 运营助手-宇婷 | `a1320e77` | ✅ agent6 | ✅ |

### 当前配置
| 项 | 值 | 来源 |
|----|-----|------|
| 模型 | DeepSeek V4 Pro | openclaw.json |
| 上下文 | 128K tokens | openclaw.json |
| Session TTL | 7 天（604800s） | .env |
| 压缩 | safeguard | openclaw.json（具体行为取决于 OpenClaw 框架） |
| 长期记忆 | self-improving 三层（HOT≤100/WARM/COLD） | 代码库 |
| 记忆整理 | memory-core dreaming 凌晨3点 | openclaw.json |

### ECC 采纳
安装 everything-claude-code-zh 到 `.claude/`：7 Agents + 15 Skills + 16 Commands + 4 Rules。

---

## <a id="anchor-cognee-phase2"></a>07-07 cognee Phase 2 升级：bge-m3 + reranker

### 改动文件清单

| 文件名 | 操作 | 说明 |
|--------|------|------|
| `dl-cognee/pyproject.toml` | 修改 | fastembed → FlagEmbedding + torch |
| `dl-cognee/Dockerfile` | 修改 | onnxruntime → torch-cpu; 移除 model-cache COPY |
| `dl-cognee/.dockerignore` | 修改 | 注释更新 |
| `dl-cognee/dl_cognee/embedder.py` | 重写 | flagembed BGEM3FlagModel, 1024-dim |
| `dl-cognee/dl_cognee/settings.py` | 修改 | model 默认值 bge-m3; 加 reranker_url/top_k/enabled |
| `dl-cognee/dl_cognee/startup.py` | 修改 | 移除 model-cache 植入; 简化 warm_up |
| `dl-cognee/dl_cognee/main.py` | 修改 | docstring 更新 |
| `dl-cognee/dl_cognee/routes.py` | 修改 | search 端点加 reranker HTTP 调用 |
| `dl-cognee/dl_cognee/scripts/reembed.py` | **新建** | 存量 chunks 重嵌入脚本 |
| `dl-cognee/dl_cognee/uv.lock` | 更新 | 新依赖 resolved |
| `dl-cognee-reranker/` (6 个文件) | **新建** | 独立 reranker 微服务 |
| `dl-control/dl_control/migrations/0014_cognee_v2_migration.sql` | **新建** | 共享库 vector(384)→vector(1024) |
| `dl-control/dl_control/per_library_migrations/0002_cognee_iso_v2.sql` | **新建** | 隔离库 vector(384)→vector(1024) |
| `infra/docker-compose.yml` | 修改 | cognee_hf_models volume; init 容器; reranker 服务; dl-cognee 配置更新 |
| `Makefile` | 修改 | build target 加 dl-cognee-reranker |
| `CLAUDE.md` | 修改 | 架构图 + 设计决策表更新 |

### 架构变更

```
Phase 1 (before):                     Phase 2 (after):
                                       
Query ─→ fastembed (384) ─→ pgvector   Query ─→ bge-m3 (1024) ─→ pgvector (top-N)
   └──────────┬─ top-k                       └─────────────┬─ top-N (N=3×k)
              ↓                                             ↓
        [no reranker]                    dl-cognee-reranker ─→ rerank (top-k)
```

### 关键决策
- **模型不塞镜像** — bge-m3 ~2.2GB，通过 Docker named volume 外部挂载
- **Init 容器下载** — `dl-cognee-model-download` 一次性下载两个模型到 volume
- **Reranker 独立容器** — `dl-cognee-reranker`，与主服务解耦
- **全部重新嵌入** — 迁移 SQL TRUNCATE 旧数据，reembed.py 从 chunk_text 重建
- **CPU-only 推理** — `use_fp16=False`, `--index-url https://download.pytorch.org/whl/cpu`

---

| # | 事项 | 优先级 | 依赖 |
|---|------|--------|------|
| 1 | 验证戴恩管线在改造后仍正常运行 | 🔴 高 | ✅ 每日13:27定时执行正常（`brand: daien`）|
| 2 | 跟进嵌入式工程师文档助手（新建 tech-doc-generator skill） | 🔴 高 | 工程师提供产品参数 |
| 3 | 收集家乐和宇婷的使用反馈，迭代 SOUL.md | 🟡 中 | 她们开始使用 |
| 4 | 品牌图库系统（图片 ID 化，`manage_library.py` + `image_library.json`） | 🟡 中 | — |
| 5 | 文件上传→知识库自动解析（markitdown 解析 PDF/Docx/PPT） | 🟡 中 | — |
| 6 | 永和产品图（`smart_ring`）入库 | 🔵 低 | 客户提供照片 |
| 7 | ComfyUI 生图部署 | 🔵 低 | Pexels 已够用 |
| 8 | cognee Phase 2（bge-m3 + reranker） | 🔵 低 | ✅ 已实施，参见 [#cognee-phase2](#anchor-cognee-phase2) |


## <a id="anchor-gbrain"></a>07-08 GBrain 引入计划：一线人员知识平台

**背景：** 客户需求从"内容管线检索"升级为"面向一线人员的知识平台"，需要多用户查询、Lint 校验、高标准规格文档入库。cognee 纯向量搜索无法满足。

**决策：** 引入 GBrain 作为一线团队知识平台，cognee 保持不动继续服务内容管线。

**方案概要（详见 `GBrain一线知识平台实施计划.md` v4）：**
- 5 阶段实施：基础设施（第 1 周）→ 数据迁移（第 2 周）→ Schema+Lint（第 3 周）→ 管理后台+Agent 集成（第 4 周）→ 收尾（第 5 周）
- 架构演进：~~v2: Ollama bge-m3~~ → ~~v3: dl-cognee /v1/embed~~ → **v4: 复用 dl-cognee 嵌入 + reranker（GBrain 原生 recipe）**
- **零新模型下载** — 嵌入走 dl-cognee `POST /v1/embeddings`（OpenAI 兼容），reranker 走 dl-cognee-reranker `POST /v1/rerank`
- GBrain 使用 `llama-server` / `llama-server-reranker` recipe，原生支持，零适配
- 20 个新建文件，**7 个修改文件**（新增 dl-cognee-reranker/main.py 的 `/v1/rerank` 端点）
- **简化点：** 去掉 OAuth 多用户 ACL（团队 Basic Auth 共享账号）
- **MVP（2 周）：** 基础设施 + 数据迁移 + 管理后台 → 2-3 人试用

**待办更新：** 已将 `GBrain 引入` 标记为 📋 待实施。

---

## <a id="anchor-file-parse"></a>07-09 文件上传→自动解析→GBrain 入库

…

## <a id="anchor-schema-lint"></a>07-09 GBrain Schema+Lint 实施完成

**模型：** `deepseek-reasoner`（Claude Code）

**执行内容：**

**1. gbrain-mcp handler 新增 put_page 写入功能**
- `handler.py` 新增 `put_page(slug, content)`，调 GBrain MCP `put_page` 工具
- 验证：写入测试页后搜索命中（0.937 分）✅

**2. 新建 parse_and_store.py 核心脚本**
- 流程：飞书文件下载 → markitdown 解析 → LLM(DeepSeek)分类 → GBrain put_page
- 分类逻辑：品牌(daien/yonghe/common) + 目录，不匹配时允许新建

**3. feishu-bot.ts 新增 file 消息处理**
- 复用 audio/image 的 try-catch + execSync 模式
- 注入 `[文件已入库: ...]` 到 agent 上下文

**4. 知识库 Agent（agent3）全面升级**
- skill_list 追加 gbrain-mcp、skills.yaml 重新生成
- GBRAIN_API_KEY 注入、workspace 三文件更新
- 容器重建（dato-openclaw:2026.4.8 新镜像）

**5. Dockerfile 更新**
- gbrain-mcp COPY + chown 行，feishu-bot.ts patch 编译

**关键记忆：** [[soul-identity-priority]]（SOUL.md 权重大于 IDENTITY.md）

---

## <a id="anchor-schema-lint"></a>07-09 GBrain Schema+Lint 实施完成

**模型：** `deepseek-reasoner`（Claude Code）

**执行内容：** GBrain 知识库 Schema+Lint 完整实施方案 —— 定义 schema 规则、创建 lint CLI 脚本、集成到入库流程、全量修复存量文件。

**改动文件（3 个）：**

| 操作 | 文件 | 说明 |
|------|------|------|
| 修改 | `openclaw/scripts/schema_rules.yaml` | 7 种知识类型，移除 `tags` 从必填→可选（匹配实际数据） |
| 新建 | `openclaw/scripts/lint_frontmatter.py` | CLI 校验器：支持 `--path` 单文件/目录、`--fix` 自动补全、退出码 0/1/2 |
| 修改 | `openclaw/scripts/parse_and_store.py` | 写入前 lint 校验，必填缺字段拒绝入库并打印具体错误 |

**关键决策：**
- `tags` 设为可选而非必填 —— 现有 15 个文件都无 tags 字段，强制 requires 会全 FAIL
- `product` 类型的 `tags` `version` `products` 全部设为可选 —— 实际产品文档很少填这些
- `lint_frontmatter.py` 抽取 `validate_frontmatter_dict()` 函数供 `parse_and_store.py` 复用，无 I/O 依赖

**验证结果：**
- 戴恩 8 文件 + 永和 7 文件 = 15 个全部 FIXED 后验证 PASS ✅

**待办更新：** Schema+Lint 完成，下一优先级：内容填充（戴恩 12 文件 + 永和 11 文件）

---

### 会话记录：07-09 戴恩知识库内容填充 + 夜间质量扫描

**模型：** `deepseek-reasoner`（Claude Code）

**执行内容：**

**1. 戴恩知识库内容补充（基于官网公开信息）**
- 新增 `certifications.md`（15+资质认证、37项专利、出口国家）
- 新增 `install_faq.md` / `after_sales.md`（安装FAQ + 售后FAQ）
- 更新 `company_intro.md`（研发中心、集团背景、周到佳品牌）
- 更新 5 个产品页面（补充 InstanHot X、SpinSoothe、7L容量、APP远程监控等技术细节）
- 11 个文件全部 lint 通过 ✅ → `gbrain import` 到容器 → **26/26 导入成功** ✅
- 搜索验证：新内容命中 0.88-0.91 分 ✅

**2. 夜间自动质量扫描**
- 新建 `openclaw/scripts/nightly_gbrain_probe.sh`
- 宿主机 crontab `0 3 * * *` 触发，日志写入 `logs/gbrain-nightly-probe.log`
- 内容：`gbrain doctor` + `gbrain dream` + `gbrain stats`
- 特点：纯内网部署、零配置、低成本增量执行、宿主机级 crontab 容器重启不影响

**关键决策：**
- 戴恩缺的 `sales/` `training/` `operations/` 不编，标记为需要内部资料
- 永和全部内容（产品参数、案例等）标记为需要客户/内部提供

**待办更新：** 已更新里程碑和优先级清单



## <a id="anchor-next-phase"></a>下一阶段优先级清单（2026-07-09）

### 🅰 高优先级

| # | 事项 | 预估 | 状态 |
|:-:|:-----|:----:|:----:|
| 1 | **GBrain Schema + Lint** — 定义知识类型+校验规则，strict 模式 | ~1 周 | ✅ 已完成 |
| 2 | **填充知识库未填内容** — 戴恩 12 文件 + 永和 11 文件 | 持续 | 📋 进行中 |
| 3 | **知识同步脚本（GBrain → cognee）** — 新内容管线能搜到 | ~2 天 | ✅ 已完成 |
| 4 | **GBrain 收尾** — 压测 50 并发、操作文档、灾难恢复 | ~2 天 | 📋 内容填充完成后 |

### 🟡 中优先级

| # | 事项 | 说明 |
|:-:|:-----|:------|
| 5 | **品牌图库系统** — 图片 ID 化，manage_library.py + image_library.json | 图片多了再做 |
| 6 | **收集家乐/宇婷反馈**，迭代 SOUL.md | 等她们开始用 |
| 7 | **嵌入式工程师文档助手**（tech-doc-generator skill） | 等工程师提供参数 |

### 🔵 低优先级

| # | 事项 | 说明 |
|:-:|:-----|:------|
| 8 | **永和产品图（smart_ring）入库** | 客户提供照片 |
| 9 | **ComfyUI 生图部署** | Pexels 已够用 |
| 10 | **Agent Manager 前缀标记** | 已放弃（模型不遵循格式指令） |

### ✅ 已完成的里程碑

| 里程碑 | 时间 | 状态 |
|:-------|:----:|:----:|
| GBrain Schema+Lint | 07-09 | ✅ |
| 戴恩知识库内容填充（8→11 文件） | 07-09 | ✅ |
| GBrain 夜间自动质量扫描 | 07-09 | ✅ crontab 每天 03:00 |
| GBrain → cognee 同步脚本 | 07-09 | ✅ 18/18 全量同步成功 |
| GBrain MVP（基础设施+数据迁移+管理后台） | 07-09 | ✅ |
| GBrain 知识库双品牌填充（15 文件） | 07-09 | ✅ |
| 文件上传→自动解析→GBrain 入库 | 07-09 | ✅ |
| 知识库 Agent 接入 GBrain（读+写） | 07-09 | ✅ |
| Cognee Phase 2（bge-m3 + reranker） | 07-07 | ✅ |
| 内容管线三平台（微信+小红书+抖音) | 07-07 | ✅ |
| 6 Agent 全部上线 | 07-07 | ✅ |

---

### 会话记录：07-08 GBrain 引入计划详情

**模型：** `deepseek-reasoner`（Claude Code）

**执行内容：** cognee Phase 2 升级 —— 将嵌入模型从 `paraphrase-multilingual-MiniLM-L12-v2` (384-dim, fastembed ONNX) 升级为 `BAAI/bge-m3` (1024-dim, FlagEmbedding PyTorch)，并新增独立 reranker 微服务 `dl-cognee-reranker`（`BAAI/bge-reranker-v2-m3`）。

**关键决策：**
- 使用 `/plan` 命令先方案设计，确认后再实施
- 三个关键选择：FlagEmbedding (PyTorch) / 全部重新嵌入 / 独立 reranker 容器
- 模型不塞镜像，通过 Docker named volume `cognee_hf_models` + init 容器下载

**部署顺序：** `make build` → 替换启动所有新容器 → init 下载模型 → migration 自动执行 → reembed.py 重嵌入存量数据

**后续 GBrain 计划关键发现：**
- GBrain 源码位于同级目录 `dato-knowledge-gbrain/`，是 Bun + TypeScript 项目（非 Go 二进制）
- GBrain 的嵌入通过 `src/core/ai/gateway.ts` 管理，原生支持 `llama-server`（OpenAI 兼容）recipe
- GBrain 内置 reranker 模块（`src/core/search/rerank.ts`），原生支持 `llama-server-reranker` recipe
- 协议格式已确认：`POST /v1/embeddings`（OpenAI 格式）和 `POST /v1/rerank`（`{model, query, documents}` 格式）
- **因此 GBrain 可直连现有 dl-cognee + dl-cognee-reranker，零额外模型、零 Ollama**

**相关记忆：** [[dl-cognee-model-cache]]、[[model-volume-external-mount]]、[[gbrain-integration-plan]]

---

### 会话记录：07-09 GBrain MVP 完成 + 管理后台汉化

**模型：** `deepseek-reasoner`（Claude Code）

**执行内容：**

**1. MCP API 测试验证**
- GBrain v0.42.57 容器运行正常（端口 8444）
- 通过 OAuth 2.1 client_credentials 注册客户端并获取 access token
- 成功测试 6 个 MCP API 调用：search、list_pages、put_page、get_page、query、get_stats（权限控制正确）
- 确认嵌入全程走内部 dl-cognee bge-m3（1024维），零外网依赖

**2. Admin SPA 中/英语言切换**
- 实现轻量 i18n 系统（React Context + custom hook），无第三方依赖
- 新建 3 文件：`i18n/context.tsx`（Provider + useT + locale-aware timeAgo）、`i18n/zh.ts`（中文翻译）、`i18n/en.ts`（英文原文）
- 修改 8 文件：main.tsx + App.tsx + 6 个页面（Login/Dashboard/Agents/RequestLog/Calibration/JobsWatch）
- 侧边栏底部添加 EN / 中文 切换按钮，语言偏好 localStorage 持久化
- 通过 dev path override 部署到容器 `/admin/dist/`，无需重编译二进制

**3. GBrain 实施计划更新**
- 确认 MVP（前 2 阶段）已完成无需额外工作
- 嵌入模型确认：配方名 `openai:text-embedding-3-large` 仅用于接口协议选择，实际由 `OPENAI_BASE_URL=http://dl-cognee:8080/v1` 覆写，调的是本地 bge-m3
- 更新记忆文件 [[gbrain-integration-plan]] 标记状态为 in_progress，列出剩余三阶段（Schema+Lint / Agent 飞书集成 / 收尾）

**待办更新：** 无变更（剩余阶段待确认优先级）

---

### 会话记录：07-09 GBrain Schema+Lint 实施完成

**模型：** `deepseek-reasoner`（Claude Code）

**执行内容：** GBrain 知识库 Schema+Lint 完整实施方案 —— 定义 schema 规则、创建 lint CLI 脚本、集成到入库流程、全量修复存量文件。

**改动文件（3 个）：**

| 操作 | 文件 | 说明 |
|------|------|------|
| 修改 | `openclaw/scripts/schema_rules.yaml` | 7 种知识类型，移除 `tags` 从必填→可选（匹配实际数据） |
| 新建 | `openclaw/scripts/lint_frontmatter.py` | CLI 校验器：支持 `--path` 单文件/目录、`--fix` 自动补全、退出码 0/1/2 |
| 修改 | `openclaw/scripts/parse_and_store.py` | 写入前 lint 校验，必填缺字段拒绝入库并打印具体错误 |

**关键决策：**
- `tags` 设为可选而非必填 —— 现有 15 个文件都无 tags 字段，强制 requires 会全 FAIL
- `product` 类型的 `tags` `version` `products` 全部设为可选 —— 实际产品文档很少填这些
- `lint_frontmatter.py` 抽取 `validate_frontmatter_dict()` 函数供 `parse_and_store.py` 复用，无 I/O 依赖

**验证结果：**
- 戴恩 8 文件 + 永和 7 文件 = 15 个全部 FIXED 后验证 PASS ✅

**待办更新：** Schema+Lint 完成，下一优先级：内容填充（戴恩 12 文件 + 永和 11 文件）

---

### 会话记录：07-09 GBrain 知识库内容填充 + 双品牌分类

**模型：** `deepseek-reasoner`（Claude Code）

**执行内容：**

**1. Tavily API 接入**
- 获取用户提供的 `TAVILY_API_KEY` 并记录到 [[tavily-api-key]]
- 通过 Tavily 成功搜索戴恩官网（daneenon.com）和百度百科，获取完整产品线信息
- 确认戴恩 4 大硬件 + 1 个软件方案：智能护理机器人、DEN FlexBath 360、助浴陪护一体床、床边清洗护理站、智慧养老解决方案

**2. 双品牌知识分类方案**（记录到 [[knowledge-classification-plan]]）
- **戴恩**（B端为主）：6 目录 — `company/` `product/` `sales/` `faq/` `training/` `operations/`
- **永和**（C端为主）：7 目录 — 同上 + `health/`（中医养生专属）
- 关键原则：`company/` + `operations/` 必须保留（管线依赖），先填内容再做 Schema+Lint

**3. brain-repo 重组 + 内容填充**
- 15 个新页面写入 brain-repo 并按分类目录组织
- 戴恩：company_intro + brand_guidelines + 5 个产品页 + FAQ（8 文件）
- 永和：company_intro + brand_guidelines + writing_style + audience + product_keywords + health/九种体质 + FAQ（7 文件）
- 通过 `docker cp` 复制进容器，重启后 GBrain 成功导入 15/15 页面
- 搜索验证通过（"护理机器人" 0.922、"阴虚体质" 0.912）

**4. 关键发现**
- GBrain `delete_page` 需要 `admin` scope（`read write` 不够）
- entrypoint 的 `gbrain import` 是幂等的，旧页面删除后有 slug 变化才会创建新记录
- Tavily 对中文搜索效果远好于内置 WebSearch 工具


### 会话记录：07-10 ComfyUI MCP 部署 + Skills 编辑 UI

**模型：** `deepseek-reasoner`（Claude Code）

**执行内容：** 一整天的大规模工程：

**1. ComfyUI 管线方案 A 实施**
- `comfyui_client.py` 重写为加载 `workflow_api.json`（Juggernaut-XL_v9 + HD 放大重绘）
- 工作流文件复制进 repo，3 个文件部署到内容运营容器
- `COMFYUI_URL` 写入 .env，管线现在走 Juggernaut-XL HD 模式

**2. 5 个 Agent 部署 MCP 生图**
- agent1/2/4/5/6 全部装了 FastMCP 包 + `comfy_mcp_server.py` + MCP 配置
- 修复 3 个 bug：`load_dotenv` 缺失、`shutil.copy2` 改为 `/view` API、容器网络

**3. Skills 编辑 UI（管理后台）**
- agent_detail.html 加 inline 编辑表单，4 文件改动
- DB + skills.yaml 同步更新，设 needs_restart

**4. 修复 dato-control 容器网络**
- 缺少 `dato_proxy_net` 导致 Agent 重启 500
- `docker network connect` 修复

**成果演示：** 通过飞书对话让 Agent1 成功生成小狗图片 ✅

| 里程碑 | 时间 | 状态 |
|:-------|:----:|:----:|
| ComfyUI 管线方案A（Juggernaut-XL + HD） | 07-10 | ✅ |
| 5 Agent MCP 生图部署 | 07-10 | ✅ |
| Skills 编辑 UI | 07-10 | ✅ |
| dato-control 网络修复 | 07-10 | ✅ |
| ComfyUI MCP 踩坑修复（load_dotenv /view API） | 07-10 | ✅ |

### 待办：永和交付（2026-07-11）

**背景：** 永和服务器（192.168.10.101）无 NVIDIA GPU，远程调用这台开发机（192.168.10.70，RTX 3050）的 ComfyUI。

**代码集成 → delivery/yonghe-v1 → 永和服务器部署**

| # | 事项 | 状态 |
|:-:|:-----|:----:|
| 1 | `comfy_mcp_server.py` 搬进 repo `openclaw/comfy-mcp/` | 📋 |
| 2 | `workflow_api.json` 复制到 mcp 目录 | 📋 |
| 3 | `openclaw.json.j2` 模板加 MCP 段 | 📋 |
| 4 | `config_gen.py` 加 COMFYUI_URL + MCP 渲染 | 📋 |
| 5 | `service.py` 加 COMFYUI_URL carry-forward | 📋 |
| 6 | docker-compose 加 ComfyUI 服务（`profiles: ["gpu"]`） | 📋 |
| 7 | 合到 `delivery/yonghe-v1` 并推送 | 📋 |
| 8 | 永和服务器 git pull → make build → make up | 📋 |

---

## 养老院项目：第二家客户 — 杭州市社会福利中心（2026-07-28）

### 背景

AI 养老院院长项目 MVP 首发客户为 **杭州市第三社会福利院**（1,752 床 / 350 员工 / 26 栋楼）。经调研发现第二家潜在客户 **杭州市社会福利中心**，已完成专属调研问卷。

### 杭州市社会福利中心关键信息

| 维度 | 详情 |
|------|------|
| **性质** | 杭州市民政局直属公办养老机构，挂"杭州市光荣院"牌子，公益二类 |
| **成立** | 1999 年 11 月投用，2024 年 25 周年 |
| **位置** | 拱墅区和睦路 451 号 |
| **规模** | 占地 60 亩，建筑面积 54,000㎡，总投资 1.6 亿元 |
| **床位** | 初始 1,450 张，2023 年后缩减至 1,300+ 张 |
| **员工** | 近 300 名 |
| **分区** | 自理区、介助区、介护区、认知障碍照护专区 |
| **科室** | 综合管理科、护理管理科、医疗康复科、社工工作室（秦芸工作室）、膳食营养科、养老服务培训科、安全保障科 |
| **管理模式** | "1+1+N"网格化管理（区别于三福的楼栋负责制） |
| **特色** | 浙江省首家养老机构内部社工工作室、"1+X"多专业团队、临终关怀、光荣院兜底职能 |
| **荣誉** | 全国文明单位、全国敬老文明号、浙江省四星级养老机构 |

### 调研问卷

已创建 `docs/调研问卷-杭州市社会福利中心.md`（**88 问**，含 41 MVP + 23 二期 + 19 通用），对比三福问卷（65 问）新增 23 问。

**核心差异点（与三福对比）：**

| 维度 | 三福 | 社会福利中心 |
|------|------|:----:|
| 管理模式 | 楼栋负责制（6 栋 → 6 Agent） | "1+1+N"网格化 |
| 特色科室 | — | 社工工作室 + 养老服务培训科 |
| 服务对象 | 普通养老 | 含光荣院（特困/孤老/优抚） |
| 组织架构 | 护理/总务/综合独立 | 总务+财务+人事统一归综合管理科 |
| 总问数 | 65 | **88** |
| MVP 问数 | 32 | **41** |

**新增专属章节：**
- 社工工作室/秦芸工作室（8 问：#22-29）
- 养老服务培训科（6 问：#39-44）
- 照护分区专项（5 问：#59-63）
- 光荣院兜底人群（问题 #78-79）

**极速模式（12 核心问）：** 1, 5, 8, 9, 22, 24, 35, 39, 46, 60, 70, 74

### 后续影响

- 适配社会福利中心时，Agent 架构需从"楼栋负责制"改为"网格化 + 分区制"
- 需新增社工、培训等专属 Agent 或技能
- 光荣院属性需特殊数据字段（优抚对象标识、兜底费用等）
- 公办机构合规约束需在系统设计中提前考虑

### 相关文件

- `docs/第二家-杭州市社会福利中心消息.md` — 基础信息汇总
- `docs/调研问卷-杭州市社会福利中心.md` — 实地调研问卷（88 问）
- `docs/调研问卷-三福养老院.md` — 三福调研问卷（参照模板）

---

## 养老院项目：网络部署与域名方案（2026-07-30）

### 背景

养老院现场部署时服务器通过 DHCP 获取 IP，不能手动配死。需要开箱即用的方案：用户手机扫码即访问，IP 变化自动适配。

### 讨论过程

1. 尝试 WiFi IP + HTTPS 9443 → 手机无法访问（Docker iptables + UFW 防火墙）+ 自签名证书被拒
2. 修复 iptables + 开放 HTTP 9080 端口 → 可行但 Cookie `Secure` 标记导致 HTTP 登录失败
3. 修复 Cookie `Secure` 为条件判断 → 手机 HTTP 登录成功
4. 尝试 mDNS `.local` 域名 → Android 不原生支持
5. 明确需求：开箱即用、自动广播、动态 IP 自适应
6. 决定采用方案：**真实域名 + 二维码 + DDNS**（详见 `docs/ip策略.md`）

### 域名方案（已确定）

| 项目 | 值 |
|------|-----|
| **主域名** | `eldcare.cn`（待购买） |
| **三福子域名** | `hz-sanfu.eldcare.cn` |
| **社会福利中心子域名** | `hz-shefuli.eldcare.cn` |
| **购买渠道** | DNSPod（腾讯云）或阿里云 DNS |
| **二维码** | `https://hz-sanfu.eldcare.cn`（永久不变） |

### 当前开发阶段

开发阶段使用 `https://hz-sanfu.eldcare.cn`（标准 HTTPS 443，真证书）。远程开发使用 SSH 直连 192.168.10.247。

### 部署待办（阶段二：产品化）

| # | 事项 | 依赖 | 状态 |
|---|------|------|:--:|
| 1 | 购买 `eldcare.cn` 域名 | — | ✅ |
| 2 | 在 DNS 服务商创建 API 凭据（最小权限，仅能改本设备记录） | 域名购买 | ✅ |
| 3 | 编写 DDNS 更新脚本（检测当前 IP → DNS API 更新 A 记录） | API 凭据 | ✅ |
| 4 | systemd 开机自启 | — | ✅ |
| 5 | Let's Encrypt DNS-01 签发正式 HTTPS 证书（替换自签名） | DNS API | ✅ |
| 6 | Caddy 端口改 443 + 加载正式证书 | 证书就绪 | ✅ |
| 7 | 生成永久二维码 | 证书就绪 | ✅ |
| 8 | 写入 iptables 持久化规则（443/9080 放行） | — | ✅ |
| 9 | DNS 服务器固定地址（服务器重启 DNS 不丢失） | 部署时 |

### 相关文件

- `docs/ip策略.md` — 完整网络部署方案（v1.1，已更新域名）
- `scripts/ddns_update.py` — DDNS 更新脚本
- `scripts/gen_qrcode.py` — 二维码生成
- `scripts/ddns-updater.service` — systemd 服务

---

## 养老院项目：本期会话记录（2026-07-28 ~ 07-31）

### 工作流调试与修复

- 修复 `config_cache.py` 硬编码的旧项目 UUID，改为 precreated_id 缓存
- 重构 `nursing_ops.py` 的 agent 解析，四级优先级（显式输入 > precreated 缓存 > workflow 默认 > 友好错误）
- 修复 `dispatch.py` token 读取（去重 .env 中重复的 DL_INTERNAL_TOKEN，last-wins 语义）
- 发现并修复 agent 容器缺少 DATABASE_URL 导致 handler 无法查 DB
- 修复 agent 容器缺少 DL_INTERNAL_TOKEN 环境变量导致 dispatch 401
- 修复 openclaw.json 缺少 models 配置段导致 "No API key found"
- 修复 nursing-schedule handler：day.isoformat() 返回 string 但 DB 期望 date
- 授予 dl_control_app 对 nursing 表的 INSERT/UPDATE/DELETE 权限
- maxTokens 从 32768 改为 65536
- 工作流 4 步全部调通（排班、物资、财务、周报）
- 编写 60 个单元测试 + 13 个集成测试

### Dashboard 修复

- 种子数据日期固定导致当前日期无数据 → 查询改为 `_eff_date()` 兜底（CURRENT_DATE 无数据时自动降级）
- Seed SQL 末尾添加数据填充块（唯一索引 + ON CONFLICT DO NOTHING）
- 仪表板全面恢复：完成率、当日在岗、排班、菜单

### 工单页面重设计

- 内联 CSS 卡片布局：统计格、进度条、过滤标签、行内状态徽章
- 数据源增加 staff_name、note 字段

### 周报页面 /reports

- API: `/api/nursing/report` 提取工作流结果，展开 OpenClaw 容器并提取 JSON
- 前端渲染：4 步可展开，排班/物资用卡片网格，财务用 markdown 渲染，院长用分区卡片 + 重点关注列表
- 多 payload 拼接（LLM 使用 tool 时会分拆到多个 payload）

### 手机访问与域名

- 开放 9080 HTTP 端口（经 iptables DOCKER-USER），解决 Docker + UFW 矛盾
- 修复 Cookie `Secure=True` 导致 HTTP 登录失败
- 购买域名 `eldcare.cn`（DNSPod）
- 编写 DDNS 更新脚本 `scripts/ddns_update.py`（python3，Tencent Cloud SDK）
- systemd 开机自启 `ddns-updater.service`
- Let's Encrypt DNS-01 + acme.sh 签发真实证书（`hz-sanfu.eldcare.cn`）
- Caddy 配置 443 端口映射 + 加载真证书
- 二维码生成脚本 `scripts/gen_qrcode.py`

### 待办（后续会话）

- 养老院现场部署时：DNS 服务器地址固化、iptables 持久化、新服务器重置 API 凭据
- 社会福利中心适配：Agent 架构需从"楼栋负责制"改为"网格化 + 分区制"
---

## <a id="anchor-ocr"></a>nursing-erp OCR 功能完成 + 断电恢复（2026-08-13）

### 一、两个项目现状

**nursing-erp**（养老院业务系统，独立仓库 `github.com/huha-yy/nursing-erp`）：
- Django 6 + django-unfold + django-ninja，SQLite（开发）/ PostgreSQL（生产）
- 独立部署目录 `/home/nursing-home/huha-project/nursing-erp/`
- 开发服务器：`cd nursing-erp && DEBUG=true ALLOWED_HOSTS="*" .venv/bin/python manage.py runserver 0.0.0.0:8765`（手动启动，断电不自启）

**ai-nursing-home**（AI 系统，`github.com/huha-yy/ai-nursing-home`）：
- Docker Compose 部署，10+ 容器（dato-control/postgres/redis/cognee/llm-proxy/gbrain/15个agent/ocr等）
- 断电自动恢复（`restart: unless-stopped`）

### 二、OCR 功能（本次核心，全部完成）

两个 OCR 场景 + LLM 双引擎：

| 页面 | 地址 | 用途 | 落库 |
|------|------|------|------|
| 菜单 OCR | `/menu-ocr/` | 食堂拍周菜单 | WeekMenu |
| 点餐 OCR | `/meal-order-ocr/` | 选老人+拍点餐单 | MealOrder |

**核心架构：OCR 看图识字 + LLM 理解纠错**
```
拍照 → Baidu OCR 提取文字 → DeepSeek LLM 结构化+纠错 → 匹配菜品库 → 落库
```

关键文件（nursing-erp）：
- `nursing_erp/llm.py` — DeepSeek 调用封装（重试2次+90s超时）
- `meals/api.py` — `_ocr_extract_multi`（跨页）、`_llm_structure`（mode=menu/order）、`_dish_match`（整词匹配）、`_parse_order_item`（括号特殊要求）
- `templates/menu_ocr.html`、`templates/meal_order_ocr.html` — 前端

能力清单：
- ✅ LLM 结构化（识别星期/餐次层级，不再逐行手动标注）
- ✅ LLM 纠错（清蒸鲈渔→清蒸鲈鱼，对齐菜品库93道菜）
- ✅ 特殊要求识别（少盐/不吃(外出)，点餐OCR）
- ✅ 跨页合并（多张图标注【第N页】，LLM自动合并）
- ✅ 前端图片压缩（Canvas 1600px，避免大图OCR超时）
- ✅ 整词匹配算法（`_dish_match`，拒绝单字重叠误匹配）

测试文档：`docs/OCR/测试菜单.md`、`docs/OCR/测试老人点餐单.md`

### 三、AI/ERP 数据对接（Mock → 真实数据）

ai-nursing-home 的 Dashboard 和 Chat 技能查询，已从 Mock 表切换到 nursing-erp API：

| 查询类型 | 之前 | 现在 |
|------|------|------|
| 库存预警 | nursing_inventory Mock表 | GET /api/inventory/low-stock/ |
| 排班 | nursing_schedules Mock表 | GET /api/schedules/ |
| 老人 | nursing_residents Mock表 | GET /api/residents/ |
| 菜单 | nursing_meals Mock表 | GET /api/meal-plans/ |
| 异常 | nursing_health_alerts Mock表 | GET /api/incidents/ |
| 员工 | nursing_users Mock表 | GET /api/employees/ |

关键实现：`main.py` 里 SKILL_QUERIES 用 `API:/path` 前缀标识走 API，`_erp_items()` 解析分页响应。环境变量 `NURSING_ERP_URL`。

### 四、断电恢复（2026-08-13）

服务器意外断电，排查修复：

1. **Docker 服务** — 自动恢复（restart policy），无需处理
2. **nursing-erp 开发服务器** — 手动 runserver 不自启，需重启
3. **Clash 代理**（`127.0.0.1:7890`）— 断电后没启动，导致外网（DeepSeek/unsplash）全断
   - 修复：`systemctl --user start clash`
   - 持久化：`sudo loginctl enable-linger daneenon`（user 服务无登录自启）
4. **防火墙 iptables** — 断电后 DOCKER-USER 规则丢失，HTTPS 443/9443 端口被拦
   - 修复：放行 443/9443/9080/9081 + `netfilter-persistent save`
5. **登录背景图** — 引用外部 unsplash，服务器外网断则空白
   - 修复：下载到本地 `static/login-bg.jpg`，改本地引用

### 五、关键技术点/踩坑

- **DeepSeek API key** 触发 GitHub secret scanning（sk-开头），已移到 `.env`（gitignore），settings.py 用 `os.environ.setdefault` 读
- **OCR 大图超时**：手机照片 4000px+ 会导致 OCR 超时，前端必须 Canvas 压缩
- **LLM 偶发抖动**：DeepSeek 偶尔超时导致结构化返回空，已加重试
- **Django unfold 嵌套 `<style>` 标签**：误加导致主 CSS 全失效（图标白色/Toast白色），已修
- **容器 read_only**：pip 装包会丢，Pillow 等需进 Dockerfile

### 六、待办（后续会话）

- [ ] nursing-erp 生产部署（Docker 化，PostgreSQL + Gunicorn）
- [ ] 菜单 OCR 未匹配菜名的人工处理流程（前端目前只红色标出，不能直接添加菜品库）
- [ ] 老人点餐 OCR 的"不吃/外出"特殊要求的落库细节完善
- [ ] 养老院现场部署（DNS固化、iptables持久化、重置API凭据）
- [ ] 社会福利中心适配（网格化+分区制 Agent 架构）

---

## <a id="anchor-openclaw-upgrade"></a>OpenClaw 镜像升级评估（2026-08-20）

### 当前状态

| 项 | 值 |
|---|---|
| 基础镜像 | `ghcr.io/openclaw/openclaw:2026.4.8` |
| 自有镜像 tag | `dato-openclaw:2026.4.8`（规范：`dato-openclaw:<上游版本>[-patch<N>]`） |
| 镜像大小 | ~14.3GB（torch + 飞书 TS 源码完整重建） |
| 计划升级目标 | **2026.7.1** |
| 决策 | 📋 待实施（需先做 7.1 验证测试） |

### 镜像内容
- 中国镜像加速：阿里云 apt/npm/pip + hf-mirror
- 工具：Playwright MCP、markitdown、ffmpeg、whisper、nano-pdf、python-pptx
- 飞书补丁：`feishu-reply-dispatcher.ts`（537 行）、`feishu-bot.ts`（1308 行）
- 自定义技能 ~20 个（admin-mgmt/cognee/workflow/nursing 系列/ppt-master 等）
- ComfyUI MCP server、任务接收 sidecar、DeepSeek 预配置

### 核心坑（handler.py 技能不注册）
- **OpenClaw v2026.4.8 不把 Python `handler.py` 技能注册为 agent 可见的 callable tool**
- **已调研 2026.7.1 release notes（357KB 全文）：无修复证据**（两条 handler 条目 = Gateway 审批 #79861、ACP 事件诊断 #100558，均无关）
- **当前规避方案**：所有 agent 走 `process` 工具 `python3 -c "from handler import ..."` 调用
- 涉及技能：admin-mgmt、cognee、workflow、nursing-schedule、meal-query 等

### 升级前验证清单（待办）
- [ ] 拉 `openclaw:2026.7.1` 容器跑 handler 注册验证测试（不改生产镜像、可回滚）
- [ ] 按 PROVENANCE.md 补丁协调流程：diff 新旧 extension → 重打飞书补丁 → 更新 PROVENANCE.md
- [ ] 验证 `openclaw.json` schema 兼容 + 端到端生成测试
- [ ] 升级后跑飞书集成冒烟测试（P3）

### 相关文件
- `openclaw/Dockerfile`、`openclaw/PROVENANCE.md`（补丁协调/回滚流程）
- `CLAUDE.md` line 29/58（handler 不可调用，agent 需用 process 工具）

---

## <a id="anchor-public-access"></a>公网访问架构：新域名 + frpc 隧道（2026-08-21 记录，08-15 上线）

### 背景
院内一体机（192.168.10.247）需要从院外公网访问 ERP 后台和 AI Chat。原域名 `hz-sanfu.eldcare.cn` 的 A 记录指向内网 IP，**仅院内局域网可用**。2026-08-15 起 frpc 隧道上线，经腾讯云服务器中转，提供公网 HTTPS 入口。

### 访问入口

| 服务 | 公网地址（院外/院内通用） | 院内直连（不走云） |
|---|---|---|
| nursing-erp 后台 | `https://admin.eldcare.cn:8443/admin/` | `http://hz-sanfu.eldcare.cn:9081/admin/` |
| AI Chat（dato-control） | `https://chat.eldcare.cn:8443/chat` | `http://hz-sanfu.eldcare.cn:9080/` |

### 链路架构

```
用户浏览器 → 腾讯云 43.137.7.133（frps :7000，:8443 按 Host 域名分流）
  ├─ admin.eldcare.cn → 远端 19081 → frpc 隧道 → 本机 Caddy :9081 → nursing-erp runserver :8765
  └─ chat.eldcare.cn  → 远端 19080 → frpc 隧道 → 本机 Caddy :9080 → dato-control :8080
```

### 关键配置位置

| 配置 | 位置 |
|---|---|
| frpc 客户端 | `/etc/frp/frpc.toml`，systemd `frpc.service`（开机自启） |
| frp 代理映射 | `ai-chat`：127.0.0.1:9080→远端 19080；`erp`：127.0.0.1:9081→远端 19081 |
| DNS 记录 | `chat`/`admin` → 43.137.7.133（手工设置）；`hz-sanfu` → 192.168.10.247（`ddns-updater.service` 仍在更新） |
| nursing-erp 域名白名单 | `nursing-erp/.env`：`ALLOWED_HOSTS=admin.eldcare.cn,...`、`CSRF_TRUSTED_ORIGINS=https://admin.eldcare.cn:8443` |
| dato-control 站点域名 | `infra/.env`：`DL_CONTROL_SITE_HOST=chat.eldcare.cn` |

### 踩坑/注意
- **直接访问 frp 远端端口（如 `http://chat.eldcare.cn:19081`）返回 502，不是有效入口**——必须走 `:8443` 域名分流
- 云端分流配置在腾讯云服务器上，**不在本仓库**；frps token 见 `/etc/frp/frpc.toml`
- `infra/Caddyfile` 主站块与 `infra/.env` 的 `CADDY_DOMAIN` 仍写 `hz-sanfu.eldcare.cn`——`:9080`/`:9081` 站点块不匹配 Host 所以公网链路不受影响，但重构时注意
- nursing-erp 是**宿主机进程**（`manage.py runserver 0.0.0.0:8765`，非容器）——它挂了公网 ERP 就 502

### 运维命令
- 看隧道状态：`systemctl status frpc`
- 重启隧道：`sudo systemctl restart frpc`
- 2026-08-21 已验证：两个公网 URL 均正常响应（admin 登录页可渲染）

---

## <a id="anchor-erp-api-auth"></a>nursing-erp /api/ 认证加固（2026-08-21）

### 背景（P0）
ERP 经 `admin.eldcare.cn:8443` 暴露公网后，`/api/` 无认证意味着老人 PII（姓名/身份证/健康数据）
可被任何互联网用户读取、护理记录可被伪造、OCR 端点可被盗刷 DeepSeek 配额。
实测：`curl https://admin.eldcare.cn:8443/api/residents/` 匿名返回全部老人数据。

### 方案：API key（机器）+ session（浏览器）双认证
- **nursing-erp**（3 处改动 + 新文件）：
  - 新增 `nursing_erp/api_auth.py`：`erp_auth` 认证函数——`X-API-Key` 头与
    `settings.ERP_API_KEY`（.env）常量时间比较，或已登录 Django session，任一通过；
    都失败 → 401。**未配置 key 时 fail-closed**（密钥路径永不放行，不退化为无认证）
  - `nursing_erp/urls.py`：`NinjaAPI(..., auth=erp_auth)` 全局生效
  - `nursing_erp/settings.py`：`ERP_API_KEY` 环境变量 + `LOGIN_URL = "/admin/login/"`
  - `nursing_erp/views.py`：7 个轻量页视图加 `@login_required`（未登录跳 admin 登录页，
    登录后回跳；页面 JS 的 fetch 同源自动带 session cookie，模板零改动）
- **ai-nursing-home**：
  - `dl-control/dl_control/main.py`：新增 `_erp_headers()` 辅助函数，8 处
    `AsyncClient(timeout=10.0)` 全部改为带 `X-API-Key` 头
  - `infra/docker-compose.yml`：dato-control 服务加 `NURSING_ERP_API_KEY` env 透传
  - `openclaw/skills/nursing-erp-query/handler.py`：`_client()` 支持从
    `NURSING_ERP_API_KEY` env 读 key（agent 容器目前未配 ERP env，技能经 dl-control 中转，
    暂无容器需重启）
- **密钥**：`nursing-erp/.env` 的 `ERP_API_KEY` 与 `infra/.env` 的 `NURSING_ERP_API_KEY` 同值，
  gitignored；生成方式 `python3 -c "import secrets; print(secrets.token_urlsafe(36))"`；
  两侧 `.env.example` 已补文档

### 验证结果（2026-08-21，全绿）
| 检查 | 结果 |
|---|---|
| 公网匿名 GET /api/residents/ | 401 ✅（加固前 200 泄露 PII） |
| 公网匿名 POST /api/incidents/ 伪造 | 401 ✅ |
| 公网 GET /kitchen/ | 302 → admin 登录页 ✅ |
| 公网带 key GET /api/residents/ | 200 ✅ |
| dato-control 容器内带 key 调 ERP | 200 ✅（Dashboard/Chat 数据链路正常） |
| dato-control 容器内无 key | 401 ✅ |
| nursing-erp 测试 | 39 passed（33 原有 + 6 个新认证测试 `tests/test_api_auth.py`） |

### 踩坑/注意
- **Makefile 的 compose project 名与实际栈不一致（已修复）**：根因是 fork 自 dato 平台时
  （commit 4108d19）只改了 Makefile 的 `--project-name nursing`，漏改
  `scripts/lib/appliance-common.sh`（仍是 `dato`）。一体机经 `scripts/init` 安装，容器
  label 全是 `project=dato` → `make up` 报容器名冲突、`make ps/logs` 视角为空。
  2026-08-21 已把 Makefile 改回 `--project-name dato` 并加注释，`make ps` 验证恢复。
  注意：修复后 `make down`/`make wipe` 等破坏性命令会真正作用于生产栈（这是应有语义，慎用）。
- 零中断上线顺序：先给调用方（control）配好 key 并重启 → 再启用 ERP 认证，中间无 401 窗口
- 多设备同账号并发不受影响（Django session 每设备独立，互不踢下线）
- `openclaw/skills/` 的 handler 改动按 CLAUDE.md 规则需要 docker cp 进 agent 容器才生效——
  本次因 agent 未直连 ERP，无需操作

## <a id="anchor-erp-building-scope"></a>nursing-erp 楼栋权限贯通：X-Building 头 + 路由加固（2026-08-21）

背景：nursing-erp 阶段一第二批（隐患 #2 staff FK、#3 API 楼栋过滤）的 AI 侧配套。
ERP 侧改动见 nursing-erp 仓库（commit 846d083 / ffa1c9a / f4649e8）。

### dl-control 改动（commit 4c95e7a + 6ff75b7）
- `_erp_headers` / `_skill_queries` / `_week_start` 从 create_app 闭包提升到模块级（可单测），
  新增 sess 参数：**楼长会话（building 非空）自动附 X-Building，管理层不发头 → 全院**；
  7 个 ERP 调用点（chat 预取、alerts×2、dashboard×4）全部传 sess
- **堵两个无鉴权路由**：GET /api/nursing/alerts 与 /api/nursing/dashboard 原先公网匿名可读
  ERP PII，现经 `_load_nursing_sess` 校验 nursing cookie，匿名 401（dashboard.html 同源
  fetch 带 cookie，登录用户不受影响）
- **修 meal-query 404**：技能预取原先指向不存在的 `/api/meal-plans/`（自上线静默 404），
  改为 `/api/week-menu/?week_start=本周一`
- 新增 `tests/test_nursing_erp_headers.py`（7 项），全套 70 passed + 10 skipped

### 踩坑：中文 HTTP 头（联调抓出的真 bug）
httpx 对非 ASCII 头值直接 `UnicodeEncodeError`；raw UTF-8 字节上线路径经 WSGI latin-1
解码成乱码（fail-loud 400 恰好暴露）。**契约：dl-control 侧 `quote()` percent-encode 发送
（纯 ASCII，过 Caddy/frp 隧道可靠），ERP 侧 `unquote()` 还原**（不含 % 的值原样，
进程内测试直传中文兼容）。两侧各加了契约钉测试。
教训：单测只构造 headers dict 没真发请求，编码问题只有联调暴露——涉及线上协议的
改动必须有真网验证。

### 验证结果（2026-08-21，全绿）
| 检查 | 结果 |
|---|---|
| 匿名 GET chat…/api/nursing/dashboard 与 /alerts | 401 ✅（原先 200 泄露 PII） |
| key + X-Building:7号楼（未知名） | 400 fail-loud ✅ |
| key + X-Building:1号楼 → /api/residents/ | 200 仅 6 位 1号楼老人 ✅ |
| /api/beds/occupancy/ 带头 | 仅 1号楼，覆盖 ?building= 语义 ✅ |
| b1_liu（1号楼长）dashboard | focus_residents 2 条全 1号楼、预警 2 ✅ |
| wang_jianguo（院长）dashboard | focus_residents 5 条跨 4 栋楼、预警 8 ✅ |
| /api/week-menu/?week_start=周一 | 200，21 条 ✅（meal-query 修复） |

注意：dashboard 的 building_distribution/schedule_today/completion_rate 等仍来自
一体机本地 Postgres（nursing_residents 等种子表），**未按楼栋过滤**——属旧 MVP 数据
路径，不在本次隐患 #3 范围；ERP 来源的 focus_residents/pending_health_alerts/
low_stock 已带头。若后续要把 dashboard 全量切到 ERP，需一并补楼栋语义。

### 遗留（人工）
- ~~nursing-erp 11 条刘主任歧义行挂空~~ → **已解决（08-21 同日）**：用户定夺全挂
  1号楼刘主任（id=7；上下文证据：张国栋住 1号楼、任务执行人均 1号楼、无一条指向
  5号楼）。备份 `backups/db-before-liu-zhuren-fix-20260821.sqlite3` 后定向 update 11 条，
  幂等重跑 `backfill_staff_fk` 清零——74/74 全挂接，0 歧义 0 无匹配。

---

## 2026-08-21（下午）· nursing-erp 应收月账单（阶段二 Q3）上线

Q3 定稿落地：床位费+护理费+餐费合并出账、全额核销、欠费名单。nursing-erp 新建
`billing` app（FeeRule 价目表 + MonthlyBill + services + API 6 端点 + /billing/ 看板 +
admin 核销 actions），种子价目 800/300~2400/15 由数据迁移写入。提交 `31425ef`（功能）
+ `30d01f5`（路线图）。测试 18 新增、全套 102 绿；ruff 新文件干净。

生产 2026-08 出账 36 张 ¥85,555，联调（127.0.0.1:8765 真网）全绿：匿名 302、
summary 三额、1号楼 percent-encode 头 scope=6 张、settle/unsettle 往返、跨楼守卫。

### 勾稽与偏差（记录在案）
- 床位+护理 70,600 与计划锚点**分毫不差**（14×1100 + 10×2000 + 8×2800 + 4×3200）。
- 餐费 14,955 vs 锚点 13,320：差额 1,635 全部来自张国栋（id=1）名下 171 条点餐
  （演示期重复提交，同一 date+meal_type 最多 7 条）——月结行按口径刷新 930→2,565。
  属设计内行为（餐费以 MealFinance=MealOrder 实际数据为准）；**点餐去重是独立的
  数据治理事项**，未在本次处理（meals/ 零改动承诺）。

### 踩坑：pytest 之外的 ORM 复现脚本直接写生产
排障 ninja 400 时图省事用 `python -c` + Django setup 复现——**没意识到这连的是
生产 db.sqlite3**（pytest 才有隔离测试库）。`_resident()` 已插入一位"测试老人"，
后续 bed 挂接撞唯一约束才暴露。确认无子记录后按 id+name 定向删除（36 位老人复原）。
教训：**生产库就是默认库**——凡要跑 ORM 复现，一律写 throwaway pytest（`tests/_scratch_*.py`
跑完即删），禁止 manage.py shell / python -c 建对象。

### 后续可选
- dl-control 财务技能接 /api/billing/（ERP 侧 API 已就绪，另起小活）
- 旧 /api/meal-finance/generate/ 硬编码 15 与价目分叉——可改读 FeeRule
- 点餐重复数据治理（张国栋 171 条）

---

## <a id="anchor-moonshot"></a>2026-08-21 · AI 栈 DeepSeek → Moonshot/Kimi 切换（15 agent 全量）

动机：DeepSeek 余额耗尽，chat.eldcare.cn 对话不可用。全栈换 Moonshot
（`https://api.moonshot.cn/v1`，模型 `kimi-k2.6`——注意 API id 是 kimi-k2.6 不是 kimi-2.6）。

改动面：infra/.env 新增 `LLM_API_KEY/LLM_BASE_URL/LLM_MODEL`（DEEPSEEK_API_KEY 留作
legacy fallback）；dl-control settings/main/provisioning 全部 provider 中立化
（非 agent 角色直连 LLM 的调用不再写死 deepseek）；compose 传 LLM_*；模板/setup 脚本
setup-deepseek.sh→setup-llm.sh；receiver 注入 OPENAI_API_KEY/OPENAI_BASE_URL；
installer/文档同步。70 tests 绿，新增行 ruff 干净。E2E：b1_liu（楼长）与
wang_jianguo（院长）经 https://chat.eldcare.cn:8443 对话均正常。

### openclaw 鉴权三坑（逆向 /app/dist 得出，CLAUDE.md 部署清单已固化）
1. **env marker 白名单**：models.json 的 apiKey 环境标记必须是 openclaw 认识的名字
   （`OPENAI_API_KEY`/`DEEPSEEK_API_KEY` ✓，`LLM_API_KEY` ✗ 静默解析为 null →
   "No API key found for provider openai"）。所以 agent config/.env 同一 key 双写
   `LLM_API_KEY`（dl-control 读）+ `OPENAI_API_KEY`（openclaw 解析）。
2. **auth-profiles.json 只认新版 store 格式** `{version,1,profiles}`；扁平整Shape被
   coercePersistedAuthProfileStore 直接拒收。
3. **openclaw.json `models.providers` 以 merge 压过 agent models.json**：4 个最早期
   agent（院长/护理科/总务科/通用助手）残留 deepseek-era providers 块（baseUrl
   api.deepseek.com + 明文 key），把 Moonshot 配置整个盖掉——表现为 DeepSeek 风味
   401/402（"insufficient balance" 实为 DeepSeek 402）。setup-llm.sh 现已顺带迁移该块。

### 顺带修掉的老 bug：DL_INTERNAL_TOKEN 重复行
历次重复 provisioning 给 6 个楼栋 agent 的 config/.env 追加至多 4 行
DL_INTERNAL_TOKEN：source 后最后一行生效（receiver 认），而 main.py 读第一行 →
agent 路由 401 静默回落直连 LLM。已全量去重（保留最后一行）。

### 排障方法论（值得复用）
- 对比凭证**一律 sha256 指纹**（且注意 `cut`/管道带尾换行 vs python strip 的预像差异，
  本次差点被"两个不同的 key"假象带偏——其实是同一 key 的 hash 预像差一个 `\n`）。
- docker exec 新起 shell 不继承 entrypoint 环境，验进程真实 env 要读
  `/proc/<pid>/environ`。
- 错误文案能指路：`insufficient balance`/`Authentication Fails, Your api key` 是
  DeepSeek 的话术——出现即说明请求其实打到了 deepseek baseUrl。

### 现场遗留文件
agent 目录留有 `*.bak-pre-moonshot`（pass2 前）与 `*.bak-pass3*`（pass3/3b）备份；
旧 DeepSeek 明文 key 仍在备份与 infra/.env 的 DEEPSEEK_API_KEY 行（rollback 用，
换 key 时记得一并处理）。

### 后续计划：切换本地 qwen3.6 27b（用户已拍板要做，时间未定）
流程与本次同构——改 `LLM_*` 三变量 + 按 CLAUDE.md 四站点矩阵走，完整手册已写进
CLAUDE.md「Vendor switch runbook」一节。**注意：本地目标是宿主机的
`dato-vision.service`（vLLM serve ocicek/Qwen3.6-27B-NVFP4，0.0.0.0:8000，key
在 ~/.config/dato/vision.env，与 ComfyUI 共卡 gpu-util 0.35、平时常处于
inactive），不是项目内的 dl-llm-local(ollama)——两者别混。**差异三点：① 切换前
`systemctl --user start dato-vision` 并确认显存余量（ComfyUI 常驻 28GB）；② j2
模板 baseUrl 写死 moonshot，要改 `${OPENAI_BASE_URL}`（**唯一代码点**）；
③ models.json reasoning/maxTokens/contextWindow 按 Qwen3.6-27B 实调。

## 2026-08-24 · nursing-erp 点餐防重（清存量 + 堵增量）

用户发现张国栋的 OCR 点餐重复（"每个老人不能每周多次点餐"），定位为两层问题
一次做完（nursing-erp 提交 `3404ac6`/`7ee1e39`）：

**清存量**：张国栋 171 条点餐实为 63 个有效槽位（08-06~08-18 三周的
早餐/午餐/晚餐），重复全部来自 OCR 重拍/重复识别（同分钟成批、08-12 两轮 +
08-13 一轮）。每槽位保留首单 min(id)——菜谱有分歧的 3 个 08-23 槽位
（4 票 vs 1 票）min(id) 恰为多数派；108 条重复行连同 111 条 M2M 一事务删除。
备份 `backups/db-before-meal-dedup-20260824.sqlite3`。

**堵增量**：MealOrder 条件唯一约束（resident+date+meal_type，非 cancelled；
SQLite 部分索引，迁移 0003）+ 三条创建路径（单条/批量/OCR 批量）加
`_assert_no_active_duplicate` 预检：批内重复或撞库中有效订单 → 400 整批拒绝。
OCR 路径重构为 规范化→预检→落库 三遍，跳过条件语义不变。测试 +4，全套 107 绿。

**勾稽**：重出 8 月账 36 张 **¥83,935**（床位+护理 70,600 分毫未动；餐费
945 = 张国栋 63×15 + 12,390 = 其余 11 位老人的 fill_demo_data 演示月结行——
**这 11 位从未有点餐记录**（meals_mealorder 全表仅 171 条、自增序号 171，
排除"曾有大额点餐被删"的可能），billing 设计"无点餐读已有月结行不造行"
正好把演示数据原样带过）。全院应收 85,555→83,935，差额 1,620 = 108×15 严丝合缝。

**排障教训**：会话早期的"餐费锚点 13,320 = 888×15"是规划时的假想算术，
生产根本不存在这 888 条订单——数据核对要以 sqlite_sequence/max(id) 等
硬证据为准，别拿计划锚点当现实。admin_log 只有 1 行（改库存），也佐证
没有人工删单。

## 2026-08-24 · nursing-erp 演示数据重灌（rebuild_demo_data.py）

用户确认演示/测试全是 mock 后拍板：保留 mock 但要"真实又合理、相互勾稽"，
且**不能干扰日常点点点**。方案为分层契约——档案层（账号/员工/老人/床位/
菜品/价目/库存目录）原样保留，动态层全部重置，**派生数字一律走真实业务
代码**（generate_monthly → generate_month_bills → settle），脚本从不手写
结论。任何时候重灌都是相对当日锚点的"活数据"；固定 seed 两次运行数字
逐位一致；11 条自检断言放在 transaction.atomic 内，失败即整体回滚。

**真实感**：状态随日期走（历史已送达/今日备餐送餐中/未来已点餐）、人设
画像（固定口味+固定代点护理员+改退留痕）、周节律（实测周末退餐 15.9% ≈
3× 工作日 5.0%）、三个月出账深度（往月全额核销、当月部分核销 → 欠费
名单 13 人有层次）。四剧本全部落位：吴桂英欠费三月居榜首（¥12,840）、
张国栋自理→半护→全护按月分档出账（300/1200/2000）、杨国华上月身故
（上月账照出已核销、当月释放床位不出账、入住率 35/36）、低库存+待批采购。

**坑三则**：① `auto_now_add/auto_now` 在 bulk_create 时被 pre_save 无声
覆盖，构造参数里传的时间全部作废——下单/改退/核销时间必须 executemany
回填，且回填值存平行列表（别赌 pre_save 是否回写属性）；带 `+08:00`
偏移的串 ORM 读回已验证正确。② `DischargeRecord.save()` 新建时自动
`update(bed=None)` 释放床位（绕过 Resident.save，楼栋字符串缓存保留为
"最后已知位置"）——释放床位不要手动再放一遍。③ 出入库用 bulk_create
绕过 save() 的数量钩子，才保得住档案层库存数字（低库存剧本本就来自原值）。

**顺带治愈一个存量 bug**：重灌前抽查发现 8 月 `SUM(total)`=85,555 而三费
之和 83,935——张国栋那张单 total 列没随三费刷新重算（差 1,620=108×15）。
上轮 JOURNAL 记的"重出账 ¥83,935"实为三费合计，total 列一直是陈旧的
（又一次印证：核对要拿列的实值算，别拿语义推）。断言 4（逐单 total=
三费之和）已把该不变量钉死。

**运维通道**：settings 的 sqlite NAME 支持 `NURSING_DB` 环境变量指向
临时库——演练/验证零接触生产；脚本带 runserver 守卫（默认库运行时拒绝
并行重灌，NURSING_DB 临时库放行）。生产执行序：sqlite3 backup API 备份
→ `systemctl --user stop nursing-erp` → 重灌 → start → E2E（两账号
form+CSRF 登录、/api/ key 面对账、匿名 401）。服务由 systemd user unit
`nursing-erp.service` 托管，重启不再手工 nohup。

## 2026-08-24 · AI 财务技能接入 /api/billing/（chat 预取 + agent 侧 handler）

ERP 应收月账单 6 端点上线 + 演示数据重灌（三个月账单/欠费剧本）后，
把财务问答接到真实数据。提交 `b1bb6eb`（chat/技能）+ `e255f9d`（env 携带）。

**dl-control chat 预取**（`_skill_queries` 财务行升级）：欠费类（欠费/没交/
未缴/催缴）→ `/api/billing/arrears/`（跨月累计名单）；泛财务词（费用/结算/
缴费/账单/应收/出账）→ `/api/billing/summary/`（当月三额勾稽）；餐费/月结
仍走 meal-finance。**行序即优先级**——欠费行必须先于泛财务行（首匹配
break，"谁欠费"不能落到 summary）。`_erp_items` 从 build_app 提升为模块级
（可单测）并支持三种响应形状：分页 dict 取 items；聚合 dict（rows+汇总
标量）汇总置顶成一行再接 rows（LLM 不丢 total_outstanding——13 行小数让
LLM 自己加总会算错）；纯标量 dict 包成单行。

**agent 侧**：nursing-erp-query/handler.py +6 函数（list/summary/arrears/
generate/settle/unsettle，1:1 映射 API）；SKILL.md 补财务触发词、端点表、
欠费口径说明（arrears 跨月 vs summary 单月）；finance-query（Mock
Postgres）加废弃横幅——工作流 finance-step 的提示词让它读那个 SKILL.md，
横幅正好把 agent 导向 billing。

**部署发现（两个此前不可见的坑）**：① 运行中的 agent 容器镜像里**根本没有**
nursing-erp-query 技能目录（agent.yaml skill_list 挂着，目录不存在）——
此前 4c95e7a 联调全绿是因为 chat 走 dl-control 预取注入，agent 侧从未
真正可用；② agent 容器 env 完全没有 NURSING_ERP_*，handler 默认连
http://nursing-erp:8080（不存在的容器名）。修法：config_gen/service 加
nursing_erp_env_lines 携带通道（Feishu 凭据同款 carry-forward，密钥永不
进模板；无携带行给默认 URL `http://dato-caddy:9081`，与 dato_net 网内
ERP 网关一致）；院长/通用助手两容器手工追加两行 + docker cp 技能 +
restart。验证口径：`docker exec` 的 shell **不继承** PID 1 source 的 env
（查 `/proc/1/environ`）；exec 里测 handler 要先 `. /app/config/.env`。

**E2E（院长 wang_jianguo）**："这个月谁欠费？列出前3名" → 吴桂英
¥12,840/3个月居首的表格（与重灌面板分毫不差）；"本月应收多少？" →
104,500/66,460/38,040，LLM 自算收缴率 63.6%（22/35）。容器内 handler
实测 arrears 13 人 ¥46,675 ✓。

**顺带修复**：dl-control pyproject 加 pytest `pythonpath = ["."]`——项目
无 build-system，uv 不装自身进 venv，此前依赖某次手工 editable 安装，
`uv sync` 后全套 import 断（新克隆必炸）。另：root tests 套件在本机跑不动
（uv run 落到 miniconda 3.14 缺 argon2），test_nursing_auth.py 的
`deepseek_api_key=` 参数也已过期，账先记在这。

**遗留**：其余 13 个 agent 容器未 cp 技能目录（非财务角色；镜像重建自然
带上，skills 已在 git）；ERP `/api/meal-finance/generate/` 硬编码 15 元
仍未读 FeeRule。

## 2026-08-24 · chat 表格渲染修复（simpleMarkdown 三个叠加 bug）

用户报障：院长问"这个月谁欠费？前3名"，回答的表格表头和数据行之间多出
一行孤立的冒号（`:    :`），还夹着 `-----` 横线。Redis 里存的原始回复
（`chat_msgs:7f317a10`，经 `user_chats:u001` 索引定位）是规整的 markdown
——`|:---:|:---|` 分隔行完好，**模型输出没问题，锅在前端**：chat.html 里
手写的 simpleMarkdown 转换器三个 bug 叠加：

1. **全局 `---+` → `<!--hr-->` 跑在表格处理之前**：分隔行 `|:---:|` 里的
   `---` 先被吃成 hr 占位符，后面判"这是分隔行"的 `indexOf('---')` 永远
   落空 → 分隔行被当成数据行，渲染出一行只含冒号的单元格，每个单元格里
   还嵌着 `<hr>`（用户看到的 `:` + `-----`）。修：hr 只认"整行纯连字符"
   `^\s*-{3,}\s*$`（行锚定）。
2. **分隔行匹配后只删了行内文本、留下换行**：空行把
   `((?:<!--tr-->.*\n?)+)` 的连续性打断 → 一张表被拆成两张 `<table>`。
   修：分隔行整行连换行一起删 `^[ \t]*\|[ \t:|]*-[ \t|:-]*\|[ \t]*\n?`。
3. **表内换行活到步骤 3 的 `\n→<br>`**：`</td>` 与下一行 `<tr>` 之间插入
   游离 `<br>`——表格里这是非法 HTML，浏览器把 `<br>` foster 出表格，
   视觉上就是杂线。修：包 table 块的回调里顺手 `replace(/\n+/g, '')`。

验证：node 抠出 simpleMarkdown 直接跑真实 Redis 回复，输出单张表、表头
+3 数据行、无冒号行、无游离 `<br>`；段落 `---` 仍正常渲染 `<hr>`。
`docker restart dato-control` 后 curl `/chat`（nursing 会话）确认三条新
正则已在线上页面。历史消息无需重发——存储的原文没变，纯渲染修复，
刷新即见干净表格。

## 2026-08-24 · 餐费单价统一价目表（nursing-erp 侧小活）

清掉上条"遗留"里的硬编码债：`MealFinance.generate_monthly` 默认参数
15 元移除（billing 侧本就显式传 `FeeRule.get_meal_price()`，唯一吃默认值
的是 `/api/meal-finance/generate/`，同改读价目表）。缺行 400 指引后台
补配、响应回显单价；测试 +2（改价 20 生效 / 缺行 400 零写入），
全套 111 绿。生产价目 meal=15 → 行为零变化；零写入 E2E（不存在
resident_id）确认线上响应带回 `price_per_meal`。nursing-erp `8b10b1e`。
注意：该端点会对**全部老人** update_or_create——对无点餐老人会把
fill_demo_data 的演示月结行刷成 0，故生产只做读验证不真跑出账。

## 2026-08-24 · 入住评估→定级上线（nursing-erp 阶段二收尾）

路线图阶段二最后一块：`assessments` app 全套上线，提交 `69b9b3c`。
国标 GB/T 42195-2022 口径——26 项二级指标（4 维度 8-4-9-5，原始满分
190）→ 归一化 0-100 → 等级分段（国标常量硬编码）→ 建议护理档
（`GradeLevelMap` 配置表，缺行 fail-loud）→ `confirm()` 原子闭环：锁序
resident→assessment 防死锁，翻转 `care_level` + **自动生成关联
CareLevelChange**（from==to 也留痕、change_date=assess_date、重复确认
raise）。失智刻意不入映射——只能定级改判且原因必填。原先"改字段+建
记录"两步人工合一，billing 语义零改动（confirm 与手工改级完全同语义）。

要点三条：①总分归一化对"在用目录"实时求满分——后台调目录不腐蚀
0-100 语义；历史单总分落单上，`recalculate()` 是唯一写入口。②明细行
item FK 用 PROTECT + 只存得分（快照语义），目录改名/调上限不漂移历史。
③双评估员只记录不做工作流（国标要求 ≥1 医护），评估员1 走 StaffFkMixin。

演示剧本升级：张国栋等级时间线改走真实 create+confirm（synth_scores
目标分 55→60分2级半护 / 75→80分3级全护，±1 舍入不跨段），陈永发补录
400 天前已确认单（from==to 留痕）→ 盘点「待复评」有内容；断言 #10 钉
"每张已确认单恰关联 1 条变更行 + 补录者入待复评"。

部署序：sqlite3 backup API 备份（注意 `scripts/backup_db.py` 是给
dato-control Postgres 写的，找 pg_dump 会失败留 0 字节文件——本库一律
`sqlite3 db.sqlite3 ".backup backups/<名>.sqlite3"`）→ 停服 → 迁移 3 张
（含种子）→ 重灌 10 断言全过 → 起服 → E2E（b1_liu 看板/生命周期评估
事件/API 26 行明细/匿名 401/楼长 admin 403 与 residents 既有口径一致，
admin 三页 HTTP 级渲染走 throwaway pytest 测试库超管验过）。测试
111→129 全绿，ruff 新文件零告警。

遗留：AI 侧（chat/agent）评估只读查询未接（用户拍板后续单独做）——
`_skill_queries` 加评估行须排在"老人/健康档案"行**之前**（行序即优先
级，否则"评估"关键词被吞）；handler 侧配套读函数。

## 2026-08-24 · AI 侧评估查询接入（chat 预取 + 技能读函数；nursing-erp 50dd857）

入住评估上线的遗留项收尾。ERP 侧新增 `GET /api/assessments/review/`
三态盘点端点（rows 只含待评估/待复评，期内已评只给计数——不稀释注入
上限；聚合形状对齐 _erp_items 汇总置顶范式），本仓三处接入：

**dl-control 预取两行**（`_skill_queries`）：盘点类（待评估/复评/评估
盘点）→ review 端点，泛评估词（评估/定级/能力等级/护理等级）→ 评估单
列表。**排位比计划多踩一坑**：不只 resident 行（"老人"），logistics 行
的"盘点"同样会吞"评估盘点"——两行一并提到 logistics **之前**；行内
review 先于泛评估（"待评估"含子串"评估"）。测试 +3（指向/三连行序钉
i_review < i_generic < i_logi < i_res），18 过全套 84 过。

**技能 +3 读函数**：nursing-erp-query/handler.py 的 list_assessments /
get_assessment（26 行明细）/ assessment_review，1:1 映射 API；SKILL.md
补触发词、端点表与口径注（总分越高越差、12 个月复评、"谁该复评"用
review、"某人结果"用列表/详情）。

**部署**：dato-control `--force-recreate --build`（容器内 grep 验新行）；
技能 docker cp 进院长/通用助手两容器 + restart（其余 13 个无技能目录，
镜像重建自然带上）。

**E2E（院长 wang_jianguo，chat.eldcare.cn 同源）**："谁该复评了？"→
32/1/2 与线上分毫不差，陈永发（1号楼103，2025-07-20 待复评）单列提示；
"张国栋评估结果"→ 两次评估 60/80 分、半护→全护定级时间线全对；"做个
评估盘点"→ 评估报告而非库存（行序修复的直接验证），"库存盘点/尿不湿"
→ 物流无回归。

## 2026-08-24 · 评估页面交互改版 + admin 侧边栏补床位管理组（nursing-erp）

用户反馈单页"一拉到底很奇怪"，对齐周点餐交互拆分评估页；随后追加
详情页与侧边栏补组。四个提交：`1a8bddc`（拆页）→ `bad7af2`（按钮
下划线）→ `219db33`（只读详情）→ `9310082`（床位组）。

**看板/工作台拆分**（`1a8bddc`）：/assessments/ 盘点表服务端分页 20/页
+ 状态筛选 chip（带计数）+ 姓名搜索，行尾「评估/复评 ›」；26 项录入
拆到独立工作台 /assessments/new/?resident_id=（老人头 + 实时总分角标
——json_script 注入满分/BANDS/GRADE_LABELS/GradeLevelMap，纯前端
预览，落库仍以 recalculate() 为准；Math.round 与银行家舍入在满分 190
下无 .5 端点恒一致）；建单/定级成功回看板 toast（?created=/?confirmed=，
escapejs 防注入）。

**两个小坑**：① .btn 类此前只服务 <button>（周点餐分页即 button），
改版用 <a class="btn"> 后锚点带出浏览器默认下划线——类级补
color:inherit;text-decoration:none 归一（`bad7af2`），对既有 button
零副作用；② json_script 渲染带 type="application/json"，测试里 split
解析要跨过该属性。

**只读详情页**（`219db33`）：/assessments/<id>/ 四格（总分/等级/建议/
定级含改判标）+ 26 项分维度明细（逐项 分数/上限 + 比例条 + 维度小计）；
三入口=定级历史行/待定级行「详情 ›」（定级前过目明细的口子）+ 工作台
「上次评估 ›」。不存在回看板（与 API 404 语义分层）。

**admin 侧边栏补「床位管理」组**（`9310082`）：UNFOLD SIDEBAR 是
点名制（show_all_applications=False），漏列=整组静默隐藏——beds 四级
台账 admin 早已注册、/beds/ 看板也在轻页面顶栏，唯独后台侧边栏漏配
（用户发现）。补组置「老人照护」后：床位看板 + 楼栋/楼层/房间/床位
台账链接。test_beds +1 回归钉——点名制配置此前无任何测试盯着。另记：
**点名制配置漂移**是"功能在但无入口"的一类根因，再遇到同类现象优先
查这份名单。

测试 129→134 全绿（评估 +5、床位 +1），ruff 手写文件零新增告警。
## 2026-08-24 · 家属端上线（Q6 重启：ERP 轻量页 + AI 家属对话；nursing-erp 3b0313e）

用户重启 Q6（08-21 定稿"暂不做"）并拍板形态 C：ERP 家属轻量页 + AI 对话
入口都要；健康档案（诊断+过敏+用药）对家属全开放；家属可代点餐/退餐。
id_card、他人数据、Resident.notes、异常上报永不开放（测试钉）。

**架构**：ERP 是家属身份唯一真源（FamilyMember 手机号账号 + token +
FamilyBinding 绑定）；AI 侧零账号表——`/auth/family-login` 委托 ERP
`POST /api/family/auth/` 换 token+绑定清单写 session（role="family"，
family_token/residents 两字段扩进 SessionStore），家属对话走 dl-control
自己的预取+直连 LLM 路径（agent 容器零隔离，有意不进）。

**ERP 侧**（细节见 nursing-erp/docs/业务完整度评估与路线图.md 落地记录）：
三层安全闭合（erp_auth 拒家属 session / family_auth 只认家属 /
staff_required×12 员工页）；家属 API 六端点 + 批量点餐（归属服务端生成
`家属-姓名（关系）`）+ 退餐留痕；轻量页 7 模板；admin 开通/action/台账；
seed_family 36 账号（王丽华双绑 showcase，`--seed-family-only` 外科手术
模式与常驻 runserver 并行安全）。测试 134→179。

**AI 侧三个易错点**：
① **角色门分流**——`_NURSING_ROLES`（middleware+main 两处）刻意不加
"family"（家属进 dashboard/alerts 等员工路由一律 302），只在 main.py 新增
`_CHAT_ALLOWED = _NURSING_ROLES | {"family"}` 换掉**恰好六处**对话门
（chat 页/会话 CRUD×4/发消息）；测试用源码计数钉死 6/8 分流（
test_main_role_gate_split），新增路由时同步改钉。
② **家属预取只有 API 行无 SQL 行**——`_family_skill_queries()` 全走
`/api/family/*`（X-Family-Token 头，token 是 ASCII hex 免编码，与
X-Building 互斥不叠加），本侧库没有家属可看数据；行序：账单<吃饭<健康<
泛近况兜底 overview。周报触发 `not is_family and ...` 跳过（员工工作流）；
agent 路由 ROLE_TO_AGENT 无 family 键自然落空。
③ **直连 LLM 超时 60→90s**——家属首问 E2E 真实超时一次（kimi-k2.6 推理
+整周菜单注入，实测 42s 贴边），空异常文本是 httpx ReadTimeout 的
str() 为空，排障别被"AI 服务暂时不可用："后面没字骗到；同日另见
"[Errno -2] Name or service not known"=瞬时 DNS 抖动（容器内嵌
DNS → 宿主 systemd-resolved 127.0.0.53 链路，复测 30/30 正常），
已给直连 LLM 加 AsyncHTTPTransport(retries=1) 连接层重试。

**E2E（生产，chat.eldcare.cn）**：王丽华（138…0001/123456，双绑张国栋+
李秀兰）登录 302→/chat；「这周吃饭情况」→ 双老人真实订单含少盐备注；
「3号楼有哪些老人」→ 只报自家名单不列他人；「帮我退餐」→ 拒代操作并
引导「家属服务」页（prompt 明示只查不代改）。安全四针全过：跨家 token
读非绑定 404 / 员工 session 进 /api/family/ 401 / 家属 session 进
/api/residents/ 401 / 家属进 /billing/ 302 /family/。

**坑**：ERP 家属页登录表单字段是 `phone` 不是 `username`（curl 冒烟曾
因此 200 重渲染误判成"登录坏了"）；`docker exec dato-control python -c`
直接实例化 Settings 会缺 DB_URL 等（入口注入），裸用 os.environ 即可。

测试 dl-control 84→101（+17 家属管线：headers 回归钉/查询行序/prompt
三分支/try_family_login 四路 fake httpx+redis），ruff 双仓基线持平
（83/12）。部署：`docker compose --project-name dato --project-directory
infra --env-file infra/.env up -d --force-recreate --build dato-control`。

## 2026-08-25 · 运营大屏增强（餐食/点餐/护理等级/评估/入住率五组件）

用户反馈 dashboard 数据太少，选定五项（今日餐食/今日点餐动态/护理等级
分布/评估待办/床位入住率；点餐动态按用户拍板独立成面板、餐食面板纯菜单）。
全部数据走 ERP 既有 /api/*（week-menu、meal-orders、residents、
assessments/review、beds/occupancy），零 ERP 侧改动。

**四个实测出的坑**：
① **week-menu 的 `day` 是中文"周二"不是日期**——今日过滤键
`'周' + '一二三四五六日'[date.today().weekday()]`（_today_cn）。
② **meal-orders 分页上限 50**——全院 96 单超页，按餐次分三次拉
（每餐 ≤36）再聚（_order_stats）；status 口径：cancelled 不进
total 但 by_status 保留（大屏要看得见退改痕迹），特殊餐只数未退单
（退了的特殊诉求不该再让厨房操心）。
③ **楼长会话 scope 自动收窄五组件**——X-Building 一路透传，b1_liu 实测：
点餐 17 单（全院 96）/护理等级 6 人（全院 36）/入住率 100%（=1号楼
满床，非全院），副行语义随之变"本楼"，无需代码区分。
④ **ERP 挂掉单组件降级**——五个拉取各自 try/except 包在同一个
AsyncClient 里，缺谁谁空（前端显示"未发布/暂无"），大屏不炸。

**前端**：KPI 行 4→5 卡（+评估待办 = 待首评+到期复评，副行拆两数；
在院老人卡加入住率副行"入住率 97% · 空余 2 床"）；新底排三面板
（早/午/晚三列菜单 ｜ 餐次×状态 chips+特殊餐徽章 ｜ echarts 环形饼图，
档位定序 自理→半护→全护→失智→特护、未定兜底永远最后）。
注意 echarts 实例数据缺席时 clear() 即可、别置 null（30s 自愈刷新）。

测试 dl-control 101→111（+10 helper 单测：七天星期映射/菜单过滤排序/
订单聚合口径/等级定序/入住率取整+除零/五键装配源码钉），ruff 基线 83
持平。部署同一条 compose 命令；E2E b1_liu 会话五键全通+页面/静态资源
10/10 命中。

**同日补记（菜单排版升级）**：初版今日餐食是三列纯文字，用户嫌素。
改为"菜单卡"样式：三餐色带列头（早橙/午绿/晚紫 + 道数），菜品按
ERP category（主食/汤/素菜/荤菜/小菜，实测就这五个值）配色的圆角菜签
（荤红/素绿/主食米黄/汤蓝/小菜明黄，未知中性兜底），面板底部色点图例。
`_today_menu` 契约从 dishes:[菜名] 改为 dishes:[{name, category}]，
测试同步。

**同日再补（护理等级饼图"…9"根治）**：用户截图实锤——左下档位标签
渲染成裸省略号"…\n9人"，"全护"两字整个没了（半护标签也被挤成饼下
错位窄列）。像素级定位 + 翻 echarts.min.js(5.5.0) 源码定位根因：**饼系
列外置标签默认 `overflow:"truncate"`**（series 默认表里写死），窄面板下
标签可用宽度塌到放不下两个汉字就截成光秃秃省略号。第一次修（2d7c1e9
的 alignTo:'edge'/labelLine/中心总数/图例带人数）只是给了更多空间、没
关截断本身——补 `label.overflow:'none'` 根治。教训：**视觉模型的
"没看到省略号"不可信，417×384 小图要切区域放大逐像素核对**；排查
echarts 渲染怪象直接 grep min.js 的默认配置表。

**同日三补（大屏主题分线重排）**：用户问布局想法，给了三方案（主题分线
/驾驶舱右栏/最小微调），拍板"主题分线"。动机：两行面板都是"当日动态×
慢变画像"混排；库存表整宽横条切节奏；交接班横幅埋中部；告警 KPI 排最右
（左上才是视觉黄金位）；各楼栋柱状图对楼长作用域只剩一根柱不配整格。
落位：KPI 序 在院老人→**告警**→评估→当班→库存；交接班横幅上移到 KPI
行下方（时间锚点置顶）；中排照护线 重点老人|完成率|**护理等级**（两环
图成对）；底排后勤线 餐食|点餐|**库存表**（flex+滚动，行高跟菜单面板）；
各楼栋在院人数降格为在院老人卡内迷你条形（纯 CSS 竖条，32px 满格像素
定高防 flex 压缩失真，默认 display:none 防首帧空块，title 悬停看全值），
buildingChart 实例/resize/updateBuildingChart 全删。纯模板+CSS 零后端，
bind-mount 即时生效；JS 用 node --check 验过语法，111 测试不动全绿。

**同日四补（库存表回滚整宽）**：库存表塞进后勤线 1fr 窄格后四列可读性
差，用户反馈"还是之前的格式好"——恢复整宽横条（后勤线收缩为餐食|点餐
两格 1.3fr/1fr，库存表降到两面板下方整宽）。教训：4 列表格不适合 <400px
列宽，整宽表格的可读性 > 布局的对称性。
