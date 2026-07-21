"""Audit-log writer with defensive secret redaction.

write_event redacts meta before the INSERT (spec §7.2) — leaking a secret
into the audit log is structurally prevented, not discipline-dependent.
The caller supplies an open transaction-scoped connection (Database.conn);
this function never opens its own transaction.
"""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from dl_control.secrets_redaction import redact


async def write_event(
    conn: psycopg.AsyncConnection,
    *,
    actor_user_id: str | None,
    action: str,
    target: str | None = None,
    meta: dict[str, Any] | None = None,
) -> int:
    """INSERT one audit row; return its id. meta is redacted first.

    A non-null actor_user_id requires the connection's app.current_user_id
    GUC to equal it (audit_log_insert_self policy) — see spec §6.3.
    """
    safe_meta = redact(meta or {})
    cur = await conn.execute(
        "INSERT INTO audit_log (actor_user_id, action, target, meta) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        (actor_user_id, action, target, Jsonb(safe_meta)),
    )
    row = await cur.fetchone()
    return row[0]
