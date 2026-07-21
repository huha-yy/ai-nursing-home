"""agent_call persistence — the §5.6 conditional-UPDATE arbiters.

Every exit from posted/dispatched is ONE conditional UPDATE; the side that
gets rowcount=1 wins, and each loser has a defined consume path (the runner
re-reads and consumes; a late callback is 409'd). All functions run inside
the caller's transaction-scoped connection (the runs.py idiom) so callers
compose them atomically with run-status flips."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

IN_FLIGHT = ("posted", "dispatched")

_ROW_COLS = (
    "correlation_id, run_id, step_key, attempt, agent_id, status, "
    "request_hash, response, dispatch_count"
)


@dataclass(frozen=True)
class AgentCallRow:
    correlation_id: UUID
    run_id: UUID
    step_key: str
    attempt: int
    agent_id: UUID
    status: str
    request_hash: str
    response: dict[str, Any] | None
    dispatch_count: int


def _row(r) -> AgentCallRow:
    return AgentCallRow(*r)


async def get_latest_call(
    conn: psycopg.AsyncConnection,
    *,
    run_id: UUID,
    step_key: str,
) -> AgentCallRow | None:
    """The driver's branch point: the highest-attempt correlation for the step."""
    cur = await conn.execute(
        f"SELECT {_ROW_COLS} FROM agent_call "
        "WHERE run_id = %s AND step_key = %s ORDER BY attempt DESC LIMIT 1",
        (run_id, step_key),
    )
    r = await cur.fetchone()
    return _row(r) if r else None


async def get_call(
    conn: psycopg.AsyncConnection,
    *,
    correlation_id: UUID,
) -> AgentCallRow | None:
    cur = await conn.execute(
        f"SELECT {_ROW_COLS} FROM agent_call WHERE correlation_id = %s",
        (correlation_id,),
    )
    r = await cur.fetchone()
    return _row(r) if r else None


async def insert_call(
    conn: psycopg.AsyncConnection,
    *,
    run_id: UUID,
    step_key: str,
    agent_id: UUID,
    request_hash: str,
) -> AgentCallRow:
    """Mint the next correlation (status='posted', written BEFORE dispatch —
    §5.6). attempt = max(existing)+1: monotonic provenance that can never
    collide with history, even after a D-P13C-6 manual retry reset
    workflow_step.attempt (D-P13D-8)."""
    cur = await conn.execute(
        "INSERT INTO agent_call (run_id, step_key, attempt, agent_id, request_hash) "
        "VALUES (%s, %s, "
        " (SELECT COALESCE(MAX(attempt), 0) + 1 FROM agent_call "
        "  WHERE run_id = %s AND step_key = %s), %s, %s) "
        f"RETURNING {_ROW_COLS}",
        (run_id, step_key, run_id, step_key, agent_id, request_hash),
    )
    return _row(await cur.fetchone())


async def supersede_in_flight(
    conn: psycopg.AsyncConnection,
    *,
    run_id: UUID,
    step_key: str | None = None,
) -> int:
    """Mark in-flight correlations superseded (pre-mint hygiene; cancellation
    and the operator 'supersede' resolution — §5.6). Returns rows affected."""
    sql = "UPDATE agent_call SET status = 'superseded' WHERE run_id = %s AND status = ANY(%s)"
    params: tuple = (run_id, list(IN_FLIGHT))
    if step_key is not None:
        sql += " AND step_key = %s"
        params = (*params, step_key)
    cur = await conn.execute(sql, params)
    return cur.rowcount


async def mark_dispatched(
    conn: psycopg.AsyncConnection,
    *,
    correlation_id: UUID,
) -> bool:
    """posted → dispatched on receiver ack. Loser (already responded) is fine."""
    cur = await conn.execute(
        "UPDATE agent_call SET status = 'dispatched' "
        "WHERE correlation_id = %s AND status = 'posted'",
        (correlation_id,),
    )
    return cur.rowcount == 1


async def increment_dispatch_count(
    conn: psycopg.AsyncConnection,
    *,
    correlation_id: UUID,
) -> bool:
    """Durably count a post attempt BEFORE the HTTP call (D-P13D-8). False =
    the row left 'posted' (a response raced in) — the caller re-reads."""
    cur = await conn.execute(
        "UPDATE agent_call SET dispatch_count = dispatch_count + 1 "
        "WHERE correlation_id = %s AND status = 'posted'",
        (correlation_id,),
    )
    return cur.rowcount == 1


async def reset_dispatch_count(
    conn: psycopg.AsyncConnection,
    *,
    correlation_id: UUID,
) -> bool:
    """The repost-same-correlation resolution's fresh budget (D-P13D-11)."""
    cur = await conn.execute(
        "UPDATE agent_call SET dispatch_count = 0 WHERE correlation_id = %s AND status = 'posted'",
        (correlation_id,),
    )
    return cur.rowcount == 1


async def claim_timeout(
    conn: psycopg.AsyncConnection,
    *,
    correlation_id: UUID,
) -> bool:
    """dispatched → timed_out (the §7.1 step-4 response timeout). False =
    arbiter lost — a result exists; the caller MUST consume it (§5.6)."""
    cur = await conn.execute(
        "UPDATE agent_call SET status = 'timed_out' "
        "WHERE correlation_id = %s AND status = 'dispatched'",
        (correlation_id,),
    )
    return cur.rowcount == 1


async def mark_consumed_error(
    conn: psycopg.AsyncConnection,
    *,
    correlation_id: UUID,
) -> bool:
    """responded(error) → superseded once the failure is counted, so the
    retry claim mints fresh instead of re-consuming the same error forever
    (D-P13D-14). Responded-ok rows are never flipped — they are history."""
    cur = await conn.execute(
        "UPDATE agent_call SET status = 'superseded' "
        "WHERE correlation_id = %s AND status = 'responded'",
        (correlation_id,),
    )
    return cur.rowcount == 1


async def record_response(
    conn: psycopg.AsyncConnection,
    *,
    correlation_id: UUID,
    agent_id: UUID,
    payload: dict[str, Any],
) -> UUID | None:
    """The callback path: the §5.6 arbiter (agent identity must match — the
    correlation id alone is not an authenticator) plus the conditional run
    resume, in the caller's one transaction (§6.2). Returns the run_id when
    applied, None when the arbiter lost (stale/unknown/foreign correlation).

    The resume is conditional on waiting_agent: if the run is currently
    leased 'running' (the §13 timeout-claim race) the claim holder consumes
    the stored response — flipping the run here would fight the lease."""
    cur = await conn.execute(
        "UPDATE agent_call SET status = 'responded', response = %s, "
        "responded_at = now() "
        "WHERE correlation_id = %s AND agent_id = %s AND status = ANY(%s) "
        "RETURNING run_id",
        (Jsonb(payload), correlation_id, agent_id, list(IN_FLIGHT)),
    )
    r = await cur.fetchone()
    if r is None:
        return None
    run_id = r[0]
    await conn.execute(
        "UPDATE workflow_run SET status = 'pending', wake_at = NULL, "
        "updated_at = now() WHERE id = %s AND status = 'waiting_agent'",
        (run_id,),
    )
    return run_id
