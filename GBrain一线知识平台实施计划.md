# GBrain 一线知识平台实施计划

> **日期：** 2026-07-08
> **版本：** v4（复用 dl-cognee 嵌入 + reranker，OpenAI 兼容协议）
> **相关记忆：** [[cognee-phase2]]、[[pipeline-three-platforms]]

---

## 背景

dato 知识库客户需求升级：从"为内容管线做检索"变为"面向一线人员的知识平台"。要求：

1. **面向一线人员** — 提供简洁易用的查询界面
2. **Lint 校验** — 知识库规范检查、质量门禁
3. **高标准规格文档入库** — Schema 约束、结构化提取

原有 cognee RAG 系统（纯向量搜索，无 Schema/Lint，无管理后台）无法满足。引入 **GBrain**（Y Combinator CEO Garry Tan 的知识大脑系统）作为一线团队知识平台，**cognee 保持不动**继续服务内容管线。

> **关于 ACL：** GBrain 原生支持 OAuth 2.1 多用户权限隔离，但当前阶段一线团队规模小、知识库为公共资产，没有必要做人与人之间的 ACL。简化为团队共享一个 Basic Auth 密码，等真有权限隔离需求时再加。

---

## 总体架构

```
一线人员 ─→ 管理后台网页 (浏览器) ─→ Caddy (Basic Auth) ─→ GBrain HTTP MCP
                                                                  │
                                                    ┌─────────────┴─────────────┐
                                                    │                           │
                                              dl-cognee (嵌入)     dl-cognee-reranker (精排)
                                          POST /v1/embeddings     POST /v1/rerank
                                                    │                           │
                                              ┌─────┴───────────────────────────┴─────┐
                                              │                                     │
                                        cognee_hf_models volume              Postgres + pgvector
                                        (bge-m3 + reranker, 已部署)        ┌─────┴─────┐
                                                                          │           │
                                                                     GBrain 库    cognee 库
                                                                                    │
                                                                              内容管线（不变）
```

### 与 v3 的关键变化

| 维度 | v3（旧） | v4（新） | 原因 |
|:----:|:--------:|:--------:|------|
| **嵌入端点** | 自创 `/v1/embed` 格式 | **标准 OpenAI `/v1/embeddings`** | GBrain 原生支持 `llama-server` recipe（OpenAI 兼容），无需适配层 |
| **Reranker** | "先不加，后续可选" | **直接启用，复用 dl-cognee-reranker** | GBrain 原生支持 `llama-server-reranker` recipe，只需加一个 `/v1/rerank` 端点 |
| **GBrain init** | `--embedding-api http://...` | **`--embedding-model llama-server:bge-m3`** | 使用 GBrain 内置的 recipe，无需自定义嵌入 API |
| **代码改动** | 只改 dl-cognee routes.py | **同时改 dl-cognee + dl-cognee-reranker routes** | 两端都加 OpenAI 兼容端点 |
| **GBrain 配置项** | 需要自定义 `--embedding-api` 参数 | **标准 provider 配置**（`provider_base_urls`） | GBrain 已有完整的 recipe 支持 |

### 核心原则

| 维度 | GBrain（新增） | cognee（保留） |
|:----:|:-------------:|:--------------:|
| 服务对象 | 一线人员 | 内容管线（自动执行） |
| 搜索能力 | 混合搜索 + 图谱 + 合成 + reranker | 向量搜索 + reranker（已 v2 升级） |
| Schema/Lint | ✅ 内置 | ❌ 无 |
| 管理后台 | ✅ 自带 SPA | ❌ 无 |
| 用户认证 | Basic Auth（共享账号） | Bearer Token（Agent 级别） |
| 嵌入模型 | **复用 dl-cognee bge-m3** | bge-m3（1024-dim，FlagEmbedding） |
| 精排模型 | **复用 dl-cognee-reranker bge-reranker-v2-m3** | bge-reranker-v2-m3 |
| GBrain↔cognee | **共存**，GBrain 单向同步到 cognee | 管线不改代码，零风险 |
| 代码改动 | **极少**（只加两个兼容端点） | **极少**（只加一个兼容端点） |

### 关键设计决策

