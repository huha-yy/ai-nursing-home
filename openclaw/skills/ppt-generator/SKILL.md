---
name: ppt-generator
description: Markdown 文章/大纲 → PPT 演示文稿。自动按章节分页，支持多种配色方案
metadata: { "clawdbot": { "emoji": "📊" } }
user-invocable: true
---

# ppt-generator — Markdown → PPT 生成器

## 用法

接收一篇 markdown 文章或大纲，生成可下载的 .pptx 文件。

```bash
python3 /opt/openclaw/skills/custom/ppt-generator/scripts/generate_ppt.py \
  --input <文章.md> \
  --output <输出.pptx> \
  --theme <配色方案> \
  --brand-name <品牌名>
```

### 参数说明
| 参数 | 必填 | 说明 |
|------|------|------|
| `--input` | ✅ | 输入 markdown 文件路径 |
| `--output` | ✅ | 输出 .pptx 文件路径 |
| `--theme` | ❌ | 配色方案：`科技蓝`（默认）、`商务`、`简约` |
| `--brand-name` | ❌ | 品牌名，显示在封面副标题 |

### 支持的 markdown 结构
```
# 文章标题（→ 封面）
## 第一章（→ 章节过渡页 + 内容页）
正文内容...
### 小节（→ 单独内容页）
## 第二章（→ 章节过渡页 + 内容页）
```

## 示例

**场景 1:** 把一篇公众号文章做成 PPT
```bash
python3 /opt/openclaw/skills/custom/ppt-generator/scripts/generate_ppt.py \
  --input outputs/xxx/publish_package/wechat/article.md \
  --output /tmp/xxx.pptx \
  --theme 科技蓝 \
  --brand-name "永和大健康"
```

**场景 2:** 用用户提供的大纲生成
```bash
# 先把用户说的话写成大纲文件
echo "# 健康管理新趋势\n\n## AI 赋能\n- 智能穿戴\n- 数据分析\n" > /tmp/outline.md

# 生成 PPT
python3 /opt/openclaw/skills/custom/ppt-generator/scripts/generate_ppt.py \
  --input /tmp/outline.md \
  --output /tmp/health_ai.pptx \
  --theme 商务
```

## 输出与交付

1. 生成成功后，输出路径会打印出来
2. **用飞书发送文件给用户（重要）：** 不要只回复路径

   如果是**群聊**对话，从上下文中找到 `[FeishuChatId: oc_xxxxx]`：
   ```bash
   python3 /home/node/.openclaw/comfy-mcp/send_file.py \
     --file /tmp/xxx.pptx \
     --to "[FeishuChatId]" \
     --type chat_id \
     --caption "📊 这是您要的 PPT 文件，请查收～"
   ```

   如果是**私聊**，从飞书消息记录中找到用户的 `open_id`（格式为 `ou_xxx`）：
   ```bash
   python3 /home/node/.openclaw/comfy-mcp/send_file.py \
     --file /tmp/xxx.pptx \
     --to "ou_xxxxxxxxxxxxxxxxxxxxxxxxx" \
     --type open_id \
     --caption "📊 这是您要的 PPT 文件，请查收～"
   ```
   open_id 可以从 `~/.openclaw/agents/main/sessions/` 的对话记录中找到。

3. 发送成功后回复"文件已发送，请查收～"
