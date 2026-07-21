"""admin-mgmt skill handler — thin HTTP shim to dl-control admin internal API.

Enables Agent Manager to manage agents, workflows, and schedules through
conversation.  Follows the same pattern as workflow/handler.py: the agent
authenticates with its per-agent DL_INTERNAL_TOKEN; dl-control resolves the
token to an agent_id and authorizes only the precreated agent-manager.

NOTE: DL_INTERNAL_TOKEN is resolved lazily (inside _client()) so that
OpenClaw's skill-discovery scan can import this module without the env
var being set yet at scan time.
"""

import os

import httpx

_DL_CONTROL_URL = os.environ.get("DL_CONTROL_URL", "http://dato-control:8080")


def _token():
    """Lazy resolve of DL_INTERNAL_TOKEN — avoids crashes during skill scan."""
    return os.environ["DL_INTERNAL_TOKEN"]


def _client():
    return httpx.Client(
        base_url=_DL_CONTROL_URL,
        headers={"Authorization": f"Bearer {_token()}"},
        timeout=httpx.Timeout(5.0, read=30.0),
    )

# ── Agent management ────────────────────────────────────────────────


def list_agents():
    """List all agents on the appliance.

    Returns: {agents: [{id, display_name, tier, status, skill_list, ...}]}
    """
    with _client() as c:
        r = c.get("/api/internal/admin/agents")
        r.raise_for_status()
        return r.json()


def get_agent(agent_id: str):
    """Get details for a specific agent.

    Parameters:
    - agent_id (string, required): The UUID of the agent.

    Returns: {id, display_name, tier, status, skill_list, channel_config, ...}
    Errors: 404 if the agent does not exist.
    """
    with _client() as c:
        r = c.get(f"/api/internal/admin/agents/{agent_id}")
        r.raise_for_status()
        return r.json()


def create_agent(
    display_name: str,
    tier: str = "tier0",
    skill_list: list | None = None,
    channel_config: dict | None = None,
    model_provider: str | None = None,
    model_id: str | None = None,
):
    """Create a new agent (always confirm tier, skills, and settings with the
    admin before creating).

    Parameters:
    - display_name (string, required): Human-readable name (1-200 chars).
    - tier (string, optional, default "tier0"): "tier0" or "tier1".
    - skill_list (list of string, optional): Skills to enable.
    - channel_config (object, optional): Channel configuration.
    - model_provider (string, optional): LLM provider (e.g. "deepseek").
    - model_id (string, optional): Model name (e.g. "deepseek-v4-pro").

    Returns: {id, display_name, tier, status, skill_list, ...}
    Errors: 422 if validation fails.
    """
    body: dict = {"display_name": display_name, "tier": tier}
    if skill_list is not None:
        body["skill_list"] = skill_list
    if channel_config is not None:
        body["channel_config"] = channel_config
    model_selection = {}
    if model_provider is not None:
        model_selection["provider"] = model_provider
    if model_id is not None:
        model_selection["model"] = model_id
    if model_selection:
        body["model_selection"] = model_selection
    with _client() as c:
        r = c.post("/api/internal/admin/agents", json=body)
        r.raise_for_status()
        return r.json()


def delete_agent(agent_id: str):
    """Delete an agent. Always confirm with the admin before deleting.

    Parameters:
    - agent_id (string, required): The UUID of the agent to delete.

    Errors: 404 if the agent does not exist.
    """
    with _client() as c:
        r = c.delete(f"/api/internal/admin/agents/{agent_id}")
        if r.status_code == 404:
            raise ValueError(f"Agent {agent_id} not found")
        r.raise_for_status()


def restart_agent(agent_id: str):
    """Restart an agent (regenerate config and restart container).

    Parameters:
    - agent_id (string, required): The UUID of the agent to restart.

    Returns: {restarted: true, agent_id: "..."}
    Errors: 404 if not found, 409 if busy, 500 on provisioning error.
    """
    with _client() as c:
        r = c.post(f"/api/internal/admin/agents/{agent_id}/restart")
        r.raise_for_status()
        return r.json()


# ── Workflow management ─────────────────────────────────────────────


def list_workflows():
    """List all workflows on the appliance.

    Returns: {workflows: [{id, display_name, description, enabled, ...}]}
    """
    with _client() as c:
        r = c.get("/api/internal/admin/workflows")
        r.raise_for_status()
        return r.json()


