"""Agent registry CRUD business logic.

Every mutation runs inside Database.conn(user_id=actor_user_id, role='admin')
so the registry write and its non-null-actor audit row are atomic and
RLS-valid (spec §8.2).

Before deleting a Tier 1 agent, back up its per-agent database:
    pg_dump -d agent_<short8> > /backups/agent_<short8>_$(date -Iseconds).sql
"""

from __future__ import annotations

import contextlib
import shutil
import uuid
from pathlib import Path

from psycopg.types.json import Jsonb

from dl_control.agents import registry
from dl_control.agents.provisioning import compose_mirror
from dl_control.agents.provisioning.per_agent_db import drop_per_agent_db
from dl_control.agents.schemas import AgentCreate, AgentOut, AgentUpdate
from dl_control.audit.service import write_event
from dl_control.db import Database
from dl_control.secrets_redaction import assert_no_secrets


class Tier1NotAvailableError(RuntimeError):
    """Tier 1 provisioning is not available until P4 (spec §8.2)."""


def _to_out(row: dict) -> AgentOut:
    precreated_id = row.pop("precreated_id", None)
    stored_sha = row.pop("precreated_yaml_sha256", None)
    current_sha = None
    drift = False
    removed = False
    if precreated_id is not None:
        from dl_control.precreated.reconciler import get_current_seed_sha

        current_sha = get_current_seed_sha(precreated_id)
        if current_sha is None:
            removed = True
        elif stored_sha != current_sha:
            drift = True
    return AgentOut(
        **row,
        precreated_id=precreated_id,
        precreated_yaml_sha256=stored_sha,
        precreated_current_sha=current_sha,
        precreated_source_drift=drift,
        precreated_source_removed=removed,
    )


async def create_agent(db: Database, *, actor_user_id: str, req: AgentCreate) -> AgentOut:
    # channel_config must never carry a secret (spec §8.4).
    assert_no_secrets(req.channel_config)
    agent_id = uuid.uuid4()
    async with db.conn(user_id=actor_user_id, role="admin") as conn:
        await registry.insert_agent(conn, agent_id=agent_id, req=req)
        await write_event(
            conn,
            actor_user_id=actor_user_id,
            action="agent_created",
            target=str(agent_id),
            meta={"display_name": req.display_name, "tier": req.tier},
        )
        row = await registry.get_agent(conn, agent_id)
    return _to_out(row)


async def update_agent(
    db: Database, *, actor_user_id: str, agent_id: str, req: AgentUpdate
) -> AgentOut | None:
    if req.channel_config is not None:
        assert_no_secrets(req.channel_config)
    fields: dict = {}
    if req.display_name is not None:
        fields["display_name"] = req.display_name
    if req.skill_list is not None:
        fields["skill_list"] = Jsonb(req.skill_list)
    if req.channel_config is not None:
        fields["channel_config"] = Jsonb(req.channel_config)
    if req.model_selection is not None:
        fields["model_selection"] = Jsonb(req.model_selection.model_dump(exclude_none=True))
    async with db.conn(user_id=actor_user_id, role="admin") as conn:
        if await registry.get_agent(conn, agent_id) is None:
            return None
        await registry.update_agent(conn, agent_id, fields=fields)
        await write_event(
            conn,
            actor_user_id=actor_user_id,
            action="agent_updated",
            target=str(agent_id),
            meta={"fields": sorted(fields.keys())},
        )
        row = await registry.get_agent(conn, agent_id)
    return _to_out(row)


async def update_agent_skills(
    db: Database,
    *,
    actor_user_id: str,
    agent_id: str,
    skill_list: list[str],
    cfg,
) -> None:
    """Update an agent's skill_list in the DB, regenerate skills.yaml,
    and set needs_restart = true.

    Unlike update_agent (which only hits the DB), this also materializes
    the new skills.yaml on disk so the agent picks up the change on restart.
    """
    from dl_control.agents.provisioning.config_gen import render_skills_yaml
    from dl_control.agents.provisioning.fs_safety import atomic_write_text

    from pathlib import Path

    fields: dict = {"skill_list": Jsonb(skill_list)}
    async with db.conn(user_id=actor_user_id, role="admin") as conn:
        if await registry.get_agent(conn, agent_id) is None:
            raise ValueError(f"Agent {agent_id} not found")
        await registry.update_agent(conn, agent_id, fields=fields)
        await write_event(
            conn,
            actor_user_id=actor_user_id,
            action="agent_skills_updated",
            target=str(agent_id),
            meta={"skill_count": len(skill_list)},
        )

    # Regenerate skills.yaml on disk + mark needs_restart
    agent_dir = Path(cfg.agents_root) / agent_id
    atomic_write_text(
        agent_dir / "skills.yaml",
        render_skills_yaml(skill_list),
        agent_dir=agent_dir,
    )
    async with db.conn(user_id=actor_user_id, role="admin") as conn:
        await conn.execute(
            "UPDATE agents SET needs_restart = true WHERE id = %s",
            (agent_id,),
        )


