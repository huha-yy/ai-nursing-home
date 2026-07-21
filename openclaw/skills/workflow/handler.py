"""workflow skill handler — thin HTTP shim to dl-control (workflow spec §7.2).

Mirrors the cognee skill: the agent authenticates with its per-agent
DL_INTERNAL_TOKEN; dl-control resolves the token to an agent_id, authorizes
start against workflow_agent_grant, and scopes get/await on the run's
durable started_by_agent_id owner column."""

import os
import time

import httpx

DL_INTERNAL_TOKEN = os.environ["DL_INTERNAL_TOKEN"]
DL_CONTROL_URL = os.environ.get("DL_CONTROL_URL", "http://dato-control:8080")

TERMINAL_STATUSES = ("succeeded", "failed", "cancelled")


def _client():
    return httpx.Client(
        base_url=DL_CONTROL_URL,
        headers={"Authorization": f"Bearer {DL_INTERNAL_TOKEN}"},
        timeout=httpx.Timeout(5.0, read=30.0),
    )


def start_workflow(name: str, input: dict | None = None, correlation_key: str | None = None):
    """Start a workflow run (requires an admin-managed grant)."""
    body: dict = {"input": input or {}}
    if correlation_key:
        body["correlation_key"] = correlation_key
    with _client() as c:
        r = c.post(f"/api/v1/workflows/{name}/runs", json=body)
        r.raise_for_status()
        return r.json()


def get_workflow_status(run_id: str):
    """Status of a run this agent started."""
    with _client() as c:
        r = c.get(f"/api/v1/workflow-runs/{run_id}")
        r.raise_for_status()
        return r.json()


def await_workflow(run_id: str, timeout_s: float = 600.0, poll_s: float = 5.0):
    """Poll until terminal (succeeded/failed/cancelled); TimeoutError at the
    deadline. Polling client-side keeps dl-control free of long-poll state
    (D-P13D-12)."""
    deadline = time.monotonic() + timeout_s
    while True:
        doc = get_workflow_status(run_id)
        if doc.get("status") in TERMINAL_STATUSES:
            return doc
        if time.monotonic() >= deadline:
            raise TimeoutError(f"workflow run {run_id} not terminal after {timeout_s}s")
        time.sleep(poll_s)
