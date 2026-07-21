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