| 决策 | 选择 | 原因 |
|:----:|:----:|------|
| 数据库 | **Postgres**（直连已有 pgvector） | PGLite 并发不够，dato 已有 Postgres 集群 |
| 嵌入模型 | **复用 cognee 的 bge-m3**（`/v1/embeddings`） | GBrain 原生 `llama-server` recipe，零适配 |
| Reranker | **复用 cognee-reranker 的 bge-reranker-v2-m3**（`/v1/rerank`） | GBrain 原生 `llama-server-reranker` recipe，零适配 |
| GBrain↔cognee | **共存**，GBrain 单向同步到 cognee | 管线不改代码，零风险 |
| 用户认证 | **Basic Auth**（团队共享密码） | 当前无需人与人 ACL，先简单跑起来 |
| 用户界面 | **管理后台网页**为主 | 一线人员打开浏览器就能用，零安装 |
| 构建方式 | Docker 多阶段构建，锁定 GBrain commit | 可复现，离线可部署 |

---

## GBrain ↔ dl-cognee 协议详解

### 嵌入端点 (`dl-cognee:8080`)

**端点：** `POST /v1/embeddings`

**GBrain 发来的请求：**
```json
{
  "input": "待嵌入的文本",
  "model": "bge-m3",
  "dimensions": 1024
}
```
或批量：
```json
{
  "input": ["文本1", "文本2"],
  "model": "bge-m3",
  "dimensions": 1024
}
```
**认证：** Authorization: Bearer `{DL_INTERNAL_API_KEY}`

**dl-cognee 返回：**
```json
{
  "object": "list",
  "data": [
    {"object": "embedding", "index": 0, "embedding": [0.123, ...]},
    {"object": "embedding", "index": 1, "embedding": [0.456, ...]}
  ],
  "model": "bge-m3",
  "usage": {"prompt_tokens": 42, "total_tokens": 42}
}
```

### Reranker 端点 (`dl-cognee-reranker:8080`)

**端点：** `POST /v1/rerank`

**GBrain 发来的请求：**
```json
{
  "model": "bge-reranker-v2-m3",
  "query": "搜索查询",
  "documents": ["候选文档1", "候选文档2", "候选文档3"],
  "top_n": 5
}
```

**dl-cognee-reranker 返回：**
```json
{
  "results": [
    {"index": 1, "relevance_score": 0.95},
    {"index": 0, "relevance_score": 0.87},
    {"index": 2, "relevance_score": 0.23}
  ]
}
```

---

## 实施计划：5 个阶段（约 4.5 周）

### 第一阶段：基础设施搭建（第 1 周）

**目标：** dl-cognee 嵌入+reranker API 就绪 → GBrain Postgres 部署 → GBrain 容器启动 → 能搜索

#### 1.1 新增 dl-cognee `POST /v1/embeddings`（OpenAI 兼容）

**文件：** `dl-cognee/dl_cognee/routes.py`

在现有 `make_router()` 中新增端点。这是 GBrain 嵌入调用的入口：

```python
class OpenAIEmbedRequest(BaseModel):
    input: str | list[str]
    model: str = "BAAI/bge-m3"
    dimensions: int | None = None

@router.post("/embeddings")
async def openai_embed(request: Request, body: OpenAIEmbedRequest):
    """OpenAI-compatible /v1/embeddings endpoint (for GBrain)."""
    app_state = request.app.state
    # Verify internal token.
    auth_header = request.headers.get("Authorization", "")
    expected = app_state.settings.dl_internal_api_key.get_secret_value()
    if not auth_header.startswith("Bearer ") or \
       not secrets.compare_digest(auth_header.removeprefix("Bearer ").strip().encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="invalid token")

    texts = [body.input] if isinstance(body.input, str) else body.input
    embeddings = app_state.embedder.embed_batch(texts)
    data = [
        {"object": "embedding", "index": i, "embedding": emb}
        for i, emb in enumerate(embeddings)
    ]
    return {
        "object": "list",
        "data": data,
        "model": body.model,
        "usage": {"prompt_tokens": len("".join(texts)), "total_tokens": len("".join(texts))},
    }
```

> **认证策略：** 使用 `DL_INTERNAL_API_KEY`（Bearer token）。GBrain 在配置 `provider_base_urls.llama-server` 时不需要指定 API key（可选 auth），llama-server recipe 支持 `LLAMA_SERVER_API_KEY` 环境变量。

#### 1.2 新增 dl-cognee-reranker `POST /v1/rerank`（GBrain 兼容）

**文件：** `dl-cognee-reranker/dl_cognee_reranker/main.py`

新增端点，接收 GBrain 格式，映射到内部 reranker：

