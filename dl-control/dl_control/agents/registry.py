"""SQL helper functions for the agents table.

Each takes a transaction-scoped connection from Database.conn(); none opens
its own transaction.
"""

from __future__ import annotations

import uuid

import psycopg
from psycopg.types.json import Jsonb

from dl_control.agents.schemas import AgentCreate

_COLUMNS = (
    "id",
    "display_name",
    "tier",
    "skill_list",
    "channel_config",
    "model_selection",
    "status",
    "created_at",
    "updated_at",
    "container_id",
    "pairing_version",
    "last_projection_version",
    "last_projection_hash",
    "needs_restart",
    "feishu_configured",
    "per_agent_db_name",
    "per_agent_db_role",
    "internal_token_hash",
    "cognee_authz_version",
    "precreated_id",
    "precreated_yaml_sha256",
)
# Columns an update may legitimately set (guards the dynamic UPDATE).
_UPDATABLE = frozenset({"display_name", "skill_list", "channel_config", "model_selection"})


def _row_to_dict(row: tuple) -> dict:
    data = dict(zip(_COLUMNS, row, strict=True))
    data["id"] = str(data["id"])
    data["created_at"] = data["created_at"].isoformat()
    data["updated_at"] = data["updated_at"].isoformat()
    data["needs_restart"] = bool(data.get("needs_restart", False))
    data["feishu_configured"] = bool(data.get("feishu_configured", False))
    data["cognee_authz_version"] = int(data.get("cognee_authz_version", 0))
    # bytea fields → hex string for JSON-safe serialization
    for key in ("internal_token_hash", "last_projection_hash"):
        val = data.get(key)
        if isinstance(val, (bytes, memoryview)):
            data[key] = bytes(val).hex()
    return data


async def insert_agent(
    conn: psycopg.AsyncConnection,
    *,
    agent_id: uuid.UUID,
    req: AgentCreate,
    precreated_id: str | None = None,
    precreated_yaml_sha256: str | None = None,
    current_openclaw_digest: str | None = None,
) -> None:
    columns = [
        "id",
        "display_name",
        "tier",
        "skill_list",
        "channel_config",
        "model_selection",
        "precreated_id",
        "precreated_yaml_sha256",
    ]
    values: list = [
        str(agent_id),
        req.display_name,
        req.tier,
        Jsonb(req.skill_list),
        Jsonb(req.channel_config),
        Jsonb(req.model_selection.model_dump(exclude_none=True)),
        precreated_id,
        precreated_yaml_sha256,
    ]
    if current_openclaw_digest is not None:
        columns.append("current_openclaw_digest")
        values.append(current_openclaw_digest)
    await conn.execute(
        f"INSERT INTO agents ({', '.join(columns)}) VALUES ({', '.join('%s' for _ in columns)})",
        tuple(values),
    )


async def get_agent(conn: psycopg.AsyncConnection, agent_id) -> dict | None:
    cur = await conn.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM agents WHERE id = %s",
        (str(agent_id),),
    )
    row = await cur.fetchone()
    return _row_to_dict(row) if row else None


async def list_agents(conn: psycopg.AsyncConnection) -> list[dict]:
    cur = await conn.execute(f"SELECT {', '.join(_COLUMNS)} FROM agents ORDER BY created_at DESC")
    return [_row_to_dict(r) for r in await cur.fetchall()]


async def update_agent(conn: psycopg.AsyncConnection, agent_id, *, fields: dict) -> None:
    if not fields:
        return
    bad = set(fields) - _UPDATABLE
    if bad:
        raise ValueError(f"non-updatable column(s): {sorted(bad)}")
    assignments = ", ".join(f"{column} = %s" for column in fields)
    await conn.execute(
        f"UPDATE agents SET {assignments} WHERE id = %s",
        (*fields.values(), str(agent_id)),
    )


async def delete_agent(conn: psycopg.AsyncConnection, agent_id) -> None:
    await conn.execute("DELETE FROM agents WHERE id = %s", (str(agent_id),))


async def count_agents_by_tier(conn: psycopg.AsyncConnection) -> dict[str, int]:
    cur = await conn.execute("SELECT tier, count(*) FROM agents GROUP BY tier")
    return {r[0]: r[1] for r in await cur.fetchall()}


async def claim_status(
    conn: psycopg.AsyncConnection,
    agent_id,
    *,
    new_status: str,
    allowed_from: tuple[str, ...],
) -> bool:
    """Compare-and-swap the status. Returns True iff exactly one row moved
    (the agent was in an allowed-from state and is now new_status). The
    atomicity is the WHERE clause — see spec §5.3."""
    cur = await conn.execute(
        "UPDATE agents SET status = %s WHERE id = %s AND status = ANY(%s)",
        (new_status, str(agent_id), list(allowed_from)),
    )
    return cur.rowcount == 1


async def set_status(conn: psycopg.AsyncConnection, agent_id, *, status: str) -> None:
    """Unconditionally set the status (used for terminal active/error
    transitions inside a flow that already holds the agent)."""
    await conn.execute(
        "UPDATE agents SET status = %s WHERE id = %s",
        (status, str(agent_id)),
    )


async def set_container_id(
    conn: psycopg.AsyncConnection, agent_id, *, container_id: str | None
) -> None:
    """Record (or clear) the Docker container id."""
    await conn.execute(
        "UPDATE agents SET container_id = %s WHERE id = %s",
        (container_id, str(agent_id)),
    )


async def set_openclaw_digest(
    conn: psycopg.AsyncConnection, agent_id, *, digest: str | None
) -> None:
    """Record (or clear) the current OpenClaw image digest."""
    await conn.execute(
        "UPDATE agents SET current_openclaw_digest = %s WHERE id = %s",
        (digest, str(agent_id)),
    )


async def list_agents_in_status(conn: psycopg.AsyncConnection, status: str) -> list[dict]:
    """All agents currently in the given status (startup reconciliation)."""
    cur = await conn.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM agents WHERE status = %s",
        (status,),
    )
    return [_row_to_dict(r) for r in await cur.fetchall()]
