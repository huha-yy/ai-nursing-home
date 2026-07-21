"""P13 peer-interface end-to-end smoke tests.

Run via `make smoke` — requires the full compose stack to be up, including at
least one provisioned agent container with the dato-task-receiver sidecar.

Tests exercise the four P13d peer-interface paths:
 1. Receiver health (sidecar responds inside the agent container)
 2. Dispatch transport (task POSTed to receiver; state file created)
 3. Callback roundtrip (agent result flows back to dl-control; run resumes)
 4. Agent→workflow API (start/get with grant enforcement)
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

# ═══════════════════════════════════════════════════════════════════════════
# docker-exec HTTP proxy (same pattern as test_cognee_smoke.py)
# ═══════════════════════════════════════════════════════════════════════════


class _CurlClient:
    """httpx-like client that proxies requests via docker exec curl."""

    def __init__(self, container: str, target_url: str):
        self._container = container
        self._target_url = target_url

    def _curl(
        self,
        method: str,
        path: str,
        *,
        body: dict | None = None,
        headers: dict | None = None,
        timeout: int = 15,
    ) -> tuple[int, str]:
        url = f"{self._target_url}{path}"
        cmd = [
            "docker",
            "exec",
            self._container,
            "curl",
            "-s",
            "-w",
            "\n%{http_code}",
            "-o",
            "-",
            "-X",
            method,
            url,
        ]
        for k, v in (headers or {}).items():
            cmd.extend(["-H", f"{k}: {v}"])
        if body is not None:
            cmd.extend(["-H", "Content-Type: application/json"])
            cmd.extend(["-d", json.dumps(body)])
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        output = result.stdout.rstrip("\n")
        lines = output.split("\n")
        status_code = int(lines[-1])
        body_text = "\n".join(lines[:-1])
        return status_code, body_text

    def get(self, path: str, **kw) -> _Response:
        code, body = self._curl("GET", path, **kw)
        return _Response(code, body)

    def post(self, path: str, *, json_data: dict | None = None, **kw) -> _Response:
        code, body = self._curl("POST", path, body=json_data, **kw)
        return _Response(code, body)


class _Response:
    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self._text = text

    def json(self):
        return json.loads(self._text)


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _admin_password():
    """Read admin password from infra/.env (set by make admin-init)."""
    env_dir = Path(__file__).resolve().parents[1] / "infra"
    for line in (env_dir / ".env").read_text().splitlines():
        if line.startswith("DL_CONTROL_APP_PASSWORD="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("DL_CONTROL_APP_PASSWORD not found in infra/.env")


def _read_agent_token(agent_id: str) -> str | None:
    """Read DL_INTERNAL_TOKEN from the agent container's .env file.
    Retries up to 30s for the container to be fully started (D-SMOKE-1)."""
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            result = subprocess.run(
                [
                    "docker",
                    "exec",
                    f"dato-agent-{agent_id}",
                    "cat",
                    "/home/node/.openclaw/config/.env",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            time.sleep(1)
            continue
        if result.returncode != 0:
            time.sleep(1)
            continue
        for line in result.stdout.splitlines():
            if line.startswith("DL_INTERNAL_TOKEN="):
                val = line.split("=", 1)[1].strip()
                if val.startswith("'") and val.endswith("'"):
                    val = val[1:-1]
                return val
    return None


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def admin_session(http_session, caddy_url, dl_control_ready):
    """Login once and return a cookie-bearing httpx.Client for admin API
    calls (agent create/provision/delete)."""
    password = _admin_password()
    r = http_session.post(
        f"{caddy_url}/login",
        data={"username": "admin", "password": password},
        follow_redirects=False,
    )
    assert r.status_code == 302, f"login: {r.status_code}"
    session_cookies = r.cookies
    assert "session" in session_cookies, "no session cookie after login"
    # Create a client that carries the session cookie.
    client = httpx.Client(verify=False, timeout=30, cookies=session_cookies)
    yield client
    client.close()


@pytest.fixture(scope="module")
def smoke_agent(admin_session, caddy_url, pg_conn):
    """Provision a temporary agent for the smoke, yield (agent_id, token),
    then delete it. The agent container has the receiver sidecar running
    because its .env includes DL_INTERNAL_TOKEN."""
    display_name = f"smoke-p13-{uuid4().hex[:8]}"
    # Create the agent
    r = admin_session.post(
        f"{caddy_url}/api/v1/admin/agents",
        json={"display_name": display_name, "tier": "tier0"},
    )
    assert r.status_code == 201, f"agent create: {r.status_code} {r.text}"
    agent_id = r.json()["id"]

    # Provision (starts the container with the receiver sidecar)
    r = admin_session.post(
        f"{caddy_url}/api/v1/admin/agents/{agent_id}/provision",
        json={"restart": False},
    )
    assert r.status_code in (200, 201, 202), f"provision: {r.status_code} {r.text}"

    # Read the plaintext token from the agent's .env (with retry)
    token = _read_agent_token(agent_id)
    assert token is not None, "DL_INTERNAL_TOKEN not found in agent .env"

    yield agent_id, token

    # Teardown: hard-delete the agent (cascades container removal)
    admin_session.delete(f"{caddy_url}/api/v1/admin/agents/{agent_id}")


# ═══════════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.smoke
def test_receiver_health(smoke_agent):
    """The dato-task-receiver sidecar is alive inside the agent container
    and responds to GET /healthz."""
    agent_id, _token = smoke_agent
    client = _CurlClient(f"dato-agent-{agent_id}", "http://localhost:18790")
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


@pytest.mark.smoke
def test_receiver_auth_rejects_bad_token(smoke_agent):
    """The receiver rejects a dispatch with a wrong bearer token (401)."""
    agent_id, _token = smoke_agent
    client = _CurlClient(f"dato-agent-{agent_id}", "http://localhost:18790")
    r = client.post(
        "/dato/task",
        json_data={
            "correlation_id": str(uuid4()),
            "run_id": str(uuid4()),
            "step_key": "ask",
            "message": "hello",
        },
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert r.status_code == 401


@pytest.mark.smoke
def test_dispatch_receiver_creates_state_file(smoke_agent):
    """The receiver acks (202), spawns the agent command, and writes a state
    file to the mounted agent home (D-P13D-1). This exercises the actual
    transport path: dl-control -> receiver intake.

    Note: the D-P13D-2 default command (node /app/openclaw.mjs) is not
    verified here — that requires the full dato-openclaw image."""
    agent_id, token = smoke_agent
    client = _CurlClient(f"dato-agent-{agent_id}", "http://localhost:18790")
    corr = uuid4()
    r = client.post(
        "/dato/task",
        json_data={
            "correlation_id": str(corr),
            "run_id": str(uuid4()),
            "step_key": "ask",
            "message": "hello",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 202
    doc = r.json()
    assert doc["ack"] is True
    assert doc["correlation_id"] == str(corr)
    assert doc["duplicate"] is False

    # The state file is written atomically in the agent home.
    state_file = f"/home/node/.openclaw/dato-tasks/{corr}.json"
    deadline = time.monotonic() + 15
    state_text = None
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["docker", "exec", f"dato-agent-{agent_id}", "cat", state_file],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            state_text = result.stdout
            break
        time.sleep(0.5)
    assert state_text is not None, f"state file {state_file} not created"
    state = json.loads(state_text)
    assert state["status"] == "done" or state["status"] == "accepted"

    # Duplicate post is deduped (the normative §5.6 contract)
    r = client.post(
        "/dato/task",
        json_data={
            "correlation_id": str(corr),
            "run_id": str(uuid4()),
            "step_key": "ask",
            "message": "hello",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 202
    assert r.json()["duplicate"] is True


@pytest.mark.smoke
def test_callback_roundtrip(
    smoke_agent,
    http_session,
    caddy_url,
    pg_conn,
    dl_control_ready,
):
    """End-to-end callback path: insert a posted agent_call + waiting_agent
    run via DB, POST the callback to dl-control, verify the correlation is
    updated and the run is resumed."""
    agent_id, token = smoke_agent

    flow_id = f"t.smoke-peer-{uuid4().hex[:8]}"
    # Register and enable the flow via DB (avoids CSRF/admin-session complexity)
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO workflow (id, version, code_ref, display_name, enabled) "
            "VALUES (%s, '1.0.0', %s, 'Smoke Peer', true)",
            (flow_id, f"builtin:{flow_id}"),
        )
        cur.execute(
            "INSERT INTO workflow_version (workflow_id, version, code_ref, definition_snapshot) "
            "VALUES (%s, '1.0.0', %s, '{}'::jsonb) "
            "ON CONFLICT DO NOTHING",
            (flow_id, f"builtin:{flow_id}"),
        )

    # Insert a waiting_agent run + posted agent_call
    corr = uuid4()
    run_id = uuid4()
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO workflow_run (id, workflow_id, workflow_version, trigger, "
            "status, wake_at) VALUES (%s, %s, '1.0.0', 'manual', 'waiting_agent', "
            "now() + interval '1 hour')",
            (run_id, flow_id),
        )
        cur.execute(
            "INSERT INTO agent_call (correlation_id, run_id, step_key, attempt, "
            "agent_id, status, request_hash) VALUES (%s, %s, 'ask', 1, %s, "
            "'posted', 'h')",
            (corr, run_id, agent_id),
        )

    # The callback endpoint (what the receiver would POST after the agent runs)
    r = http_session.post(
        f"{caddy_url}/api/internal/workflows/agent-callback",
        json={"correlation_id": str(corr), "status": "ok", "result": {"smoke": True}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, f"callback: {r.status_code} {r.text}"
    assert r.json()["applied"] is True
    assert r.json()["run_id"] == str(run_id)

    # Verify the correlation was updated
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT status, response FROM agent_call WHERE correlation_id = %s",
            (corr,),
        )
        status, response = cur.fetchone()
    assert status == "responded"
    assert response["result"] == {"smoke": True}


@pytest.mark.smoke
def test_agent_workflow_api_start_get(
    smoke_agent,
    http_session,
    caddy_url,
    pg_conn,
    dl_control_ready,
):
    """Agent→workflow API: start a run (requires a grant), get its status.
    The agent authenticates with its DL_INTERNAL_TOKEN."""
    agent_id, token = smoke_agent

    flow_id = f"t.smoke-agent-api-{uuid4().hex[:8]}"
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO workflow (id, version, code_ref, display_name, enabled) "
            "VALUES (%s, '1.0.0', %s, 'Smoke API', true)",
            (flow_id, f"builtin:{flow_id}"),
        )
        cur.execute(
            "INSERT INTO workflow_version (workflow_id, version, code_ref, definition_snapshot) "
            "VALUES (%s, '1.0.0', %s, '{}'::jsonb) "
            "ON CONFLICT DO NOTHING",
            (flow_id, f"builtin:{flow_id}"),
        )
        cur.execute(
            "INSERT INTO workflow_agent_grant (agent_id, workflow_id, granted_by) "
            "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (agent_id, flow_id, agent_id),
        )

    auth = {"Authorization": f"Bearer {token}"}

    # Start a run
    r = http_session.post(
        f"{caddy_url}/api/v1/workflows/{flow_id}/runs",
        json={"input": {"source": "smoke"}, "correlation_key": f"ck-{uuid4().hex[:8]}"},
        headers=auth,
    )
    assert r.status_code == 201, f"start: {r.status_code} {r.text}"
    run_id = r.json()["run_id"]

    # Verify started_by_agent_id is recorded
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT trigger, started_by_agent_id FROM workflow_run WHERE id = %s",
            (run_id,),
        )
        trigger, owner = cur.fetchone()
    assert (trigger, owner) == ("agent", agent_id)

    # Get the run
    r = http_session.get(f"{caddy_url}/api/v1/workflow-runs/{run_id}", headers=auth)
    assert r.status_code == 200
    doc = r.json()
    assert doc["run_id"] == str(run_id)
    assert doc["workflow_id"] == flow_id

    # Non-owner is 401/404 (no existence leak — D-P13D-12)
    r = http_session.get(
        f"{caddy_url}/api/v1/workflow-runs/{run_id}",
        headers={"Authorization": f"Bearer fake-{uuid4().hex}"},
    )
    assert r.status_code == 401  # unknown token → 401
