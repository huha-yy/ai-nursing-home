# admin-mgmt

Administer the dato appliance: manage agents, workflows, schedules, and view
workflow runs. This is the Agent Manager's primary toolset for managing the
system through conversation.

## Tools

### admin-mgmt.list_agents
List all agents on the appliance.

Parameters: None

Returns: `{agents: [{id, display_name, tier, status, skill_list, created_at, ...}]}`

### admin-mgmt.get_agent
Get details for a specific agent.

Parameters:
- `agent_id` (string, required): The UUID of the agent.

Returns: `{id, display_name, tier, status, skill_list, channel_config, ...}`
Errors: 404 if the agent does not exist.

### admin-mgmt.create_agent
Create a new agent (always confirm tier and settings with the admin).

Parameters:
- `display_name` (string, required): Human-readable name (1-200 chars).
- `tier` (string, optional, default "tier0"): "tier0" or "tier1".
- `skill_list` (list of string, optional): Skills to enable for this agent.
- `channel_config` (object, optional): Channel configuration.
- `model_provider` (string, optional): LLM provider (e.g. "deepseek").
- `model_id` (string, optional): Model name (e.g. "deepseek-v4-pro").

Returns: `{id, display_name, tier, status, skill_list, ...}`
Errors: 422 if validation fails.

### admin-mgmt.delete_agent
Delete an agent. Always confirm with the admin before deleting.

Parameters:
- `agent_id` (string, required): The UUID of the agent to delete.

Errors: 404 if the agent does not exist.

### admin-mgmt.restart_agent
Restart an agent (regenerate config and restart container).

Parameters:
- `agent_id` (string, required): The UUID of the agent to restart.

Returns: `{restarted: true, agent_id: "..."}`
Errors: 404 if not found, 409 if busy, 500 on provisioning error.

### admin-mgmt.list_workflows
List all workflows on the appliance.

Parameters: None

Returns: `{workflows: [{id, display_name, description, enabled, ...}]}`

### admin-mgmt.get_workflow
Get details for a specific workflow.

Parameters:
- `workflow_id` (string, required): The workflow id (e.g. "content.pipeline").

Returns: `{id, display_name, description, enabled, latest_version, ...}`
Errors: 404 if the workflow does not exist.

### admin-mgmt.list_schedules
List cron schedules for a workflow.

Parameters:
- `workflow_id` (string, required): The workflow id.

Returns: `{schedules: [{id, cron, input_template, enabled, next_fire_at, ...}]}`

### admin-mgmt.create_schedule
Create a cron schedule for a workflow.

Parameters:
- `workflow_id` (string, required): The workflow id.
- `cron` (string, required): Cron expression in Asia/Shanghai timezone.
- `input_template` (object, optional): JSON input to pass on each fire.

Returns: `{schedule_id: "..."}`
Errors: 422 if the cron expression is invalid.

### admin-mgmt.delete_schedule
Delete a cron schedule.

Parameters:
- `workflow_id` (string, required): The workflow id.
- `schedule_id` (string, required): The UUID of the schedule to delete.

Errors: 404 if the schedule does not exist.

### admin-mgmt.start_workflow
Start a workflow run immediately (bypasses agent grants — any workflow can be
started without prior grant configuration).

Parameters:
- `workflow_id` (string, required): The workflow id (e.g. "content.pipeline").
- `input` (object, optional): Run input parameters as a JSON object.
  For `content.pipeline`, the input object supports these fields:
  - `topic` (required): 文章主题
  - `brand` (optional, default `"daien"`): 品牌标识
    - `"daien"` = 戴恩医疗科技（默认）
    - `"yonghe"` = 永和大健康/生命优雅
    - Just pass the brand slug from the user's request — **do not research the brand**,
      the pipeline loads all brand-specific config automatically.
  - `agent_id` (optional): 内容运营 Agent UUID（自动预填）
- `correlation_key` (string, optional): Business dedup key — at most one live
  run per (workflow, key).

Returns: `{run_id: "..."}`
Errors: 404 if the workflow does not exist, 409 if disabled or a live run
already exists for the correlation key.

Examples:
- 戴恩文章（默认）：`{"input": {"topic": "养老政策新变化"}}`
- 永和文章：`{"input": {"brand": "yonghe", "topic": "AI家庭健康管理趋势"}}`

### admin-mgmt.get_workflow_run
Get full workflow run detail including step outputs and errors.

Parameters:
- `run_id` (string, required): The UUID of the workflow run.

Returns: `{run: {id, workflow_id, status, ...}, steps: [{step_key, status, output, error, ...}], approvals, ledger, agent_calls}`
Errors: 404 if the run does not exist.

### admin-mgmt.list_workflow_runs
List recent workflow runs.

Parameters:
- `workflow_id` (string, optional): Filter by workflow.
- `limit` (int, optional, default 50): Max results.

Returns: `{runs: [{id, workflow_id, status, trigger, created_at, ...}]}`
