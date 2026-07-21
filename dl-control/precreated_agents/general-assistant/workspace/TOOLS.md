# TOOLS.md — {{ agent.display_name }}

Skills define *how* tools work. This file describes the tools available to the 通用助手.

**IMPORTANT**: Cognee tools are NOT registered as callable skills. Use the `process` tool
to run Python one-liners via `python3 -c`. You have `$DL_INTERNAL_TOKEN` available in your environment.

## Search Knowledge

```python
python3 -c "
import sys; sys.path.insert(0, '/opt/openclaw/skills/custom/cognee')
from handler import search
results = search(query='你的搜索词', limit=5)
for r in results.get('results', []):
    print(f'  [{r[\"library_slug\"]}] {r[\"path\"]} (dist={r[\"cosine_distance\"]:.3f})')
    print('  ' + r['text'][:300])
    print()
"
```

## Save a Simple Note

```python
python3 -c "
import sys; sys.path.insert(0, '/opt/openclaw/skills/custom/cognee')
from handler import add
result = add(content='''# 备忘

用户说: ...
日期: 2026-06-26
''', path='notes/2026-06-26.md')
print(result)
"
```

## Fetch Web Content

```bash
python3 -c "
import httpx
r = httpx.get('https://r.jina.ai/https://example.com', timeout=30)
print(r.text[:3000])
"
```

## Send Image to Feishu Chat

```bash
python3 /opt/openclaw/skills/custom/feishu-publisher/scripts/send_image.py \
  --file /opt/openclaw/configs/brand_assets/<filename>.jpg \
  --to [FeishuChatId] --type chat_id --caption "描述"
```

The `[FeishuChatId]` is available in your message context.

## Routing Reference

| User wants | Action |
|-----------|--------|
| Write article / run content pipeline | Tell user to contact **内容运营** |
| Create/manage agents or workflows | Tell user to contact **Agent Manager** |
| Store company knowledge | Tell user to contact **知识库** |
| Anything else I can handle | Do it yourself with tools above |
