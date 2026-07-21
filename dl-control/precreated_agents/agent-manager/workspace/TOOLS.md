# TOOLS.md — Agent Manager

**🔴 只有一层调度，只有一个数据源：`dato-control:8080`。**

OpenClaw 内置的 `cron`/`cron_list`/`agents_list` 等工具读取的是**容器本地缓存**，数据是空的或者是错的——它们看到的 "error" 是噪音，直接忽略。

所有 Agent、工作流、定时任务的数据都在 dato-control 的 PostgreSQL 数据库里。你必须用下面的 httpx API 才能查到真实数据。

**禁止使用：** `agents_list`, `openclaw status`, `agents_spawn`, `sessions_spawn`, `sessions_list`, `cron`, `cron_list`, `cron_create`, `cron_delete`, `cron_update`。用了就是假数据。

---

Skills define *how* tools work. This file describes the tools available to agent-manager.

**IMPORTANT**: These tools are NOT registered as callable skills. Use the `process` tool
(your shell execution tool) to run Python one-liners via `python3 -c`. You have
`$DL_INTERNAL_TOKEN` and `httpx` available in your environment.

Always call `http://dato-control:8080` (internal Docker network). Use this EXACT pattern:

### Quick start: call an API right now

To call any endpoint below, run this ONE-LINER (replace method, path, and body):

```bash
python3 -c "
import httpx, os
r = httpx.Client(base_url='http://dato-control:8080',
    headers={'Authorization': f'Bearer {os.environ[\"DL_INTERNAL_TOKEN\"]}'},
    timeout=httpx.Timeout(5.0, read=30.0)
)
# CHANGE METHOD AND PATH HERE:
resp = r.get('/api/internal/admin/agents')
print(resp.status_code, resp.text[:2000])
"
```

For POST requests, add `.json(...)`:
```bash
python3 -c "
import httpx, os
r = httpx.Client(base_url='http://dato-control:8080',
    headers={'Authorization': f'Bearer {os.environ[\"DL_INTERNAL_TOKEN\"]}'},
    timeout=httpx.Timeout(5.0, read=30.0)
)
# CHANGE METHOD, PATH, AND BODY HERE:
resp = r.post('/api/internal/admin/workflows/content.pipeline/start', json={'input': {'key': 'value'}})
print(resp.status_code, resp.text[:2000])
"
```

### Polling pattern for workflow completion

After starting a workflow, poll until done:
```bash
python3 -c "
import httpx, os, time
r = httpx.Client(base_url='http://dato-control:8080',
    headers={'Authorization': f'Bearer {os.environ[\"DL_INTERNAL_TOKEN\"]}'},
    timeout=httpx.Timeout(5.0, read=30.0)
)
run_id = 'REPLACE_WITH_YOUR_RUN_ID'
while True:
    d = r.get(f'/api/internal/admin/workflow-runs/{run_id}').json()
    s = d['run']['status']
    print(f'{s} step={d[\"run\"][\"current_step\"]}')
    if s in ('succeeded','failed','cancelled'): break
    time.sleep(10)
import json; print(json.dumps(d, indent=2, ensure_ascii=False))
"
```

## Agent Management

- **`list_agents`** → `GET /api/internal/admin/agents`
  Returns: `{agents: [{id, display_name, tier, status, skill_list, ...}]}`

- **`get_agent`** → `GET /api/internal/admin/agents/{agent_id}`
  Returns agent detail. Errors: 404 if not found.

- **`create_agent`** → `POST /api/internal/admin/agents`
  Body: `{display_name, tier?, skill_list?, channel_config?, model_selection?}`
  Always confirm tier, skills, and settings with the admin before creating.

- **`delete_agent`** → `DELETE /api/internal/admin/agents/{agent_id}`
  Always confirm with admin before deleting. Errors: 404 if not found.

- **`restart_agent`** → `POST /api/internal/admin/agents/{agent_id}/restart`
  Returns: `{restarted: true, agent_id}`. Errors: 404/409/500.

## 🔥 创建新 Agent

用户说"创建一个新 Agent" → 先确认 skill_list、display_name 再创建：

```bash
python3 -c "
import httpx, os, json
r = httpx.Client(base_url='http://dato-control:8080',
    headers={'Authorization': f'Bearer {os.environ[\"DL_INTERNAL_TOKEN\"]}'},
    timeout=httpx.Timeout(5.0, read=30.0)
)
# 替换 display_name 和 skill_list 为你需要的值
resp = r.post('/api/internal/admin/agents', json={
    'display_name': '新 Agent 名称',
    'tier': 'tier0',
    'skill_list': ['cognee', 'workflow', 'web-content-fetcher'],
})
print(resp.status_code, resp.text[:2000])
"
```

**必须先确认：** display_name、tier（默认 tier0）、skill_list、channel_config 是否都需要。
创建后可用 `list_agents` 确认新 Agent 已就绪。

## Workflow Management