```python
class RerankRequestGBrain(BaseModel):
    model: str = "BAAI/bge-reranker-v2-m3"
    query: str
    documents: list[str]
    top_n: int | None = None

class RerankResultGBrain(BaseModel):
    index: int
    relevance_score: float

class RerankResponseGBrain(BaseModel):
    results: list[RerankResultGBrain]

@app.post("/v1/rerank")
async def rerank_gbrain(req: RerankRequestGBrain) -> RerankResponseGBrain:
    """GBrain-compatible /v1/rerank endpoint (OpenAI-style)."""
    reranker: Reranker = app.state.reranker
    scores = reranker.rerank(req.query, req.documents)
    paired = list(zip(range(len(req.documents)), scores))
    paired.sort(key=lambda x: x[1], reverse=True)
    top_n = min(req.top_n or len(req.documents), len(req.documents))
    top = paired[:top_n]
    return RerankResponseGBrain(
        results=[
            RerankResultGBrain(index=idx, relevance_score=float(s))
            for idx, s in top
        ]
    )
```

#### 1.3 创建 GBrain Postgres 数据库

**文件：** `infra/postgres/init/02-gbrain-db.sh`

创建 `gbrain` 数据库和 `gbrain_app` 用户，启用 pgvector 扩展：

```sql
CREATE DATABASE gbrain;
CREATE USER gbrain_app WITH PASSWORD '${DL_GBRAIN_PG_PASSWORD}';
GRANT ALL PRIVILEGES ON DATABASE gbrain TO gbrain_app;
\c gbrain
CREATE EXTENSION IF NOT EXISTS vector;
GRANT ALL ON SCHEMA public TO gbrain_app;
```

#### 1.4 创建 GBrain Dockerfile

**文件：** `dl-gbrain/Dockerfile`

多阶段构建：基于 `oven/bun` 编译 GBrain 静态二进制，复制到 distroless 运行镜像：

```dockerfile
FROM oven/bun:1 AS builder
# 克隆 GBrain 源码（锁定 commit）→ bun install → bun run build:admin → bun run build
FROM gcr.io/distroless/base-debian12:nonroot
COPY --from=builder /app/gbrain/bin/gbrain /usr/local/bin/gbrain
```

#### 1.5 创建启动入口

**文件：** `dl-gbrain/entrypoint.sh`

```bash
#!/bin/sh
set -eu

# 等待 Postgres 就绪
until pg_isready -h dato-postgres -U gbrain_app; do sleep 1; done

# 首次运行：用 llama-server recipe 指向 dl-cognee
gbrain init --engine postgres \
  --embedding-model llama-server:bge-m3 \
  --embedding-dimensions 1024

# 配置 dl-cognee 为嵌入提供方
gbrain config set provider_base_urls.llama-server http://dl-cognee:8080/v1

# （可选）配置 dl-cognee-reranker
gbrain config set search.reranker.model llama-server-reranker:bge-reranker-v2-m3
gbrain config set search.reranker.enabled true
gbrain config set provider_base_urls.llama-server-reranker http://dl-cognee-reranker:8080/v1

# 配置搜索模式
gbrain config set search.mode balanced

# 启动
exec gbrain serve --http --port 8080 --bind 0.0.0.0
```

> **注意：** GBrain 的 `llama-server` recipe 支持 `LLAMA_SERVER_API_KEY` 可选认证。如果需要认证，可在容器环境变量设 `LLAMA_SERVER_API_KEY`，GBrain 会自动作为 Bearer token 转发。

#### 1.6 更新 docker-compose.yml

**修改：** `infra/docker-compose.yml`

新增 `dl-gbrain` 服务：

```yaml
dl-gbrain:
    build:
      context: ../dl-gbrain
    image: dl-gbrain:latest
    container_name: dl-gbrain
    restart: unless-stopped
    expose:
      - "8080"
    environment:
      DATABASE_URL: postgresql://gbrain_app:${DL_GBRAIN_PG_PASSWORD}@dato-postgres:5432/gbrain
      TZ: ${TIMEZONE:-Asia/Shanghai}
      # 可选：dl-cognee 需要 Auth 时的 API key
      # LLAMA_SERVER_API_KEY: ${DL_INTERNAL_API_KEY}
    networks:
      - dato_net
    depends_on:
      dato-postgres:
        condition: service_healthy
      dl-cognee:
        condition: service_started
```

