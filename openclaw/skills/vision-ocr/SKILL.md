---
name: vision-ocr
description: 图片 OCR 与 AI 视觉理解 — 提取图片中的文字，或用 AI 深度分析图片内容
metadata: { "clawdbot": { "emoji": "👁️" } }
user-invocable: true
---

# vision-ocr — 图片文字识别与理解

## 功能
1. **OCR（文字提取）** — 从图片中提取可见文字，适合扫描件、截图、标语等
2. **AI 深度分析** — 用视觉 AI 模型理解图片内容（产品、场景、颜色、品牌等）

## 用法

### OCR 提取文字（快速）
```bash
# Python one-liner（handler 不注册为 callable tool，必须走 process）
python3 -c "import sys; sys.path.insert(0,'/opt/openclaw/skills/custom/vision-ocr'); from handler import ocr; print(ocr('图片路径'))"
```

返回提取到的文字，每行一条。如果没有文字，返回 `[未检测到文字]`。

### AI 深度分析图片
```bash
python3 -c "import sys; sys.path.insert(0,'/opt/openclaw/skills/custom/vision-ocr'); from handler import analyze; print(analyze('图片路径', '你的问题'))"
```

- `question` 可选，默认"描述图片内容"
- 你可以问："这张图里有什么产品？""这张图的品牌风格是什么？""这张截图里显示了什么数据？"
- 支持中英文

## 注意事项
- 图片路径在容器内，由 feishu-bot 下载到 `/tmp/` 目录
- 如果用户发了图片但你没看到路径，检查消息中的 `[图片路径: /tmp/...]` 标记
- OCR 适合提取文字，AI 分析适合理解内容
- 大图片（>20MB）可能处理较慢，可先告知用户
- easyocr 首次加载需要约 10-20 秒（模型初始化），后续调用很快