async def delete_agent(
    db: Database,
    *,
    actor_user_id: str,
    agent_id: str,
    docker=None,
    cfg=None,
) -> bool:
    import httpx

    async with db.conn(user_id=actor_user_id, role="admin") as conn:
        existing = await registry.get_agent(conn, agent_id)
        if existing is None:
            return False

        # P5: Clear internal_token_hash first to prevent in-flight verify calls.
        await conn.execute(
            "UPDATE agents SET internal_token_hash = NULL WHERE id = %s",
            (agent_id,),
        )

        # P5: Query owned libraries and call dl-cognee DELETE for each.
        cur = await conn.execute(
            "SELECT slug, storage_kind, per_library_db_name, per_library_db_role "
            "FROM knowledge_libraries WHERE owner_agent_id = %s",
            (agent_id,),
        )
        owned_libraries = await cur.fetchall()
        if owned_libraries and cfg is not None and cfg.cognee_admin_token:
            for lib_slug, _lib_storage, _lib_db_name, _lib_db_role in owned_libraries:
                try:
                    async with httpx.AsyncClient() as http:
                        resp = await http.delete(
                            f"{cfg.cognee_url.rstrip('/')}/v1/library/{lib_slug}",
                            headers={"Authorization": f"Bearer {cfg.cognee_admin_token}"},
                            timeout=10,
                        )
                        if resp.status_code not in (200, 404):
                            import logging

                            logging.getLogger(__name__).warning(
                                "dl-cognee DELETE /v1/library/%s returned %d",
                                lib_slug,
                                resp.status_code,
                            )
                except Exception:
                    import logging

                    logging.getLogger(__name__).warning(
                        "dl-cognee DELETE /v1/library/%s failed",
                        lib_slug,
                        exc_info=True,
                    )

        precreated_id = existing.get("precreated_id")
        if precreated_id is not None:
            cur = await conn.execute(
                "INSERT INTO precreated_suppressions "
                "(precreated_id, suppressed_by) VALUES (%s, %s) "
                "ON CONFLICT (precreated_id) DO NOTHING",
                (precreated_id, actor_user_id),
            )
            if cur.rowcount == 1:
                await write_event(
                    conn,
                    actor_user_id=actor_user_id,
                    action="precreated_suppressed",
                    target=precreated_id,
                    meta={"agent_id": agent_id},
                )
        await registry.delete_agent(conn, agent_id)
        await write_event(
            conn,
            actor_user_id=actor_user_id,
            action="agent_deleted",
            target=str(agent_id),
            meta={
                "display_name": existing["display_name"],
                "precreated_id": precreated_id,
            },
        )
    # Tier 1 cleanup (best-effort).
    if (
        docker is not None
        and cfg is not None
        and existing.get("tier") == "tier1"
        and existing.get("per_agent_db_name")
    ):
        per_agent_db_name = existing["per_agent_db_name"]
        per_agent_db_role = existing.get("per_agent_db_role")
        container_id = existing.get("container_id")
        name = f"dato-agent-{agent_id}"
        # 1. Remove Docker container.
        if container_id:
            with contextlib.suppress(Exception):
                await docker.remove_container(
                    audit=lambda *_: None,
                    name=name,
                    container_id=container_id,
                )
        # 2. Drop per-agent DB + role.
        if cfg.owner_dsn:
            with contextlib.suppress(Exception):
                await drop_per_agent_db(
                    cfg.owner_dsn,
                    db_name=per_agent_db_name,
                    role_name=per_agent_db_role,
                )
        # 3. Remove agent directory.
        agent_dir = Path(cfg.agents_root) / agent_id
        if agent_dir.is_dir():
            with contextlib.suppress(Exception):
                shutil.rmtree(agent_dir)
        # 4. Refresh compose mirror.
        with contextlib.suppress(Exception):
            async with db.conn(user_id=actor_user_id, role="admin") as conn:
                agents = await registry.list_agents(conn)
            compose_mirror.write_compose_mirror(
                cfg.agents_root,
                agents,
                host_agents_root=cfg.host_agents_root,
                openclaw_image=cfg.openclaw_image,
            )
    return True


async def list_agents(db: Database, *, actor_user_id: str) -> list[AgentOut]:
    async with db.conn(user_id=actor_user_id, role="admin") as conn:
        return [_to_out(r) for r in await registry.list_agents(conn)]


async def get_agent(db: Database, *, actor_user_id: str, agent_id: str) -> AgentOut | None:
    async with db.conn(user_id=actor_user_id, role="admin") as conn:
        row = await registry.get_agent(conn, agent_id)
    return _to_out(row) if row else None