> **不需要** `dl-gbrain-init` 一次性容器 —— `entrypoint.sh` 中的 `gbrain init` 是幂等的（safe to re-run）。

#### 1.7 更新 Caddyfile

**修改：** `infra/Caddyfile`

新增 `/gbrain*` 反向代理路由，管理后台加 Basic Auth 保护。

```caddy
@gbrain-admin path /gbrain/admin*
handle @gbrain-admin {
    basicauth {
        frontline $2a$14$...  # 团队共享账号
    }
    reverse_proxy dl-gbrain:8080
}

@gbrain path /gbrain*
handle @gbrain {
    reverse_proxy dl-gbrain:8080
}
```

#### 1.8 更新 Env 模板

**修改：** `infra/.env.example`

新增环境变量：

| 变量 | 说明 | 默认值 | 必填 |
|:-----|:-----|:-------:|:----:|
| `DL_GBRAIN_PG_PASSWORD` | GBrain Postgres 用户密码 | - | ✅ |
| `DL_GBRAIN_AUTH_USER` | 管理后台用户名 | `frontline` | ❌ |
| `DL_GBRAIN_AUTH_PASS` | 管理后台密码 | `gbrain2026` | ❌ |

> **已移除（与 v2 对比）：** `DL_GBRAIN_OLLAMA_BASE_URL`（不需要），`DL_GBRAIN_EMBED_URL`（由 GBrain recipe 管理）

#### 1.9 更新 Makefile

**修改：** `Makefile`

新增 `gbrain-build`、`gbrain-logs`、`gbrain-ps` 目标。

#### 1.10 验证

```bash
# 验证 dl-cognee OpenAI 嵌入端点
curl -X POST http://dl-cognee:8080/v1/embeddings \
  -H "Authorization: Bearer ${DL_INTERNAL_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"input": "测试", "model": "bge-m3", "dimensions": 1024}' \
  | jq '.data[0].embedding | length'  # 应返回 1024

# 验证 dl-cognee-reranker GBrain 兼容端点
curl -X POST http://dl-cognee-reranker:8080/v1/rerank \
  -H "Content-Type: application/json" \
  -d '{"model":"bge-reranker-v2-m3","query":"test","documents":["a","b"],"top_n":2}' \
  | jq '.results | length'  # 应返回 2

# 验证 GBrain
docker compose ps | grep gbrain
docker exec dato-gbrain gbrain doctor --json
docker exec dato-gbrain gbrain search "测试"
```

---

### 第二阶段：数据迁移 + 搜索优化（第 2 周）

**目标：** cognee 存量知识完整迁移，搜索质量基准达标

#### 2.1 导出 cognee 存量知识

**文件：** `dl-gbrain/scripts/export_cognee.sh`

从 Postgres 的 `cognee.documents` 表导出 `company_knowledge` 等库的数据为 markdown 文件。

#### 2.2 导入 GBrain

```bash
docker cp /tmp/cognee-export dato-gbrain:/tmp/cognee-export
gbrain import /tmp/cognee-export --source company-knowledge --yes
# ⚠️ 不需要 gbrain embed！GBrain 自动调用 dl-cognee /v1/embeddings
gbrain stats  # 验证
```

#### 2.3 配置搜索模式

```bash
gbrain config set search.mode balanced
gbrain search modes  # 验证
```

#### 2.4 搜索质量基准测试

**文件：** `dl-gbrain/scripts/benchmark_search.py`

编写测试查询（覆盖产品规格、品牌信息、FAQ 等场景），比较 GBrain vs cognee 搜索结果。

---

### 第三阶段：Schema + 规范入库（第 3 周）

**目标：** Schema Pack 定义 + Lint 门禁 + 规格文档结构化入库

#### 3.1 创建企业 Schema Pack

**文件：** `dl-gbrain/scripts/schema-pack-company.yaml`

定义 6 种知识类型：

| 类型 | 前缀 | 说明 | Lint 规则 |
|:----:|:----:|------|:---------:|
| `product-spec` | `specs/` | 产品规格/技术参数 | 必须含参数表 + 版本号 |
| `standard` | `standards/` | 操作规范/质检标准 | 必须含生效日期 |
| `faq` | `faqs/` | 常见问题解答 | 必须符合 Q&A 格式 |
| `brand` | `brand/` | 品牌资料/定位/VI | 必须含品牌名 |
| `training` | `training/` | 培训文档/操作手册 | 必须含受众 |
| `policy` | `policy/` | 公司政策/制度 | 必须含部门 + 生效日期 |

