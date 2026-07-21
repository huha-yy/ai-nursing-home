"""dl-control → agent task-dispatch transport (spec §7, D-P13D-1/3/4).

The client half of the new transport: POST the task to the agent-side
receiver sidecar at http://dato-agent-<id>:<port>/dato/task, authenticated
with the agent's own DL_INTERNAL_TOKEN (read from the per-agent config/.env
that dl-control itself authors at provision time). dispatch_task never
raises — any failure is 'no ack', which is exactly the §7.1 ambiguous
'posted' state the runner's repost machinery handles."""

from __future__ import annotations

import asyncio
import logging
import shlex
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import httpx

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DispatchConfig:
    """Everything the runner needs to dispatch (built from Settings in main)."""

    agents_root: str
    receiver_port: int = 18790
    http_timeout_seconds: float = 10.0
    repost_backoff_seconds: float = 60.0
    repost_max: int = 3


def read_agent_internal_token(agents_root: str, agent_id: str) -> str | None:
    """Parse DL_INTERNAL_TOKEN out of the agent's config/.env. The on-disk
    value is shell-quoted (render_env_file); shlex.split decodes it — the
    same idiom as provisioning's _existing_token."""
    env_path = Path(agents_root) / agent_id / "config" / ".env"
    try:
        text = env_path.read_text()
    except OSError:
        return None
    for line in text.splitlines():
        if not line.startswith("DL_INTERNAL_TOKEN="):
            continue
        try:
            parts = shlex.split(line, posix=True)
        except ValueError:
            return None
        if parts and "=" in parts[0]:
            return parts[0].split("=", 1)[1] or None
    return None


async def dispatch_task(
    cfg: DispatchConfig,
    *,
    agent_id: UUID,
    correlation_id: UUID,
    run_id: UUID,
    step_key: str,
    message: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> bool:
    """POST one task to the agent's receiver. True iff the receiver ack'd
    (2xx). Never raises (D-P13D-4)."""
    token = await asyncio.to_thread(read_agent_internal_token, cfg.agents_root, str(agent_id))
    if not token:
        logger.warning("dispatch: no DL_INTERNAL_TOKEN for agent %s", agent_id)
        return False
    url = f"http://dato-agent-{agent_id}:{cfg.receiver_port}/dato/task"
    body = {
        "correlation_id": str(correlation_id),
        "run_id": str(run_id),
        "step_key": step_key,
        "message": message,
    }
    try:
        async with httpx.AsyncClient(
            timeout=cfg.http_timeout_seconds, transport=transport
        ) as client:
            resp = await client.post(url, json=body, headers={"Authorization": f"Bearer {token}"})
        return resp.status_code // 100 == 2
    except Exception:  # noqa: BLE001 — every failure is the no-ack state
        logger.warning("dispatch: post to agent %s failed", agent_id, exc_info=True)
        return False
