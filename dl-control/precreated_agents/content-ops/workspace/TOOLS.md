# TOOLS.md — 内容运营工具指引

⚠️ **所有 Python handler 都必须通过 process 工具执行，不能直接调用。**

## workflow 技能（工作流调度）

内容运营 Agent 已被授权启动 `content.pipeline` 工作流。

### 启动工作流（不传 topic → 自动走 RSS 热点监控）
使用 process 工具执行：
```bash
python3 -c "
import sys; sys.path.insert(0, '/opt/openclaw/skills/custom/workflow')
from handler import start_workflow
import json
result = start_workflow('content.pipeline', {})
run_id = result['run_id']
print(f'run_id={run_id}')
print(json.dumps(result, indent=2))
"
```

### 轮询进度
```bash
python3 -c "
import sys; sys.path.insert(0, '/opt/openclaw/skills/custom/workflow')
from handler import get_workflow_status
import json, time
status = get_workflow_status('RUN_ID_HERE')
print(json.dumps(status, indent=2, ensure_ascii=False))
if status.get('status') in ('succeeded','failed','cancelled'):
    print('工作流已结束')
"
```

## cognee 技能（知识库搜索）
```bash
python3 -c "
import sys; sys.path.insert(0, '/opt/openclaw/skills/custom/cognee')
from handler import search
import json
results = search(query='品牌定位', limit=5, library_slugs=['company_knowledge'])
for r in results:
    print(f'[{r[\"library_slug\"]}] {r[\"path\"]} (dist={r[\"cosine_distance\"]:.3f})')
"
```

## ppt-master（生成专业 PPT）
```bash
# 从 Markdown 生成 PPTX（快速模式：不提问，一步到底）
python3 /opt/openclaw/skills/custom/ppt-master/scripts/project_manager.py \
  --source /tmp/article.md \
  --title "PPT标题" \
  --theme theme01 \
  --output /tmp/output.pptx

# 完整工作流：使用 ppt-master 的快速模式（默认走 SKILL.md 快速模式）
# 生成后务必用 send_file.py 发送给用户
```

## ComfyUI MCP（AI 生图 — 快速模式 hd=False, 20-60s）
```bash
# 通过 MCP tool 调用，不要直接跑脚本
# 调用 generate_image(prompt="...", hd=False)
# hd=False 足够作 PPT 配图，单张 20-60 秒
```

## send_file（发送 PPT/PDF/Word 文件到飞书）
```bash
# 私聊
python3 /home/node/.openclaw/comfy-mcp/send_file.py \
  --file /tmp/xxx.pptx \
  --to "[FeishuSenderOpenId]" \
  --type open_id \
  --caption "📊 PPT 已生成"

# 群聊（用 [FeishuChatId] 占位符，OpenClaw 自动替换）
python3 /home/node/.openclaw/comfy-mcp/send_file.py \
  --file /tmp/xxx.pptx \
  --to "[FeishuChatId]" \
  --type chat_id \
  --caption "📊 PPT 已生成"
```

## feishu-publisher（发送图片到飞书）
```bash
python3 /opt/openclaw/skills/feishu-publisher/scripts/send_image.py \
  --file /tmp/image.jpg \
  --to "[FeishuChatId]" \
  --type chat_id \
  --caption "图片说明"
```

## web-content-fetcher（联网查资讯）
```bash
curl -sS "https://r.jina.ai/https://example.com" -A "Mozilla/5.0"
```

## vision-ocr（图片识别）
```bash
python3 -c "import sys; sys.path.insert(0,'/opt/openclaw/skills/custom/vision-ocr'); from handler import analyze; print(analyze('/tmp/image.jpg'))"
```