#### 3.2 激活 Schema Pack

```bash
gbrain schema use company-knowledge --from /tmp/schema-pack-company.yaml
gbrain schema active
```

#### 3.3 定义 Lint 校验规则

**文件：** `dl-gbrain/scripts/lint_rules.yaml`

| 规则 | 严重度 | 检查内容 |
|:----:|:------:|---------|
| `frontmatter-complete` | error | frontmatter 必须含 title 和 type |
| `no-absolute-urls` | warning | 文档不应含外部 URL |
| `chinese-language` | warning | 主体必须为中文 |
| `no-template-placeholders` | error | 不应残留 `{{xxx}}` 占位符 |
| `section-headers` | warning | 应有至少 2 个 `##` 标题 |

#### 3.4 批量导入规格文档

```bash
mkdir -p /data/brain-repo/company-knowledge/{specs,standards,faqs,brand,training,policy}
gbrain import /data/brain-repo/company-knowledge --source company-knowledge --yes
# 嵌入自动由 dl-cognee /v1/embeddings 完成
gbrain schema lint --all --json > /tmp/lint_report.json
```

#### 3.5 创建文档质量门禁

**文件：** `dl-gbrain/scripts/lint_gate.sh`

在文档入库前自动运行 lint 检查，有 error 级别问题则拒绝入库。

---

### 第四阶段：管理后台上线 + Agent 集成（第 4 周）

**目标：** 一线人员可通过浏览器搜索知识库，Agent Manager 可通过飞书查询

#### 4.1 Caddy Basic Auth + 反向代理

**已在第一阶段 1.7 完成。**

#### 4.2 管理后台验证

打开浏览器访问 `https://<host>:9443/gbrain/admin`，用共享账号登录后验证可正常搜索。

#### 4.3 创建 gbrain-mcp SKILL.md

**文件：** `openclaw/skills/gbrain-mcp/SKILL.md`

定义 `gbrain.search` 和 `gbrain.think` 工具，通过 HTTP MCP 调用 GBrain。

#### 4.4 创建 handler.py

**文件：** `openclaw/skills/gbrain-mcp/handler.py`

Python HTTP shim（与 cognee skill 相同模式），复用 `DL_INTERNAL_TOKEN` 认证。

#### 4.5 分配技能给 Agent Manager

**修改：** `dl-control/precreated_agents/agent-manager/agent.yaml`

在 `skill_list` 追加 `gbrain-mcp`。

---

### 第五阶段：收尾（第 5 周）

**目标：** 知识同步、运营流程、文档交付

#### 5.1 知识同步脚本

**文件：** `dl-gbrain/scripts/sync_to_cognee.sh`

定时从 GBrain 导出最新变更，同步到 cognee 的 `company_knowledge` 库，确保管线能搜到最新知识。

#### 5.2 运营流程建立

| 频率 | 任务 | 脚本 |
|:----:|:----:|:----:|
| 每日 08:00 | 健康检查 + 飞书通知 | `daily_health.sh` |
| 每周一 09:00 | Lint 质量报告 | `weekly_lint.sh` |
| 每周五 14:00 | 矛盾检测 | `contradiction_check.sh` |

#### 5.3 操作手册

**文件：** `dl-gbrain/docs/OPERATIONS.md`

涵盖：启动/停止/重启、日常健康检查、知识导入流程、Schema Pack 更新、灾难恢复、常见问题排错。

#### 5.4 用户指南

**文件：** `dl-gbrain/docs/USER_GUIDE.md`

面向一线人员的搜索使用说明：浏览器访问、搜索方法、知识类型说明、查询示例、反馈渠道。

#### 5.5 压力测试

**文件：** `dl-gbrain/scripts/stress_test.py`

50 并发异步查询，测量 P50/P99 延迟。

#### 5.6 灾难恢复方案

**文件：** `dl-gbrain/docs/DR.md`

| 场景 | 恢复方式 |
|:----:|---------|
| 容器崩溃 | `docker compose restart dl-gbrain`（数据在 Postgres） |
| Postgres 损坏 | 从备份恢复 gbrain 库 + `gbrain reindex --all --yes` |
| 完全重建 | `gbrain init --force` + `gbrain import`（嵌入自动通过 dl-cognee） |
| 备份策略 | Postgres pg_dump（每日）+ brain repo（Git） |