- **`list_workflows`** → `GET /api/internal/admin/workflows`
  Returns: `{workflows: [{id, display_name, description, enabled, ...}]}`

- **`get_workflow`** → `GET /api/internal/admin/workflows/{workflow_id}`
  Errors: 404 if not found.

- **`list_workflow_runs`** → `GET /api/internal/admin/workflow-runs?workflow_id=&limit=50`
  Returns: `{runs: [{id, workflow_id, status, trigger, created_at, ...}]}`

## Schedule Management

- **`list_schedules`** → `GET /api/internal/admin/workflows/{workflow_id}/schedules`
  Returns: `{schedules: [{id, cron, input_template, enabled, next_fire_at, ...}]}`

- **`create_schedule`** → `POST /api/internal/admin/workflows/{workflow_id}/schedules`
  Body: `{cron, input_template?}`

- **`delete_schedule`** → `DELETE /api/internal/admin/workflows/{workflow_id}/schedules/{schedule_id}`
  Errors: 404 if not found.

## 🔥 创建定时任务

用户说"每天/每周/定期发一篇文章" → 创建 content.pipeline 定时任务：

```bash
python3 -c "
import httpx, os, json
r = httpx.Client(base_url='http://dato-control:8080',
    headers={'Authorization': f'Bearer {os.environ[\"DL_INTERNAL_TOKEN\"]}'},
    timeout=httpx.Timeout(5.0, read=30.0)
)
# 每天早八点，品牌 yonghe，自动选题
resp = r.post('/api/internal/admin/workflows/content.pipeline/schedules', json={
    'cron': '0 8 * * *',
    'input_template': json.dumps({'brand': 'yonghe'})
})
print(resp.status_code, resp.text[:2000])
"
```

- `cron` 是标准 5 段 cron 表达式（分 时 日 月 周），例如 `0 8 * * *` = 每天 8:00，`0 9 * * 1` = 每周一 9:00
- `input_template` 是 JSON 字符串（用 `json.dumps()` 编码），传给管线作为默认输入
- 先 `list_workflows` 看一下有哪些 workflow_id 可用（通常只有 `content.pipeline`）

## Workflow Execution

- **`start_workflow`** → `POST /api/internal/admin/workflows/{workflow_id}/start`
  Body: `{input: {...}, correlation_key?: "..."}`
  Returns: `{run_id: "..."}`. Errors: 404/409.

  Bypasses agent grants — any workflow can be started immediately.

  Use this instead of the workflow skill handler.

  **content.pipeline 输入格式：**
  ```json
  {"input": {"brand": "daien", "topic": "文章主题", "agent_id": "7c90fc88-fd6f-452c-bb49-cc1b0ef20037"}, "correlation_key": "..."}
  ```
  - `brand`（可选，默认 `"daien"`）：品牌标识，目前支持 `"daien"`（戴恩）和 `"yonghe"`（永和）。不同品牌使用不同的品牌口径、关键词和合规规则。
  - `topic`（可选）：文章主题，也可以用 `theme`。**不传则管线自动从 RSS 热点源抓取最新选题**。
  - `agent_id`：默认为 `7c90fc88-fd6f-452c-bb49-cc1b0ef20037`（内容运营 Agent），一般不需要改
  - `date`（可选）：日期，默认当天

  **使用示例：**
  - 戴恩（指定主题）：`{"input": {"topic": "养老政策新变化", "agent_id": "7c90fc88-..."}}`（`brand` 默认 "daien"）
  - 戴恩（自动选题）：`{"input": {}}` — topic、brand 都不传，管线自动监控热点 + 默认戴恩
  - 永和：`{"input": {"brand": "yonghe", "topic": "中医脉诊数字化趋势"}}`
  - 永和（自动选题）：`{"input": {"brand": "yonghe"}}` — 只指定品牌，管线自动抓取热点

  Use this instead of the workflow skill handler.

- **`get_workflow_run`** → `GET /api/internal/admin/workflow-runs/{run_id}`
  Returns full run detail incl. step outputs.

- **After starting a workflow:**
  Poll `get_workflow_run` every 10–15 seconds silently until `status` is terminal
  (`succeeded`/`failed`/`cancelled`).
  On success, read the last step's output (e.g. `feishu-publisher` step should
  contain article URLs). **回复用户只给文档链接，不要发送中间状态或 run_id。**

## Knowledge Graph (via cognee skill handler)

Import and call from `/opt/openclaw/skills/custom/cognee/handler.py`:
```python
import sys; sys.path.insert(0, '/opt/openclaw/skills/custom/cognee')
from handler import add, search
```

- **`cognee.add`** → `add(content, path?, library?)`
- **`cognee.search`** → `search(query, limit?, library_slugs?)`

## GBrain Knowledge Base (via gbrain-mcp skill handler)

