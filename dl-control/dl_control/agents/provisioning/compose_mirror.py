"""Write agents/generated/agents-compose.yml — an inspection mirror of
registry state (spec §11). This file is NEVER executed; container runtime is
the Docker Engine API (docker_client.py). It exists only so an operator can
inspect/replay what dl-control has provisioned.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from dl_control.agents.provisioning.fs_safety import atomic_write_text

_BANNER = (
    "# GENERATED — inspection mirror of the dato agent registry.\n"
    "# Do NOT run this file. Container lifecycle is owned by dl-control via\n"
    "# the Docker Engine API. Editing or `docker compose up`-ing this file\n"
    "# has no effect on running agents.\n"
)


def _service_block(agent: dict, *, host_agents_root: str, openclaw_image: str) -> dict:
    agent_id = agent["id"]
    host_dir = f"{host_agents_root.rstrip('/')}/{agent_id}"
    tier = agent.get("tier", "tier0")
    svc: dict = {
        "image": openclaw_image,
        "container_name": f"dato-agent-{agent_id}",
        "user": "1000:1000",
        "restart": "unless-stopped",
        "networks": ["dato_net"],
        "volumes": [
            f"{host_dir}:/home/node/.openclaw",
            f"{host_dir}/config:/app/config",
        ],
        "labels": {
            "com.docker.compose.project": "dato",
            "dato.agent.id": agent_id,
            "dato.agent.tier": tier,
        },
    }
    # Tier 1 additions (spec §10.2).
    if tier == "tier1":
        svc["dns"] = ["dl-egress-dns"]
        svc["env_file"] = [f"{host_dir}/config/.env"]
        svc["logging"] = {
            "driver": "json-file",
            "options": {"tag": f"agent_uuid={agent_id},tier=tier1"},
        }
    # Inspection-only annotations (not valid compose keys for runtime):
    svc["x-dato-status"] = agent["status"]
    svc["x-dato-container-id"] = agent.get("container_id")
    return svc


def write_compose_mirror(
    agents_root: Path | str,
    agents: list[dict],
    *,
    host_agents_root: str,
    openclaw_image: str,
) -> Path:
    """Write generated/agents-compose.yml under agents_root. Returns the path."""
    services = {
        f"dato-agent-{a['id']}": _service_block(
            a, host_agents_root=host_agents_root, openclaw_image=openclaw_image
        )
        for a in agents
    }
    doc = {"services": services, "networks": {"dato_net": {"external": True}}}
    generated_dir = Path(agents_root) / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)
    body = _BANNER + yaml.safe_dump(doc, default_flow_style=False, sort_keys=True)
    target = generated_dir / "agents-compose.yml"
    atomic_write_text(target, body)
    return target