---

## 文件清单

### 新建文件（20 个）

| 文件 | 阶段 | 说明 |
|:----|:----:|:----|
| `dl-gbrain/Dockerfile` | 一 | GBrain 服务容器镜像（多阶段构建） |
| `dl-gbrain/entrypoint.sh` | 一 | 启动入口（初始化 + 启动） |
| `dl-gbrain/scripts/export_cognee.sh` | 二 | cognee 存量数据导出脚本 |
| `dl-gbrain/scripts/schema-pack-company.yaml` | 三 | 企业知识 Schema Pack |
| `dl-gbrain/scripts/lint_rules.yaml` | 三 | Lint 校验规则 |
| `dl-gbrain/scripts/lint_gate.sh` | 三 | 文档入库质量门禁 |
| `dl-gbrain/scripts/benchmark_search.py` | 二 | 搜索质量基准测试 |
| `dl-gbrain/scripts/stress_test.py` | 五 | 压力测试 |
| `dl-gbrain/scripts/sync_to_cognee.sh` | 五 | GBrain→cognee 知识同步 |
| `dl-gbrain/scripts/ops/daily_health.sh` | 五 | 每日健康检查 |
| `dl-gbrain/scripts/ops/weekly_lint.sh` | 五 | 每周 Lint 报告 |
| `dl-gbrain/scripts/ops/contradiction_check.sh` | 五 | 矛盾检测 |
| `dl-gbrain/docs/OPERATIONS.md` | 五 | 操作手册 |
| `dl-gbrain/docs/USER_GUIDE.md` | 五 | 用户指南 |
| `dl-gbrain/docs/DR.md` | 五 | 灾难恢复方案 |
| `infra/postgres/init/02-gbrain-db.sh` | 一 | Postgres init 脚本 |
| `openclaw/skills/gbrain-mcp/SKILL.md` | 四 | Agent Manager 查询技能 |
| `openclaw/skills/gbrain-mcp/handler.py` | 四 | Python HTTP shim |
| `infra/.env.example` | 一 | 环境变量模板（追加内容） |
| `Makefile` | 一 | Build/Init 目标（追加内容） |

### 修改文件（7 个）

| 文件 | 阶段 | 变更 |
|:----|:----:|:----|
| `dl-cognee/dl_cognee/routes.py` | 一 | **新增** `POST /v1/embeddings`（OpenAI 兼容） |
| `dl-cognee-reranker/dl_cognee_reranker/main.py` | 一 | **新增** `POST /v1/rerank`（GBrain 兼容格式） |
| `infra/docker-compose.yml` | 一 | 新增 dl-gbrain 服务 |
| `infra/Caddyfile` | 一、四 | 新增 `/gbrain*` 路由 + Basic Auth |
| `infra/.env.example` | 一 | 新增 GBrain 环境变量 |
| `Makefile` | 一 | 新增 gbrain 构建目标 |
| `dl-control/precreated_agents/agent-manager/agent.yaml` | 四 | 新增 gbrain-mcp 技能 |

> **对比 v2 移除：** `dl-llm-local/entrypoint.sh` 不再需要修改（无需 Ollama）。\
> **对比 v3 新增：** `dl-cognee-reranker/main.py` 加 `/v1/rerank` 端点。

---

## 依赖关系图

```
第一阶段 (基础设施)
  1.1 (dl-cognee /v1/embeddings) ← cognee 已有 bge-m3 模型
  1.2 (dl-cognee-reranker /v1/rerank) ← 已有 bge-reranker 模型
  1.3 (Postgres DB创建)
    ├→ 1.6 (docker-compose)
    │   ├→ 1.7 (Caddyfile)
    │   ├→ 1.8 (.env.example)
    │   └→ 1.9 (Makefile)
    └→ 1.4 (Dockerfile) → 1.5 (entrypoint) → 1.6
  (1.1, 1.2, 1.3 可并行)

第二阶段 (数据迁移)
  2.1 (导出 cognee) → 2.2 (导入 GBrain) → 2.3 (搜索配置)
                                              └→ 2.4 (基准测试)

第三阶段 (Schema + Lint)
  3.1 (Schema Pack) → 3.2 (激活) ─→ 3.4 (批量导入)
  3.3 (Lint 规则) ─────────────────→ 3.5 (Lint 门禁)

第四阶段 (管理后台 + Agent 集成)
  4.2 (管理后台验证) ← 1.7 (Caddy Basic Auth)
  4.3 (SKILL.md) → 4.4 (handler.py) → 4.5 (Agent Manager 分配)

第五阶段 (收尾)
  5.1 (同步脚本) → 5.2 (运营流程)
  5.3 (操作手册) ← 5.4 (用户指南)
  5.5 (压力测试)
  5.6 (DR 方案)
```

