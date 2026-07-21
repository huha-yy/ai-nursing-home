# workflow — 工作流引擎

> ⚠️ Python handler 不注册为可调用工具。不能直接调用 `workflow.start_workflow()`。
> 必须通过 **process 工具**执行 `python3 -c` 调用 handler。

dato 后台有一个完整的工作流引擎，支持编排多步骤自动化流程。
内容运营 Agent 已被授权启动 `content.pipeline` 工作流。

## 如何启动工作流

使用 process 工具执行：

```bash
python3 -c "
import sys; sys.path.insert(0, '/opt/openclaw/skills/custom/workflow')
from handler import start_workflow
import json
result = start_workflow('content.pipeline', {})
print('run_id=' + result['run_id'])
"
```

## 如何轮询进度

```bash
python3 -c "
import sys; sys.path.insert(0, '/opt/openclaw/skills/custom/workflow')
from handler import get_workflow_status
import json
status = get_workflow_status('RUN_ID_HERE')
print(json.dumps(status, indent=2, ensure_ascii=False))
"
```

## 可用工作流

| 工作流 ID | 说明 | 状态 |
|---|---|---|
| `content.pipeline` | 13 步内容生产管线（热点→飞书发布全自动） | ✅ 已授权 |
| `hr.onboarding_email` | HR 入职邮件 | ❌ 未启用 |
