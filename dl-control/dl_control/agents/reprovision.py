"""P6 — re-render + restart helper for Tier 1 agents.

Used at upgrade time when openclaw.json.j2 changes (e.g., P6 atomic landing
flips apiKey from DL_INTERNAL_API_KEY to DL_INTERNAL_TOKEN). Running Tier 1
agents need their generated config re-rendered and their container restarted.

CURRENT_TEMPLATE_VERSION is bumped when the template changes; the startup hook
reprovisions any agent below the current version.

Safe to call multiple times: rows whose `last_rendered_hash` matches the
current render are skipped (no-op), and the `template_version` column is
advanced.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import structlog

from dl_control.agents.provisioning import config_gen
from dl_control.agents.provisioning.service import (
    ProvisioningConfig,
    _generate_config_set,
)
from dl_control.db import Database

logger = structlog.get_logger()

# Bump this when templates/openclaw.json.j2 changes in a way that requires
# existing Tier 1 agents to be re-rendered + restarted. P6 introduces v1.
CURRENT_TEMPLATE_VERSION = 1


def _hash_rendered(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def _noop(_action: str, _target: str, _meta: dict) -> None:
    pass


async def reprovision_tier1_agents(
    *,
    db: Database,
    docker,
    cfg: ProvisioningConfig,
    reason: str,
) -> dict:
    """Re-render + restart every active Tier 1 agent.

    Returns a summary dict: `{reprovisioned: [...], skipped: [...], failed: [...]}`.
    """
    log = logger.bind(op="reprovision_tier1", reason=reason)

    async with db.conn(user_id=None, role="admin") as conn:
        cur = await conn.execute(
            "SELECT id, display_name, tier, skill_list, channel_config, "
            "       model_selection, status, container_id, "
            "       template_version, last_rendered_hash "
            "FROM agents "
            "WHERE tier = 'tier1' AND status = 'active' "
            "ORDER BY created_at"
        )
        rows = await cur.fetchall()
    columns = [
        "id",
        "display_name",
        "tier",
        "skill_list",
        "channel_config",
        "model_selection",
        "status",
        "container_id",
        "template_version",
        "last_rendered_hash",
    ]
    agents = [dict(zip(columns, r, strict=False)) for r in rows]

    summary: dict = {"reprovisioned": [], "skipped": [], "failed": []}

    for row in agents:
        agent_id = str(row["id"])
        try:
            agent_dir = Path(cfg.agents_root) / agent_id
            if not agent_dir.is_dir():
                summary["skipped"].append(agent_id)
                continue

            # Render current template to compute hash.
            existing = None
            openclaw_path = agent_dir / "openclaw.json"
            try:
                if openclaw_path.exists():
                    from dl_control.agents.provisioning.fs_safety import (
                        read_managed_text,
                    )

                    existing = read_managed_text(
                        openclaw_path,
                        agent_dir=agent_dir,
                    )
            except Exception:
                pass

            new_text = config_gen.render_openclaw_json(
                cfg.templates_root,
                row,
                site_host=cfg.site_host,
                existing_json=existing,
                default_model=cfg.local_llm_default_model,
            )
            new_hash = _hash_rendered(new_text)

            if (
                row["last_rendered_hash"] == new_hash
                and row["template_version"] == CURRENT_TEMPLATE_VERSION
            ):
                summary["skipped"].append(agent_id)
                continue

            # Apply: write to disk, restart container, advance bookkeeping.
            _generate_config_set(cfg, agent_dir, row)
            container_id = row.get("container_id")
            restart_ok = True
            if container_id:
                name = f"dato-agent-{agent_id}"
                try:
                    await docker.stop_container(
                        audit=_noop,
                        name=name,
                        container_id=container_id,
                    )
                    await docker.start_container(
                        audit=_noop,
                        name=name,
                        container_id=container_id,
                    )
                except Exception as exc:
                    log.warning("container_restart_failed", agent_id=agent_id, error=str(exc))
                    restart_ok = False

            if not restart_ok:
                summary["failed"].append(
                    {"agent_id": agent_id, "error": "container restart failed"}
                )
                continue

            async with db.conn(user_id=None, role="admin") as conn:
                await conn.execute(
                    "UPDATE agents SET last_rendered_hash = %s, "
                    "template_version = %s, needs_restart = false WHERE id = %s",
                    (new_hash, CURRENT_TEMPLATE_VERSION, agent_id),
                )

            summary["reprovisioned"].append(agent_id)
            log.info("reprovisioned", agent_id=agent_id)
        except Exception as exc:
            log.error("reprovision_failed", agent_id=agent_id, error=str(exc))
            summary["failed"].append({"agent_id": agent_id, "error": str(exc)})

    return summary
