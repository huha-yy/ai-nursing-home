"""Audit mirror reconciler — drains audit_log_outbox into per-agent DBs (spec §6).

Single-writer pattern (same as P3 pairing reconciler): flock, polling,
exponential backoff, best-effort forever. The reconciler connects to the
per-agent database using the owner DSN (the owner role can connect to any
database it created).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from urllib.parse import urlparse, urlunparse

import psycopg

from dl_control.db import Database

logger = logging.getLogger(__name__)


def _build_per_agent_dsn(owner_dsn: str, db_name: str) -> str:
    """Derive a per-agent DSN from the owner DSN by replacing the database name.

    The reconciler connects as the owner role directly to the per-agent
    database — the owner role has full access to all databases it created
    (per spec §5.1). The per-agent role is not needed here (the agent's
    .env file owns the per-agent password; the reconciler does not need it).
    """
    parsed = urlparse(owner_dsn)
    per_agent = parsed._replace(path=f"/{db_name}")
    return urlunparse(per_agent)


async def _mirror_one(per_agent_dsn: str, source_audit_log_id: int, payload: dict) -> bool:
    """INSERT one row into the per-agent audit_log_mirror table.

    Returns True on success, False on failure. UNIQUE violation on
    source_audit_log_id = already mirrored (idempotent — treat as success).
    """
    try:
        conn = await psycopg.AsyncConnection.connect(per_agent_dsn, autocommit=True)
        try:
            meta = payload.get("meta", {})
            actor = payload.get("actor_user_id")
            action = payload.get("action", "")
            target = payload.get("target", "")
            await conn.execute(
                "INSERT INTO audit_log_mirror "
                "(source_audit_log_id, actor_user_id, action, target, meta) "
                "VALUES (%s, %s::uuid, %s, %s, %s)",
                (
                    source_audit_log_id,
                    actor,
                    action,
                    target,
                    json.dumps(meta),
                ),
            )
            return True
        except psycopg.errors.UniqueViolation:
            return True  # already mirrored — idempotent
        except Exception:
            logger.exception("mirror insert failed for audit_log_id=%d", source_audit_log_id)
            return False
        finally:
            await conn.close()
    except Exception:
        logger.exception(
            "cannot connect to per-agent DB for mirror (%d)",
            source_audit_log_id,
        )
        return False


async def audit_mirror_loop(
    db: Database,
    owner_dsn: str,
    shutdown_event: asyncio.Event,
    *,
    poll_seconds: float = 2.0,
    max_retry_seconds: float = 60.0,
) -> None:
    """Poll audit_log_outbox forever, mirroring into per-agent DBs.

    Single-writer drainer. Grabs up to 50 unprocessed outbox rows per tick,
    mirrors each into the corresponding per-agent DB, and deletes the outbox
    row on success. On failure, increments the attempt counter for
    exponential-backoff retry (capped at max_retry_seconds).
    """
    while not shutdown_event.is_set():
        try:
            async with db.conn(user_id=None, role="system") as conn:
                cur = await conn.execute(
                    "SELECT o.id, o.audit_log_id, o.agent_id, o.payload, "
                    "       o.attempts, a.per_agent_db_name "
                    "FROM audit_log_outbox o "
                    "JOIN agents a ON o.agent_id = a.id "
                    "WHERE o.attempts < 10 "
                    "ORDER BY o.id LIMIT 50"
                )
                rows = await cur.fetchall()

            for row in rows:
                outbox_id, audit_log_id, _agent_id, payload, attempts, db_name = row
                if not db_name:
                    # Tier 1 agent without a provisioned DB yet — skip.
                    continue

                per_agent_dsn = _build_per_agent_dsn(owner_dsn, db_name)
                success = await _mirror_one(per_agent_dsn, audit_log_id, payload)

                async with db.conn(user_id=None, role="system") as conn:
                    if success:
                        await conn.execute(
                            "DELETE FROM audit_log_outbox WHERE id = %s",
                            (outbox_id,),
                        )
                    else:
                        await conn.execute(
                            "UPDATE audit_log_outbox "
                            "SET attempts = attempts + 1, last_error = %s "
                            "WHERE id = %s",
                            ("mirror insert failed", outbox_id),
                        )
        except Exception:
            logger.exception("audit_mirror_loop tick failed")

        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(shutdown_event.wait(), timeout=poll_seconds)