---

## 风险与缓释

| 风险 | 影响 | 缓释措施 |
|:----|:----:|---------|
| **GBrain llama-server recipe 不支持无 auth** | 认证报错 | llama-server recipe 的 `auth_env.required` 为空数组（可选认证），`optional: ['LLAMA_SERVER_API_KEY']`。无 key 则发 `Bearer unauthenticated`，dl-cognee 可忽略。若需认证，设 `LLAMA_SERVER_API_KEY` env 即可。 |
| **GBrain llama-server-reranker recipe 路径不匹配** | reranker 调不通 | recipe 的 `path: '/rerank'`, `base_url_default: 'http://localhost:8081/v1'` → 实际调用 `http://dl-cognee-reranker:8080/v1/rerank`。我们的端点就是 `/v1/rerank`，路径完全匹配。 |
| **Bun/GBrain 安装需外网** | 内网构建失败 | 联网环境预构建 Docker 镜像推送到内部 registry |
| **GBrain 版本升级** | 兼容性破坏 | 锁定 Dockerfile 中的 GBRAIN_COMMIT；升级前回归测试 |
| **dl-cognee 嵌入 API 延迟** | GBrain 入库/搜索变慢 | dl-cognee 嵌入约 50-100 doc/s（CPU），第一批批量导入可能较慢；分批操作 |
| **cognee 知识同步延迟** | 管线搜不到最新知识 | 每小时同步一次，最小化不一致窗口 |
| **管理后台暴露** | 未授权访问 | Caddy Basic Auth + 内部网络隔离 |
| **dl-cognee 重启影响 GBrain** | GBrain 嵌入/rerank 不可用 | GBrain 内置重试逻辑 + fail-open 策略（reranker 失败自动 fallback 到无 rerank 搜索） |

---

## 成功标准

- [ ] **第一阶段：** `/v1/embeddings` 返回 1024 维向量，`/v1/rerank` 正确排序，`gbrain doctor --json` 全部绿色
- [ ] **第二阶段：** cognee `company_knowledge` 存量数据完整导入，搜索质量至少持平 cognee
- [ ] **第三阶段：** Schema Pack 正确分类 6 种类型，Lint 校验自动执行
- [ ] **第四阶段：** 管理后台可正常搜索，Agent Manager 飞书可查询知识库
- [ ] **第五阶段：** 50 并发 P99 延迟 < 3s，操作手册 + 用户指南完整可交付
- [ ] **全局：** 现有内容管线无退化（cognee 独立运行不受影响）

---

## 快速启动（MVP：压缩到 2 周）

如果希望尽快验证，跳过 Schema+Lint 和 Agent 集成：

```
第 1 周：
  ▸ dl-cognee 加 /v1/embeddings（~30 行）
  ▸ dl-cognee-reranker 加 /v1/rerank（~30 行）
  ▸ GBrain Dockerfile + entrypoint + docker-compose
  ▸ Postgres init + Caddy Basic Auth
  → GBrain 部署完毕，能搜索

第 2 周：
  ▸ 导出 cognee 存量数据 → 导入 GBrain
  ▸ 配置管理后台 Basic Auth
  → 2-3 人打开浏览器试用
  
（Schema、Lint、Agent 集成交付后迭代）
```

**MVP vs 完整版对比：**

| 维度 | MVP（2 周） | 完整版（4.5 周） |
|:----|:----------:|:---------------:|
| 搜索（嵌入 + 向量 + reranker） | ✅ | ✅ |
| 管理后台 | ✅ | ✅ |
| 数据迁移 | ✅ | ✅ |
| Schema Pack | ❌ 后期 | ✅ |
| Lint 门禁 | ❌ 后期 | ✅ |
| Agent 集成 | ❌ 后期 | ✅ |
| 知识同步 | ❌ 手动 | ✅ 自动脚本 |
| 压力测试 | ❌ | ✅ |
| 文档 | ❌ 口头 | ✅ 完整手册 |