GBrain 是公司知识库，涵盖戴恩医疗科技和永和大健康/生命优雅的品牌资料、产品规格、FAQ、中医养生知识等。Import and call from `/opt/openclaw/skills/custom/gbrain-mcp/handler.py`:

```python
import sys; sys.path.insert(0, '/opt/openclaw/skills/custom/gbrain-mcp')
from handler import search, get_page
```

- **`gbrain-mcp.search`** → `search(query, limit=5)`
  Searches the knowledge base with semantic matching.
  Returns list of `{slug, title, type, score, chunk_text}`.
  Example:
  ```python
  results = search("戴恩护理机器人参数")
  results = search("阴虚体质怎么调理")
  results = search("生命优雅智能戒指功能")
  ```

- **`gbrain-mcp.get_page`** → `get_page(slug)`
  Read a full page by slug.
  Returns `{slug, title, type, compiled_truth, updated_at}` or None.
  Example:
  ```python
  page = get_page("daien/product/care_robot")
  page = get_page("yonghe/health/constitution_types")
  page = get_page("yonghe/company/brand_guidelines")
  ```

**Use pattern:** When a user asks about company products, brand info, or health knowledge, search GBrain first. If the search result has enough info, summarize it. If not, use `get_page` to read the full page.

**Known slugs (for direct access):**
- `daien/company/company_intro` — 戴恩公司介绍
- `daien/company/brand_guidelines` — 戴恩品牌口径
- `daien/product/care_robot` — 智能排泄物护理机器人
- `daien/product/flexbath_360` — 智能便携洗浴机 DEN FlexBath 360
- `daien/faq/product_faq` — 戴恩 FAQ
- `yonghe/company/brand_guidelines` — 永和品牌口径
- `yonghe/company/company_intro` — 永和公司介绍
- `yonghe/product/product_keywords` — 永和产品与关键词
- `yonghe/health/constitution_types` — 九种体质辨识手册
- `yonghe/faq/product_faq` — 永和 FAQ

## Discovery Rule

Never invent agent IDs, workflow IDs, schedule IDs, or run IDs. Always use the corresponding `list_*` function first to discover valid identifiers.

## Send Image to Feishu Chat

Use `send_image.py` to send a local image file directly to a Feishu private chat or group.

**Finding the target ID:** Your inbound message context includes:

```
[FeishuChatId: oc_xxxxx]
[FeishuSenderOpenId: ou_xxxxx]
```

Use `[FeishuChatId]` as `--to` value with `--type chat_id`. This works for both group chats and private chats.

```bash
# Send to your current conversation (use [FeishuChatId] from your context)
python3 /opt/openclaw/skills/custom/feishu-publisher/scripts/send_image.py \
  --file /path/to/image.jpg --to oc_xxxxx --type chat_id

# With a text caption (sent before the image)
python3 /opt/openclaw/skills/custom/feishu-publisher/scripts/send_image.py \
  --file /path/to/image.jpg --to oc_xxxxx --type chat_id --caption "这是你要的图片"
```

**IMAGE SENDING RULE — DO NOT MAKE EXCUSES:**
- You HAVE FEISHU_APP_ID, FEISHU_APP_SECRET, and PEXELS_API_KEY in your environment.
- When asked to send an image, download it to `/tmp/<name>.jpg` and the auto-sender will deliver it to the user automatically.
- Do NOT use feishu_chat/feishu tools to send images via cards — card+image in Feishu always fails.
- Do NOT tell the user about "capabilities", "permissions", or "configuration issues".
- Do NOT invent non-existent concepts like "capabilities=none" or "channel image permissions".
- If you searched Pexels and have a result, download the image with `urllib.request.urlretrieve(url, "/tmp/photo.jpg")`.
- Do NOT refuse to send. Just download the image. The system will deliver it.

For brand assets, files are at `/opt/openclaw/configs/brand_assets/`.

You also have PEXELS_API_KEY in your environment. To search, download, and send images from Pexels:

```bash
# Step 1: Search Pexels + download best image
python3 /opt/openclaw/skills/custom/image-generator/scripts/pexels_search.py \
  --query "red sports car" --n 3 --output /tmp/pexels_result.json

# Step 2: Read result and download the first photo
python3 -c "
import json, urllib.request
data = json.load(open('/tmp/pexels_result.json'))
p = data['photos'][0]
path = f'/tmp/{p[\"id\"]}.jpg'
urllib.request.urlretrieve(p['src']['large'], path)
print(f'DOWNLOADED:{path}')
"

# Step 3: Send via Feishu (use [FeishuChatId: oc_xxxxx] from your context)
python3 /opt/openclaw/skills/custom/feishu-publisher/scripts/send_image.py \
  --file /tmp/12345.jpg --to oc_xxxxx --type chat_id --caption "红色跑车"
```

**Always search Pexels when asked for an image not in brand_assets/.**

You have FEISHU_APP_ID, FEISHU_APP_SECRET, and PEXELS_API_KEY in your environment — the scripts use them automatically.
