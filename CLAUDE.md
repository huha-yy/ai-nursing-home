# CLAUDE.md — AI 养老院院长 MVP

This file provides guidance to Claude Code when working with the nursing home MVP repository.

**Cross-session memory:** `JOURNAL.md` records key decisions, fixes, and pending tasks.
When starting a new session, read `JOURNAL.md` first to restore context.
Close a session by appending the session's outcome to `JOURNAL.md`.

## What this is

**AI 养老院院长** is an AI-powered operations assistant for nursing homes, built on the dato multi-agent appliance platform. MVP targets 杭州市第三社会福利院 (1,752 beds, 350 staff, 26 buildings).

The platform is powered by [OpenClaw](https://github.com/openclaw) (v2026.4.8) and ships pre-installed on dedicated hardware — all data stays local.

### Project goals

1. **Multi-agent operations** — 10 specialized agents (director, nursing, logistics, 6 buildings, general assistant) each with role-specific skills and nursing UI access.
2. **Core features** — auto schedule generation, inventory tracking with alerts, health signal monitoring, ops dashboard, multi-agent weekly report workflow.
3. **All on-premise** — local LLM, data stays local, no cloud dependencies (uses DeepSeek via proxy).

## Agent Architecture

### Design principles

1. **Multi-Agent with single-tenant Appliance** — Not SaaS multi-tenant. Each appliance runs a fixed set of precreated agents.
2. **Separation of concerns** — Each agent has one job. If a task crosses boundaries, agents communicate via dato-control's internal API.
3. **Each Agent has its own Feishu bot** — Users talk to the relevant agent directly. No shared bot, no routing logic.
4. **All agents run via the same OpenClaw image** — Skills are baked into the Docker image. Agents differentiate via `skill_list`, `SOUL.md`, and workspace config.
5. **Python handler skills are NOT callable tools** — OpenClaw v2026.4.8 doesn't register `handler.py`-based skills as agent-visible tools. Instead, agents use the `process` tool (shell) to `python3 -c` one-liners that import handlers or call HTTP APIs.

### Current 10 Nursing Agents

| Agent | Role | Skills |
|---|---|---|
| **director** | 院长 | nursing-work-order, logistics-query, meal-query, staff-query, resident-query, activity-query, finance-query, alert-query, report-generate |
| **nursing-dept** | 护理科 | nursing-schedule, nursing-work-order, logistics-query, meal-query, staff-query, resident-query, activity-query, alert-query |
| **logistics-dept** | 总务科 | logistics-inventory, logistics-query, meal-query, staff-query, resident-query |
| **building-1..6** | 楼栋负责人 | logistics-query, meal-query, staff-query, resident-query, activity-query, alert-query |
| **general-assistant** | 通用助手 | meal-query, activity-query, staff-query, resident-query (general public-facing queries) |

### Agent interaction pattern

```
Nursing users (via /chat web UI)
  ├──→ 院长: "生成本周运营报表" / "全院概况"
  ├──→ 护理科: "生成3号楼本周排班" / "查询工单完成情况"
  ├──→ 总务科: "盘点库存" / "尿不湿还有多少"
  ├──→ 楼栋负责人: "3号楼今天谁当班" / "本楼老人情况"
  └──→ 通用助手: "今天的菜单" / "活动安排"

  All agents ←→ dato-control (REST API)
  All agents ←→ Postgres (via handler.py skills with DATABASE_URL)
  Nursing ops workflow: director triggers → nursing → logistics → finance → director report
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

## Nursing Operations Workflow

### Flow definition

`dl-control/dl_control/workflows/flows/nursing_ops.py` — 4-step multi-agent workflow:

```
nursing-schedule-step → logistics-step → finance-step → director-report-step
```

### Workflow input

```json
{
  "nursing_agent_id": "<director agent UUID>"
}
```

- Triggered via `POST /api/nursing/workflow/start` by the director.
- Each step dispatches to a different nursing Agent via CallAgent.
- Results are synthesized by the `report-generate` skill into a weekly ops report.

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

### Nursing core
| File | Role |
|---|---|
| `dl-control/dl_control/workflows/flows/nursing_ops.py` | 4-step nursing ops workflow |
| `dl-control/dl_control/workflows/flows/catalog.py` | Flow registry (hr.onboarding_email, content.pipeline, nursing.ops) |
| `dl-control/dl_control/middleware/health_signal.py` | Health keyword detection + alert creation |
| `dl-control/dl_control/main.py` | FastAPI app factory — nursing routes, dashboard API, workflow trigger |
| `infra/postgres/init/03-nursing-seed.sql` | All nursing tables + seed data (users, residents, schedules, inventory, etc.) |
| `dl-control/precreated_agents/*/agent.yaml` | 10 agent seed definitions (director, nursing-dept, logistics-dept, building-1..6, general-assistant) |

### Skills with nursing focus
| Skill | Purpose |
|---|---|
| `nursing-schedule` | Generate weekly 12h-shift schedules per building |
| `nursing-work-order` | Query work order completion rates |
| `logistics-inventory` | Inventory tracking with safety-stock alerts |
| `logistics-query` | Read-only logistics/inventory lookups |
| `resident-query` | Lookup residents by name/building/room |
| `staff-query` | Lookup staff by name/building |
| `meal-query` | Query daily menus |
| `activity-query` | Query scheduled activities |
| `finance-query` | Query resident finance records |
| `alert-query` | Query health alerts with filters |
| `report-generate` | Synthesize weekly ops report from multi-agent outputs |

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
| `make build` | Build all service images |

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
nursing-home-mvp/
├── dl-control/              ← Admin web UI + agent registry + workflow engine (FastAPI)
│   ├── dl_control/
│   │   ├── agents/          ← Registry CRUD, provisioning, internal admin API
│   │   ├── precreated/      ← Seed agent reconciler (10 nursing agents)
│   │   ├── workflows/       ← Workflow runner, flows (nursing_ops, content_pipeline, hr_onboarding)
│   │   │   └── flows/       ← nursing_ops.py (4-step nursing workflow)
│   │   ├── middleware/      ← HealthSignalMiddleware (fire-and-forget health scanning)
│   │   ├── channels/        ← Channel integration (normalize.py only, feishu removed)
│   │   ├── audit/           ← Central audit log
│   │   └── auth/            ← Session-based auth + nursing login
│   └── precreated_agents/   ← 10 agent YAML seeds (director, nursing-dept, logistics-dept, building-1..6, general-assistant)
├── dl-cognee/               ← RAG knowledge graph service (FlagEmbedding bge-m3 + pgvector)
│   └── dl-cognee-reranker/  ← Reranker microservice (bge-reranker-v2-m3)
├── dl-llm-proxy/            ← LLM passthrough + rate-limit guardrail
├── dl-llm-local/            ← Bundled local model via Ollama (optional)
├── dl_shared/               ← Shared Python library (rate-limit, secrets, manifest)
├── openclaw/                ← Patched OpenClaw image + custom skills
│   ├── patches/             ← Extension patches
│   ├── skills/              ← 22 skills (11 nursing + 11 supporting)
│   ├── scripts/             ← Auxiliary scripts
│   └── configs/             ← _template/ (for future use)
├── infra/                   ← Docker Compose, Caddy, Postgres init (03-nursing-seed.sql)
├── templates/               ← Workspace templates + openclaw.json.j2
└── tests/                   ← Integration tests + MVP smoke test
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
| `channels/` | Channel normalization (feishu integration removed for nursing MVP). |
| `libraries/` | Knowledge library management. |
| `middleware/` | Health signal middleware — fire-and-forget resident health keyword detection. |
| `migrations.py` | Alembic-style schema migrations run as a one-shot container. |

Background loops:
- **Audit mirror** (P4) — drains `audit_log_outbox` into per-agent databases
- **Workflow runner** (P13b) — Postgres lease + flock, leases workflow runs, dispatches to agents
- **Active-agent reconciler** (P11) — recovers agents with missing containers at startup

### Agent model

Two tiers:
- **Tier 1** — Each agent runs in its own Docker container with an isolated per-agent Postgres database.
- **Tier 0** (default) — Docker container, no per-agent database. All agents get their own `.env` with `DL_INTERNAL_TOKEN`.

Provisioning creates: Docker container, per-agent DB + role (Tier 1 only), workspace files from templates, compose mirror, and an openclaw.json config.

### Precreated agents

Boot-time seeded agents under `dl-control/precreated_agents/`. Each subdirectory has an `agent.yaml` seed file and an optional `workspace/` directory with override files. The reconciler runs on first bootstrap and creates these agents automatically:

- `director/` — 院长 (director), owner of weekly report workflow, full read access
- `nursing-dept/` — 护理科 (nursing dept), schedule + work order management
- `logistics-dept/` — 总务科 (logistics dept), inventory tracking + alerts
- `building-1/` through `building-6/` — 楼栋负责人 (building heads), per-building queries
- `general-assistant/` — 通用助手 (general assistant), public-facing info queries
- `agent-manager/` — Admin agent, manages agents/workflows via internal API (legacy)
- `knowledge-base/` — Knowledge curator, writes/reads cognee (legacy)
- `content-ops/` — Content operations (legacy, pipeline dep removed)
- `operations-a/`, `operations-b/` — Operations agents (legacy)

### OpenClaw custom skills

Skills live under `openclaw/skills/`, each with `_meta.json` + `SKILL.md`:

### Nursing MVP skills (11)

| Skill | Purpose |
|---|---|
| **nursing-schedule** | Generate weekly 12h-shift schedules per building (asyncpg) |
| **nursing-work-order** | Query work order completion rates by type (asyncpg) |
| **logistics-inventory** | Inventory CRUD with safety-stock alerts (asyncpg) |
| **logistics-query** | Read-only inventory lookups (asyncpg) |
| **resident-query** | Lookup residents by name/building/room (asyncpg) |
| **staff-query** | Lookup staff by name/building (asyncpg) |
| **meal-query** | Query daily menus by date/meal_type (asyncpg) |
| **activity-query** | Query scheduled activities by date (asyncpg) |
| **finance-query** | Query resident finance records by month (asyncpg) |
| **alert-query** | Query health alerts with severity/handled filters (asyncpg) |
| **report-generate** | Synthesize weekly ops report from multi-agent outputs |

### Supporting skills (from original dato platform)

| Skill | Purpose |
|---|---|
| **admin-mgmt** | Agent Manager system mgmt — HTTP shim to internal admin API |
| **cognee** | RAG query handler — `add()` / `search()` via httpx to dl-cognee |
| **workflow** | Agent-to-workflow dispatch (P13d) |
| **gbrain-mcp** | GBrain MCP integration |
| **openai-whisper** | Local ASR (speech-to-text) |
| **nano-pdf** | Natural language PDF editing |
| **self-improving** | Heartbeat + self-reflection loop |
| **web-content-fetcher** | Fallback web scraper |
| **ppt-generator** | PPT generation |
| **ppt-master** | PPT master template |
| **vision-ocr** | Vision-based OCR |

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
