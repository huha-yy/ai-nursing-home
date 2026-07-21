# TOOLS.md — {{ agent.display_name }}

Skills define *how* tools work. This file describes how to use the tools available to the 知识库 agent.

**IMPORTANT**: Cognee tools are NOT registered as callable skills. Use the `process` tool
to run Python one-liners via `python3 -c`. You have `$DL_INTERNAL_TOKEN` and `httpx`
available in your environment.

## Knowledge Ingest

```python
python3 -c "
import sys; sys.path.insert(0, '/opt/openclaw/skills/custom/cognee')
from handler import add
result = add(
    content='''# 内容标题

内容正文。使用 Markdown 格式。
Tags: 产品, 智能助浴
''',
    path='products/product-name/aspect.md',
    library='company_knowledge'
)
print(result)
"
```

### Path Convention

| Category | Path Template | Example |
|----------|--------------|---------|
| Product overview | `products/<slug>/overview.md` | `products/portable-bath/overview.md` |
| Product specs | `products/<slug>/specs.md` | `products/portable-bath/specs.md` |
| Brand messaging | `brand/positioning.md` | `brand/positioning.md` |
| Brand tone | `brand/tone.md` | `brand/tone.md` |
| Company data | `company/<topic>.md` | `company/certifications.md` |
| Image assets | `assets/images/<id>.md` | `assets/images/dain-bath.md` |

### Content Format Standard

Include structured tags in each entry:

```markdown
# Title

Tags: product, bathing, portable
Category: 产品信息

Content here...
```

## Knowledge Search

```python
python3 -c "
import sys; sys.path.insert(0, '/opt/openclaw/skills/custom/cognee')
from handler import search
results = search(query='你的搜索词', limit=5, library_slugs=['company_knowledge'])
for r in results.get('results', []):
    print(f'[{r[\"library_slug\"]}] {r[\"path\"]} (dist={r[\"cosine_distance\"]:.3f})')
    print(r['text'][:500])
    print('---')
"
```

## Web Content Ingestion

When a user provides a URL with information to store:

1. Fetch the content:
   ```bash
   python3 -c "
   import httpx
   r = httpx.get('https://r.jina.ai/https://example.com/page', timeout=30)
   print(r.text[:5000])
   "
   ```

2. Extract relevant info, structure it, then store in cognee:
   ```python
   python3 -c "
   import sys; sys.path.insert(0, '/opt/openclaw/skills/custom/cognee')
   from handler import add
   add(content='...', path='products/xxx/article.md', library='company_knowledge')
   "
   ```

## Image Asset References

When storing image asset info, include the file path:
```
Path: assets/images/<id>.md
Content:
# dain-bath

Description: 戴恩智能便携助浴设备
File: /opt/openclaw/configs/brand_assets/dain-bath.jpg
Tags: product, bathing, equipment
```

## Discovery Rule

Never invent library slugs or paths. Search first to discover what knowledge already exists, then decide how to organize new data. Use `cognee.search()` with a generic query to see what's already stored.

## GBrain Knowledge Base

GBrain 是公司品牌知识库，涵盖戴恩医疗科技和永和大健康/生命优雅的官方资料。Cognee 用于向量检索（内容管线使用），GBrain 用于结构化知识管理。

### 搜索 GBrain

```python
python3 -c "
import sys; sys.path.insert(0, '/opt/openclaw/skills/custom/gbrain-mcp')
from handler import search
results = search('你的问题')
for r in results:
    print(f'[{r[\"score\"]:.3f}] {r[\"title\"]} -> {r[\"slug\"]}')
"
```

### 写入 GBrain

写入前系统会自动 lint 校验 frontmatter，必填字段缺失会拒绝写入。

新建知识前，先用模板快速生成标准格式：

```bash
# 查看可用模板
ls /opt/openclaw/scripts/templates/

# 查看某类型模板内容
cat /opt/openclaw/scripts/templates/product.md
```

模板文件会复制到项目目录作为起点，然后编辑填充具体内容。

```python
python3 -c "
import sys; sys.path.insert(0, '/opt/openclaw/skills/custom/gbrain-mcp')
from handler import put_page
content = '''---
title: 文档标题
type: product
tags: 
created: 2026-07
---

文档正文 markdown 内容...
'''
result = put_page('daien/product/xxx', content)
print(result)
"
```

### Slug 路径规范

| 品牌 | 目录 |
|------|------|
| daien/ | company, product, sales, faq, training, operations |
| yonghe/ | company, product, health, sales, faq, training, operations |
| common/ | 通用知识（允许新建子目录） |

## 文件处理

当用户在飞书给你发送文件（PDF/Docx/PPT/Excel）时，系统会自动下载、解析为 Markdown、LLM 自动判断分类，然后写入 GBrain 知识库。你不需要手动操作——完成后你会收到入库通知。

如果自动分类不准确，你可以手动用 `put_page` 重新写入正确路径。

## Send Image to Feishu Chat

Use `send_image.py`:

```bash
python3 /opt/openclaw/skills/custom/feishu-publisher/scripts/send_image.py \
  --file /opt/openclaw/configs/brand_assets/<filename>.jpg \
  --to [FeishuChatId] --type chat_id --caption "描述"
```

The `[FeishuChatId]` is available in your message context.

## Workflow Triggering

When asked to trigger the content pipeline, describe the need to the user and instruct them to contact Agent Manager. Alternatively, if you need to trigger it:

```python
python3 -c "
import httpx, os
r = httpx.Client(base_url='http://dato-control:8080',
    headers={'Authorization': f'Bearer {os.environ[\"DL_INTERNAL_TOKEN\"]}'},
    timeout=httpx.Timeout(5.0, read=30.0)
)
resp = r.post('/api/internal/admin/workflows/content.pipeline/start', json={
    'input': {'topic': '主题', 'agent_id': '7c90fc88-fd6f-452c-bb49-cc1b0ef20037'}
})
print(resp.status_code, resp.text[:2000])
"
```
