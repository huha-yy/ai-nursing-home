# CLAUDE.md

This file provides guidance to Claude Code when working with the dato repository.
New sessions must read this first to understand the project's architecture, goals, and design philosophy.

**Cross-session memory:** `JOURNAL.md` records key decisions, fixes, and pending tasks.
When starting a new session, read `JOURNAL.md` first to restore context.
Close a session by appending the session's outcome to `JOURNAL.md`.

## What dato is

**dato** is an on-premise multi-agent appliance powered by [OpenClaw](https://github.com/openclaw) (v2026.4.8).
It ships pre-installed on dedicated hardware — all data stays local. The system self-updates over the air;
do not `git pull` or manually modify this repo on the appliance.

The dev repo (`dato_prod-main`) is the source. Export produces a single squashed commit per appliance release;
the source commit SHA is recorded in `VERSION` and the `Source-Commit:` trailer.

### Project goals

1. **Multi-agent content operations** — Automate content creation (hotspot monitoring → research → strategy → writing → compliance → publishing) through a pipeline of specialized AI agents.
2. **Knowledge management** — A dedicated knowledge base agent stores/retrieves company knowledge (product info, brand positioning, image assets) via vector search (cognee RAG). Content pipeline agents reference this knowledge when composing articles.
3. **Multi-brand support** — The content pipeline is brand-agnostic; brand identity (mission, sector, tags, product names, image assets) is configured via pure YAML data files. New brands require no Python code changes.
4. **Progressively scale** — Start with content pipeline (Phase 1), expand to full operations (Phase 2+), each Agent gets its own Feishu bot for direct human interaction.
5. **Fully on-premise** — All data stays local, no external API calls to public LLM providers (uses DeepSeek via proxy).

## Agent Architecture

### Design principles

1. **Multi-Agent with single-tenant Appliance** — Not SaaS multi-tenant. Each appliance runs a fixed set of precreated agents.
2. **Separation of concerns** — Each agent has one job. If a task crosses boundaries, agents communicate via dato-control's internal API.
3. **Each Agent has its own Feishu bot** — Users talk to the relevant agent directly. No shared bot, no routing logic.
4. **All agents run via the same OpenClaw image** — Skills are baked into the Docker image. Agents differentiate via `skill_list`, `SOUL.md`, and workspace config.
5. **Python handler skills are NOT callable tools** — OpenClaw v2026.4.8 doesn't register `handler.py`-based skills as agent-visible tools. Instead, agents use the `process` tool (shell) to `python3 -c` one-liners that import handlers or call HTTP APIs.

### Current 4 Agents

| Agent | UUID | Feishu Bot | Role |
|---|---|---|---|
| **Agent Manager** | `748ffcbc` | ✅ dn1 | Management hub — create/manage agents, workflows, schedules, Feishu pairings. Only agent authorized to call `internal_routes.py` admin APIs. |
| **知识库 (Knowledge Base)** | `cc1acc65` | ✅ dn3 | Knowledge curator — ingest/search company knowledge via cognee. Product info, brand positioning, image asset paths. |
| **内容运营 (Content Ops)** | `7c90fc88` | ✅ dn2 | Content pipeline executor — runs the full content creation workflow. Also the future hub for all operations work. |
| **通用助手 (General)** | `eacdbc0e` | ✅ dn4 | User's first contact — handles simple tasks (translate, summarize, search, take notes). Routes complex requests to the right agent. |

### Agent interaction pattern

```
Feishu users
  ├──→ 通用助手: "帮我翻译这段" / "养老政策有哪些新动态" / "帮我记一下xxx"
  ├──→ Agent Manager: "帮我写一篇永和的公众号文章" → 调管线 API
  │                     "帮我跑一次内容管线，品牌永和，自动选题"
  ├──→ 知识库: "记一下戴恩助浴产品重量12kg" / "查一下我们的品牌定位"
  └──→ 内容运营: (管线内部执行，不与用户直接对话)

  Agent Manager ←→ dato-control (internal admin API, httpx) → content.pipeline
  知识库 ←→ dl-cognee (cognee skill handler, httpx)
  内容运营 ← dato-control (workflow dispatch) → 内容运营 runs pipeline steps
  内容运营 ←→ dl-cognee (reads company_knowledge library during pipeline)
```

### The `process` tool pattern (critical)

Python handler skills are NOT callable tools. Agents call them via:

```python
import sys; sys.path.insert(0, '/opt/openclaw/skills/custom/<name>')
from handler import add, search
result = add(content="...", path="...", library="...")
```

For HTTP APIs (internal admin API):
```python
import httpx, os
r = httpx.Client(base_url='http://dato-control:8080',
    headers={'Authorization': f'Bearer {os.environ["DL_INTERNAL_TOKEN"]}'})
resp = r.get('/api/internal/admin/agents')
```

### Precreated agent system

New agents are defined as YAML seeds under `dl-control/precreated_agents/<id>/`:
- `agent.yaml` — seed definition (`id`, `display_name`, `tier`, `admin_only`, `skill_list`, `channel_config`)
- `workspace/` — optional overrides for `SOUL.md`, `IDENTITY.md`, `TOOLS.md`, `HEARTBEAT.md`

The boot-time reconciler (`dl_control.precreated.reconciler.reconcile_precreated`) discovers seeds and provisions agents automatically.

**IMPORTANT:** Workspace files are seed templates, copied once at provision time. Changes to `precreated_agents/<id>/workspace/` do NOT affect running agents. To update a running agent:
1. Copy files to the bind-mount path (`/home/li/.local/share/dato/agents/<uuid>/`)
2. Restart the agent container

## Multi-Brand Architecture

The content pipeline supports multiple brands via a pure-data YAML configuration layer. No Python code changes needed for new brands.

### Configuration layers

```
dl-control container (pipeline orchestration):
  brand_configs/<brand>.yaml     ← brand text (mission, sector, tags, compliance rules)

Agent container (skill execution):
  configs/<brand>/brand_images.yaml     ← image asset mappings (brand_logo, product photos)
  configs/<brand>/brand_guidelines.md   ← brand voice, tone, forbidden words (read by SKILL.md)
  configs/<brand>/company_keywords.yaml ← keyword lists for relevance matching
  configs/<brand>/brand_assets/         ← logo, QR code, product photos
```

Brand configs are auto-discovered — `brand_config.py` scans `brand_configs/*.yaml` at import time.
Calling `get_brand("unknown")` silently falls back to `daien`.

### Brands currently defined

| Brand | Slug | Status |
|---|---|---|
| 戴恩医疗科技 | `daien` | Full config (guidelines, keywords, assets, images) |
| 永和大健康 / 生命优雅 | `yonghe` | Text config complete, logo uploaded, product photos pending |

### How brand isolation works at runtime

1. **Pipeline prompt**: `_brand_config(input)` reads `input["brand"]` and injects only that brand's mission/sector/tags into the agent's prompt. Other brands' data is never exposed.
2. **SKILL.md templates**: All content skills use generic language (e.g., "品牌连接段" not "戴恩连接段"). Brand-specific info comes solely from `brand_guidelines.md` + pipeline prompt.
3. **Entrypoint overlay**: The docker-entrypoint-wrapper.sh reads the `BRAND` env var and symlinks brand-specific files (`brand_guidelines.md`, `company_keywords.yaml`, `brand_assets/`) over the shared `configs/` root.
4. **Image assets**: `insert_images.py` reads `configs/<brand>/brand_images.yaml` for brand-specific image mappings. Unknown brands gracefully skip missing assets.

### Adding a new brand (no Python code)

```bash
# dl-control side
cp brand_configs/_template.yaml brand_configs/<slug>.yaml

# agent side
cp configs/_template/brand_images.yaml configs/<slug>/brand_images.yaml
# create configs/<slug>/brand_guidelines.md
# create configs/<slug>/company_keywords.yaml
# place assets in configs/<slug>/brand_assets/
```

Then the workflow accepts `{"brand": "<slug>", "topic": "..."}`.

### Delivery considerations

- **dato is single-tenant**: All brand configs coexist in the same Docker image.
- **Runtime isolation** is by `brand` parameter — only the active brand's config enters the prompt.
- **To permanently remove a brand** for a delivery: delete its brand_config YAML + configs directory + rebuild images (configs are baked into the Docker image at build time, not runtime-mounted).

## Content Pipeline (workflow)

### Flow definition

`dl-control/dl_control/workflows/flows/content_pipeline.py` — 13-step sequential workflow:

```
topic-gate → hotspot-monitor → relevance-judge → relevance-gate →
fact-research → content-strategy → wechat-content → xhs-content →
image-generator → article-composer → humanizer → compliance-check → feishu-publisher
```

### Workflow input

```json
{
  "agent_id": "7c90fc88-...",
  "brand": "yonghe",
  "topic": "文章主题"
}
```

- `brand` (optional, default `"daien"`): Brand slug — drives brand-specific mission, sector, tags, rules.
- `topic` (optional): Article topic. If omitted, `hotspot-monitor` auto-selects the top RSS story.
- `agent_id` (optional): Content-ops agent UUID (pre-filled by config_cache).

### Brand config loading

`content_pipeline.py` imports from `dl_control.workflows.brand_config`:

```python
from dl_control.workflows.brand_config import get_brand, list_brands

cfg = get_brand(input.get("brand"))  # returns dict with sector, mission, xhs_tags, etc.
```

The `_BRAND_CONFIG` inline dict was removed in favor of YAML-backed loading. Config keys:

| Key | Used by | Purpose |
|---|---|---|
| `name` | WeChat/XHS | Full brand name |
| `brand_short` | WeChat/XHS | Short brand name for social tags |
| `sector` | relevance-judge | Domain keywords for relevance scoring |
| `mission` | content-strategy, wechat-content | Core narrative mission injected into prompts |
| `wechat_rule` | wechat-content | Brand-specific WeChat content rules |
| `xhs_tags` | xhs-content | Required XHS hashtags |
| `brand_bridge` | content-strategy | Brand bridge description |
| `compliance_extra` | compliance-check | Additional compliance checks |
| `product_names` | insert_images.py | Product names for keyword matching |

### Image insertion

`openclaw/skills/article-composer/scripts/insert_images.py` is brand-agnostic. It loads brand assets from `configs/<brand>/brand_images.yaml` at runtime:

```python
cfg = _load_brand_images_yaml(brand, workspace)
brand_assets = cfg.get("brand_assets", [])    # [(src, dst, label), ...]
product_assets = cfg.get("product_assets", []) # [(src, dst, name), ...]
product_keywords = cfg.get("product_keywords", {})  # {name: [keywords]}
```

## Knowledge Base RAG (dl-cognee)

### Architecture

```
Agent containers ──httpx──→ dl-cognee:8080 (FastAPI service)
                                │
                        ┌───────┴───────┐
                    POST /v1/ingest  POST /v1/search
                        │               │
                        ▼               ▼
                  pgvector (shared via cognee.* tables)
```

### Design decisions

| Decision | Why |
|---|---|
| **Embedding model**: `BAAI/bge-m3` (1024-dim) | FlagEmbedding, Chinese + English, better quality vs multilingual-MiniLM |
| **Reranker**: `BAAI/bge-reranker-v2-m3` (separate microservice) | Second-pass ranking over vector search candidates, improves precision |
| **Model deployment**: Docker named volume (`cognee_hf_models`) | Models too large to bake into image (~2.2GB each); init container downloads at startup |
| **Shared library** vs isolated DB | Shared (`cognee.documents` / `cognee.chunks`) — simpler; no per-agent DB provisioning needed |
| **Search flow**: Q → embed (bge-m3, 1024-dim) → vector search (top-N, N>k) → reranker (top-k) | Phase 2: reranker refines vector search results for higher precision |
| **Library ACL**: read_write / read per agent | `company_knowledge` shared library: 知识库=write, 内容运营=read |
| **`refs/main` must not have trailing newline** | huggingface_hub doesn't strip → `snapshots/{hash}\n` fails lookup offline |

### How agents use cognee

Knowledge Base Agent writes:
```python
from handler import add
add(content="# product specs...", path="products/xxx/specs.md", library="company_knowledge")
```

Content pipeline steps search:
```python
from handler import search
results = search(query="brand positioning", library_slugs=["company_knowledge"])
```

### Libraries

| Library | Type | Who can write | Who can read |
|---|---|---|---|
| `company_knowledge` | shared | 知识库 Agent | 知识库 Agent + 内容运营 Agent |
| `_public` | shared | (system) | All agents |
| Agent auto-private | per-agent | The agent itself | The agent itself |

## Key files reference

### Multi-brand core
| File | Role |
|---|---|
| `dl-control/dl_control/workflows/brand_config.py` | YAML loader — scans `brand_configs/*.yaml`, `get_brand()` with fallback |
| `dl-control/dl_control/workflows/brand_configs/<slug>.yaml` | Brand text config (dl-control side) |
| `dl-control/dl_control/workflows/flows/content_pipeline.py` | Pipeline flow — `_brand_config(inp)` for prompt injection |
| `openclaw/configs/<slug>/brand_images.yaml` | Brand image asset mappings (agent side) |
| `openclaw/configs/<slug>/brand_guidelines.md` | Brand guidelines read by SKILL.md |
| `openclaw/configs/<slug>/company_keywords.yaml` | Keyword lists for relevance matching |
| `openclaw/entrypoint/docker-entrypoint-wrapper.sh` | BRAND env var overlay for configs/ |

### Agent Manager behavior
| File | Role |
|---|---|
| `dl-control/precreated_agents/agent-manager/workspace/IDENTITY.md` | **"写文章→调管线API，禁止自写"** — forces API call |
| `dl-control/precreated_agents/agent-manager/workspace/TOOLS.md` | Workflow input format docs (brand/topic optional) |
| `openclaw/skills/admin-mgmt/SKILL.md` | start_workflow parameter docs + brand examples |

### Skills with brand de-hardcoding
| File | Status |
|---|---|
| `openclaw/skills/content-strategy/SKILL.md` | ✅ Generic — no "戴恩" references |
| `openclaw/skills/wechat-content/SKILL.md` | ✅ Generic — brand info from prompt/guidelines |
| `openclaw/skills/xhs-content/SKILL.md` | ✅ Generic — tags from pipeline prompt |

## Commands

### Developer (host-side)

| Command | What |
|---|---|
| `make lint` | `ruff check .` on root + dl-control |
| `make fmt` | `ruff format .` |
| `make test` | `pytest tests/ -v` + `make control-test` |
| `make control-test` | `cd dl-control && uv run pytest -q` |
| `make smoke` | `make up && make admin-init && pytest tests/test_smoke*.py` |
| `uv run pytest tests/test_smoke_p6_llm.py -v` | Smoke-test the local LLM path |
| `make build` | Build all service images (see note about dl-ota-watcher in Makefile) |

### Operator (appliance-side — requires `make up` / full stack)

| Command | What |
|---|---|
| `make up` | Start the full foundation stack |
| `make down` | Stop & remove containers |
| `make logs` | Tail all services |
| `make ps` | List running services |
| `make psql` | Open psql to dato-postgres |
| `make redis-cli` | Open redis-cli to dato-redis |
| `make admin-init` | Create first admin user (idempotent) |
| `make control-logs` | Tail dl-control logs |
| `make cognee-logs` | Tail dl-cognee logs |
| `make clean` | Down + prune volumes |
| `make wipe` | Factory reset |
| `make reset` | Non-destructive in-place re-init |

To run a single test: `cd dl-control && uv run pytest tests/path/to/test.py::test_name -xvs` or `uv run pytest tests/test_smoke.py -k test_name -v`.

### Running agent config sync

When modifying workspace files for running agents, changes to `precreated_agents/` seed templates are not enough:

```bash
# 1. Copy to running agent's bind-mount
cp seed/TOOLS.md /home/li/.local/share/dato/agents/<uuid>/TOOLS.md
# 2. Restart to reload
docker restart dato-agent-<uuid>
```

For skills (SKILL.md), the agent reads from `/opt/openclaw/skills/custom/<name>/` (baked into image):

```bash
# Override by copying directly into container
docker cp SKILL.md dato-agent-<uuid>:/opt/openclaw/skills/custom/<name>/SKILL.md
docker restart dato-agent-<uuid>
```

## Architecture overview

```
dato/
├── dl-control/              ← Admin web UI + agent registry + workflow engine (FastAPI)
│   ├── dl_control/
│   │   ├── agents/          ← Registry CRUD, provisioning, internal admin API
│   │   ├── precreated/      ← Seed agent reconciler
│   │   ├── workflows/       ← Workflow runner, flows (content_pipeline, hr_onboarding)
│   │   │   ├── flows/       ← content_pipeline.py (brand-agnostic pipeline)
│   │   │   ├── brand_config.py  ← YAML-backed brand loader
│   │   │   └── brand_configs/   ← <slug>.yaml per brand (daien, yonghe)
│   │   ├── channels/        ← Feishu integration (pairings, credentials)
│   │   ├── audit/           ← Central audit log
│   │   └── auth/            ← Session-based auth
│   └── precreated_agents/   ← Seed YAML files (agent-manager, knowledge-base)
├── dl-cognee/               ← RAG knowledge graph service (FlagEmbedding bge-m3 + pgvector)
│   └── dl-cognee-reranker/  ← Reranker microservice (bge-reranker-v2-m3)
├── dl-llm-proxy/            ← LLM passthrough + rate-limit guardrail
├── dl-llm-local/            ← Bundled local model via Ollama (optional)
├── dl-ota-watcher/          ← OTA self-update poller
├── dl-egress-dns/           ← DNS-level LLM-deny guardrail
├── dl_shared/               ← Shared Python library (rate-limit, secrets, manifest)
├── openclaw/                ← Patched OpenClaw image + custom skills
│   ├── patches/             ← Feishu extension patches
│   ├── skills/              ← 20 custom skills (all brand-agnostic SKILL.md)
│   ├── scripts/             ← Content pipeline scripts
│   └── configs/             ← Brand assets, platform style guides, keywords
│       ├── daien/           ← 戴恩 brand config
│       ├── yonghe/          ← 永和 brand config
│       └── _template/       ← New brand template
├── infra/                   ← Docker Compose, Caddy, Postgres init
├── templates/               ← Workspace templates + openclaw.json.j2
└── tests/                   ← Integration smoke tests (require full stack)
```

### dl-control internals

The app lives in `dl-control/dl_control/`:

| Module | Responsibility |
|---|---|
| `main.py` | FastAPI app factory with lazy ASGI entrypoint (LazyApp pattern). Wires routers, starts background loops (reconciler, audit mirror, workflow runner, OTA reattach). |
| `settings.py` | All env-driven config (Pydantic Settings, immutable, validated at load). |
| `db.py` | Database connection manager with role-based RLS (`conn(user_id, role)`). |
| `auth/` | Session-based auth (Redis), argon2 password hashing, password rotation enforcement. |
| `agents/` | Agent registry CRUD (`service.py`), provisioning orchestration (`provisioning/`), Docker container lifecycle via docker-socket-proxy. Also `internal_routes.py` — admin internal API endpoints. |
| `precreated/` | Declarative seed-agent system: `loader.py` parses `agent.yaml` files, `reconciler.py` bootstraps seeds, `apply.py` applies drift. |
| `workflows/` | Workflow runner with Postgres-based lease, scheduler, wake-listener (Redis pub/sub), and agent dispatch (P13d peer interface). |
| `audit/` | Central audit log with per-agent DB mirroring. |
| `channels/` | Feishu integration (pairings, credential wizard, reconciler). |
| `libraries/` | Knowledge library management. |
| `ota/` | OTA coordination — roll/openclaw jobs, install digest seed. |
| `migrations.py` | Alembic-style schema migrations run as a one-shot container. |

Background loops:
- **Feishu reconciler** (P3) — single-writer flock, projects pairings → allowFrom files
- **Audit mirror** (P4) — drains `audit_log_outbox` into per-agent databases
- **Workflow runner** (P13b) — Postgres lease + flock, leases workflow runs, dispatches to agents
- **Active-agent reconciler** (P11) — recovers agents with missing containers at startup
- **OTA reattach** (P7) — reattaches in-progress roll-openclaw jobs after restart

### Agent model

Two tiers:
- **Tier 1** — Each agent runs in its own Docker container with an isolated per-agent Postgres database.
- **Tier 0** (default) — Docker container, no per-agent database. All agents get their own `.env` with `DL_INTERNAL_TOKEN`.

Provisioning creates: Docker container, per-agent DB + role (Tier 1 only), workspace files from templates, compose mirror, and an openclaw.json config.

### Precreated agents

Boot-time seeded agents under `dl-control/precreated_agents/`. Each subdirectory has an `agent.yaml` seed file and an optional `workspace/` directory with override files. The reconciler runs on first bootstrap and creates these agents automatically:

- `agent-manager/` — Admin agent, manages agents/workflows via internal API
- `knowledge-base/` — Knowledge curator, writes/reads cognee

### OpenClaw custom skills

20 custom skills live under `openclaw/skills/`, each with `_meta.json` + `SKILL.md`:

| Skill | Purpose |
|---|---|
| **admin-mgmt** | Agent Manager system mgmt — HTTP shim to internal admin API |
| **cognee** | RAG query handler — `add()` / `search()` via httpx to dl-cognee |
| **workflow** | Agent→workflow dispatch (P13d) |
| **hotspot-monitor** | Fetch + cluster hotspot news |
| **relevance-judge** | Score topic relevance to brand |
| **fact-research** | Cross-verify facts from multiple sources |
| **content-strategy** | Create platform-specific content strategies (brand-agnostic) |
| **wechat-content** | Generate WeChat public account articles (brand-agnostic) |
| **xhs-content** | Generate Xiaohongshu notes (brand-agnostic) |
| **douyin-content** | Generate Douyin video scripts (brand-agnostic) |
| **image-generator** | Search Pexels or run ComfyUI |
| **article-composer** | Embed images into articles (brand-agnostic, reads brand_images.yaml) |
| **humanizer** | Detect + remove AI writing patterns |
| **compliance-check** | 4-dimension compliance review |
| **publish-package** | Package outputs for publishing |
| **feishu-publisher** | Push markdown → Feishu Doc |
| **web-content-fetcher** | Fallback web scraper |
| **self-improving** | Heartbeat + self-reflection loop |
| **openai-whisper** | Local ASR (speech→text) |
| **nano-pdf** | Natural language PDF editing |

**IMPORTANT:** Python `handler.py`-based skills (`admin-mgmt`, `cognee`, `workflow`) do NOT register as callable tools in OpenClaw v2026.4.8. Agents use the `process` tool to `python3 -c` one-liners that import handlers directly.

### Internal admin API (`internal_routes.py`)

Located at `dl-control/dl_control/agents/internal_routes.py`. Authenticated by `DL_INTERNAL_TOKEN` sha256, authorized only for `precreated_id='agent-manager'`. 11 endpoints: list/get/create/delete/restart agents, list/get workflows, list/create/delete schedules, list workflow runs, start workflow.

All endpoints use `role="system"` for DB access (bypassing RLS). `actor_user_id` is always `None`.

### Shared library (`dl_shared/`)

Three modules:
- `rate_limit.py` — ASGI middleware for per-IP flood gates
- `secrets.py` — Secret management utilities
- `manifest_verify.py` — Install bundle manifest verification (minisign)

## Key conventions

- **Python 3.12+**, `uv` for package management. Each sub-project has its own `pyproject.toml` and `uv.lock`.
- **Ruff** for linting (E, F, I, B, UP, N, SIM rules), line length 100.
- **pytest** with `asyncio_mode = "auto"`. dl-control has a `docker` marker for tests needing the OpenClaw image.
- **Secrets never enter git.** `infra/.env` is gitignored. `infra/.env.example` is the template.
- **All mutations run through RLS.** Every DB connection passes `user_id` and `role`.
- **Background loops use flock** as single-writer guard. Lock path: `<agents_root>/.dato-<purpose>.lock`.
- **Caddy** serves the admin UI at `https://<host>:9443` (default).
- **Precreated agent workspace changes:** Edit both the seed template AND the running agent's bind-mount, then restart the container.
- **SKILL.md changes:** `docker cp` into the running agent container, then restart.
- **Brand configs are pure YAML** — adding a new brand never requires Python code changes.
