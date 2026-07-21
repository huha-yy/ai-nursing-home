"""P8 precreated agents — admin opt-in drift apply.

apply_seed updates the mutable fields of an existing precreated agent
to match the current on-disk seed.  TOCTOU-guarded.  Preserves
system-managed skills (e.g., P5's cognee).
"""

from __future__ import annotations

from pathlib import Path

import structlog
from psycopg.types.json import Jsonb

from dl_control.agents import registry
from dl_control.agents.provisioning.service import AgentNotFoundError
from dl_control.agents.schemas import dedup_skills
from dl_control.audit.service import write_event
from dl_control.precreated import loader
from dl_control.precreated.errors import SeedShaConflict

logger = structlog.get_logger(__name__)

SYSTEM_MANAGED_SKILLS: frozenset[str] = frozenset({"cognee"})


def _preserve_system_skills(seed_skills: list[str], row_skills: list[str]) -> list[str]:
    out = dedup_skills(list(seed_skills))
    out_set = set(out)
    for s in row_skills:
        if s in SYSTEM_MANAGED_SKILLS and s not in out_set:
            out.append(s)
            out_set.add(s)
    return out


async def apply_seed(
    db,
    *,
    agent_id: str,
    expected_current_sha: str,
    actor_user_id: str,
    cfg,
):
    from dl_control.agents.provisioning.config_gen import regenerate_openclaw_json
    from dl_control.agents.service import _to_out

    # 1. Load + verify (no DB writes yet).
    async with db.conn(user_id=actor_user_id, role="admin") as conn:
        row = await registry.get_agent(conn, agent_id)
    if row is None:
        raise AgentNotFoundError(agent_id)
    precreated_id = row.get("precreated_id")
    if precreated_id is None:
        raise AgentNotFoundError(agent_id)

    seeds_root = Path(cfg.precreated_agents_root)
    seed = loader.load_seed(seeds_root, precreated_id)
    current_sha = loader.canonical_seed_sha(seed.raw_yaml)
    if current_sha != expected_current_sha:
        raise SeedShaConflict(
            precreated_id,
            expected=expected_current_sha,
            actual=current_sha,
        )

    # 2. Build final skill_list preserving system skills.
    new_skill_list = _preserve_system_skills(
        seed.skill_list,
        row.get("skill_list") or [],
    )

    # 3. Canonicalise model_selection (drop None values).
    model_canonical = {k: v for k, v in (seed.model_selection or {}).items() if v is not None}

    # 4. Single transaction: update apply-able fields + SHA + needs_restart + audit.
    async with db.conn(user_id=actor_user_id, role="admin") as conn:
        await registry.update_agent(
            conn,
            agent_id,
            fields={
                "display_name": seed.display_name,
                "skill_list": Jsonb(new_skill_list),
                "model_selection": Jsonb(model_canonical),
            },
        )
        await conn.execute(
            "UPDATE agents SET precreated_yaml_sha256 = %s, needs_restart = true WHERE id = %s",
            (current_sha, agent_id),
        )
        await write_event(
            conn,
            actor_user_id=actor_user_id,
            action="agent_precreated_applied",
            target=str(agent_id),
            meta={
                "precreated_id": precreated_id,
                "from_sha": row.get("precreated_yaml_sha256"),
                "to_sha": current_sha,
                "applied_fields": ["display_name", "skill_list", "model_selection"],
                "skipped_fields": ["channel_config"],
            },
        )
        row = await registry.get_agent(conn, agent_id)

    # 5. Regenerate openclaw.json after txn commits.
    regenerate_openclaw_json(cfg, row)

    return _to_out(row)
