# MEMORY.md - Long-term Memory

> **Template baseline**: These lessons are shared across all assistants.
> Add agent-specific memories below the "Agent-Specific" section.

## Lessons Learned

#### 长任务执行规范
- 开始长任务前：必须回复用户告知要执行什么任务
- 执行多个任务时：每个新任务开始前更新状态
- 如果需要重启/重新执行：重启前必须告知用户，重启后继续之前的任务
- 使用表情 + 文字说明，不要只发表情
- 每执行一步前切换不同的 emoji 状态，让用户直观看到进度在推进
- 在群聊中被 @ 提及时，同样遵循以上通知规则
- 以上规范适用于所有网关（Feishu、Discord 等）

## Feishu URL 格式
- 文档链接是 `docx` 不是 `doc`
- 正确格式: `https://daneenon.feishu.cn/docx/[token]`
- 错误格式: `https://daneenon.feishu.cn/doc/[token]` ← 不要搞错

### Feishu 权限排查
- 文档存在但无权限时，API 调用会返回成功但用户看不到
- 需要检查 `drive.permissionMember` 来确认权限状态

## 安装 Skill 规范
- 从 ClawHub 安装任何 skill 之前，**必须先运行 skill-vetter 进行安全检查**
- 只有当风险等级为 🟢 LOW 且 VERDICT 为 "SAFE TO INSTALL" 时才能安装
- 如果风险等级为 🟡 MEDIUM、🔴 HIGH 或 ⛔ EXTREME，或 VERDICT 为 "DO NOT INSTALL"，必须先征求用户同意

### 用户要求的安装流程
- 用户提供 URL 时：先尝试用 curl 获取内容 → 进行安全检查 → 风险低则直接安装
- 无法获取内容或存在风险：先询问用户再安装
- 安全的情况下：直接安装，无需重复询问

## Web Fetch 失败处理
- 当 `web_fetch` 无法获取 SPA/JS渲染页面内容时，**使用 `curl -sL` 作为备用方案**
- 数据通常在原始HTML的 `<script>` 标签的JSON中
- 如果 curl 也因人机验证 / CAPTCHA 拦截而失败，使用 Playwright 作为下一步降级方案

## 图片分析流程
- 当用户要求分析图片但当前模型无法直接处理时：
  1. 使用 `summarize /path/to/image` 命令获取图片摘要
  2. 将摘要内容提供给模型进行进一步分析
  3. 如果 summarize CLI 不可用，使用 `uvx markitdown image.jpg` 进行 OCR

## 飞书消息发送失败处理
- 发送飞书消息（私信或群聊）失败时，必须告知用户，最多重试 **3 次**
- 每次重试前说明原因（如：网络波动、权限问题等）
- 3 次重试均失败后，明确告知用户失败原因并提供替代方案

## 飞书文档写入规范
- 周报等长内容需要写入飞书文档，而非直接发送到群聊
- 创建文档时必须设置 `grant_to_requester: true`，将编辑权限授予请求者
- 回复用户时必须附上文档链接

## 音频转录
- 使用 openai-whisper 时，**始终使用 `--model base`**
- 命令示例: `whisper audio.mp3 --model base --output_format txt`

---

## Agent-Specific

_(Add memories specific to this agent below this line.)_