def get_workflow(workflow_id: str):
    """Get details for a specific workflow.

    Parameters:
    - workflow_id (string, required): The workflow id (e.g. "content.pipeline").

    Returns: {id, display_name, description, enabled, latest_version, ...}
    Errors: 404 if the workflow does not exist.
    """
    with _client() as c:
        r = c.get(f"/api/internal/admin/workflows/{workflow_id}")
        r.raise_for_status()
        return r.json()


# ── Schedule management ─────────────────────────────────────────────


def list_schedules(workflow_id: str):
    """List cron schedules for a workflow.

    Parameters:
    - workflow_id (string, required): The workflow id.

    Returns: {schedules: [{id, cron, input_template, enabled, next_fire_at, ...}]}
    """
    with _client() as c:
        r = c.get(f"/api/internal/admin/workflows/{workflow_id}/schedules")
        r.raise_for_status()
        return r.json()


def create_schedule(workflow_id: str, cron: str, input_template: dict | None = None):
    """Create a cron schedule for a workflow.

    Parameters:
    - workflow_id (string, required): The workflow id.
    - cron (string, required): Cron expression in Asia/Shanghai timezone.
    - input_template (object, optional): JSON input to pass on each fire.

    Returns: {schedule_id: "..."}
    Errors: 422 if the cron expression is invalid.
    """
    body: dict = {"cron": cron, "input_template": input_template or {}}
    with _client() as c:
        r = c.post(f"/api/internal/admin/workflows/{workflow_id}/schedules", json=body)
        r.raise_for_status()
        return r.json()


def delete_schedule(workflow_id: str, schedule_id: str):
    """Delete a cron schedule.

    Parameters:
    - workflow_id (string, required): The workflow id.
    - schedule_id (string, required): The UUID of the schedule to delete.

    Errors: 404 if the schedule does not exist.
    """
    with _client() as c:
        r = c.delete(f"/api/internal/admin/workflows/{workflow_id}/schedules/{schedule_id}")
        if r.status_code == 404:
            raise ValueError(f"Schedule {schedule_id} not found")
        r.raise_for_status()


def start_workflow(workflow_id: str, input: dict | None = None, correlation_key: str | None = None):
    """Start a workflow run immediately (bypasses agent grants).

    Parameters:
    - workflow_id (string, required): The workflow id (e.g. "content.pipeline").
    - input (object, optional): Run input parameters as a JSON object.
    - correlation_key (string, optional): Business dedup key.

    Returns: {run_id: "..."}
    Errors: 404 if the workflow does not exist, 409 if disabled or a live run
    already exists for the correlation key.
    """
    body: dict = {"input": input or {}}
    if correlation_key:
        body["correlation_key"] = correlation_key
    with _client() as c:
        r = c.post(f"/api/internal/admin/workflows/{workflow_id}/start", json=body)
        r.raise_for_status()
        return r.json()


# ── Workflow run queries ────────────────────────────────────────────


def list_workflow_runs(workflow_id: str | None = None, limit: int = 50):
    """List recent workflow runs.

    Parameters:
    - workflow_id (string, optional): Filter by workflow.
    - limit (int, optional, default 50): Max results.

    Returns: {runs: [{id, workflow_id, status, trigger, created_at, ...}]}
    """
    params: dict = {}
    if workflow_id:
        params["workflow_id"] = workflow_id
    params["limit"] = str(limit)
    with _client() as c:
        r = c.get("/api/internal/admin/workflow-runs", params=params)
        r.raise_for_status()
        return r.json()


def get_workflow_run(run_id: str):
    """Get full workflow run detail including step outputs, errors, and
    approval status.  Use this after a workflow completes to retrieve
    published article URLs from the final step output.

    Parameters:
    - run_id (string, required): The UUID of the workflow run.

    Returns: {run: {id, workflow_id, status, ...},
              steps: [{step_key, status, output, error, ...}],
              approvals: [...],
              ledger: [...],
              agent_calls: [...]}
    Errors: 404 if the run does not exist.
    """
    with _client() as c:
        r = c.get(f"/api/internal/admin/workflow-runs/{run_id}")
        r.raise_for_status()
        return r.json()
