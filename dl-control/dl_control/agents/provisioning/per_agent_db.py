"""Per-agent Postgres database lifecycle (spec §5).

Create/drop per-agent databases and roles. Called by the provisioning service
at Tier 1 provision time and agent delete time. All SQL runs against the
owner DSN (the serving app's DSN is dl_control_app, which lacks CREATEDB).

Short8 collision: slices 8 hex chars from the agent UUID. If the unique
index on per_agent_db_name catches a collision, the next 32-bit segment is
tried (up to 3 retries — birthday collision at ~65k agents is astronomically
unlikely with a 122-bit namespace).
"""

from __future__ import annotations

import secrets

import psycopg

from dl_control.agents.provisioning.errors import ProvisioningError


class PerAgentDBCollisionError(ProvisioningError):
    """short8 collision exhausted retries."""

    def __init__(self, agent_id: str) -> None:
        super().__init__(
            "per_agent_db",
            f"short8 collision for {agent_id} after retries",
        )


def _short8(agent_id: str, segment: int = 0) -> str:
    """Return 8 hex chars from a UUID segment (spec §5.4).

    segment 0 = first 8 hex chars, segment 1 = next 8, etc.
    """
    hex_id = agent_id.replace("-", "")
    start = segment * 8
    if start + 8 > len(hex_id):
        raise PerAgentDBCollisionError(agent_id)
    return hex_id[start : start + 8]


async def create_per_agent_db(
    owner_dsn: str,
    *,
    agent_id: str,
    max_retries: int = 3,
) -> tuple[str, str, str]:
    """Create per-agent database + role. Returns (db_name, role_name, password).

    Retries on short8 collision up to max_retries. The caller is responsible
    for calling apply_per_agent_migrations after this returns.

    Raises PerAgentDBCollisionError if all segments collide.
    """
    conn = await psycopg.AsyncConnection.connect(owner_dsn, autocommit=True)
    try:
        for attempt in range(max_retries):
            short = _short8(agent_id, segment=attempt)
            db_name = f"agent_{short}"
            role_name = f"agent_role_{short}"
            password = secrets.token_hex(32)

            # Check for collision in the agents table.
            cur = await conn.execute(
                "SELECT 1 FROM agents WHERE per_agent_db_name = %s",
                (db_name,),
            )
            if await cur.fetchone():
                if attempt < max_retries - 1:
                    continue
                raise PerAgentDBCollisionError(agent_id)

            # Create role (idempotent via exception handler).
            # Use psycopg.sql for safe identifier quoting.
            rid = psycopg.sql.Identifier(role_name)
            pw = psycopg.sql.Literal(password)
            await conn.execute(
                psycopg.sql.SQL(
                    "DO $$ BEGIN "
                    "  CREATE ROLE {role} "
                    "  WITH LOGIN PASSWORD {pw} "
                    "  NOCREATEDB NOCREATEROLE NOSUPERUSER NOINHERIT; "
                    "EXCEPTION WHEN duplicate_object THEN NULL; END; $$"
                ).format(role=rid, pw=pw)
            )

            # Create database (owner is implicitly the connecting role).
            did = psycopg.sql.Identifier(db_name)
            await conn.execute(psycopg.sql.SQL("CREATE DATABASE {db}").format(db=did))

            # Grant connect to the per-agent role.
            await conn.execute(
                psycopg.sql.SQL("GRANT CONNECT ON DATABASE {db} TO {role}").format(db=did, role=rid)
            )

            return db_name, role_name, password
    finally:
        await conn.close()


async def drop_per_agent_db(
    owner_dsn: str,
    *,
    db_name: str,
    role_name: str,
) -> None:
    """Hard-drop a per-agent database and its role (spec §5.3).

    Best-effort: supresses errors for already-dropped objects.
    """
    conn = await psycopg.AsyncConnection.connect(owner_dsn, autocommit=True)
    try:
        # Terminate connections to the target DB so DROP can proceed.
        await conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (db_name,),
        )
        did = psycopg.sql.Identifier(db_name)
        rid = psycopg.sql.Identifier(role_name)
        await conn.execute(psycopg.sql.SQL("DROP DATABASE IF EXISTS {db}").format(db=did))
        await conn.execute(psycopg.sql.SQL("DROP ROLE IF EXISTS {role}").format(role=rid))
    finally:
        await conn.close()


def build_per_agent_dsn(
    db_name: str,
    role_name: str,
    password: str,
    *,
    host: str = "dato-postgres",
    port: int = 5432,
) -> str:
    """Build the DSN string for the per-agent .env (spec §5.1 step 5)."""
    from urllib.parse import quote

    return f"postgresql://{quote(role_name)}:{quote(password)}@{host}:{port}/{quote(db_name)}"


def build_per_agent_dsn_from_owner(
    owner_dsn: str,
    *,
    db_name: str,
    role_name: str,
    password: str,
) -> str:
    """Like build_per_agent_dsn but derives host/port from the owner DSN.

    Useful in tests where the host is not the production container name.
    """
    from urllib.parse import urlparse

    parsed = urlparse(owner_dsn)
    host = parsed.hostname or "localhost"
    port = parsed.port or 5432
    return build_per_agent_dsn(db_name, role_name, password, host=host, port=port)
