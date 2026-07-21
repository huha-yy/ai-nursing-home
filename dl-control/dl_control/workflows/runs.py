"""workflow_run / workflow_step / workflow_approval persistence (spec §5–§6.2).

Every post-claim run mutation is guarded by ``lease_owner = worker AND
status = 'running'`` — a guard miss raises LeaseLostError so a usurped writer
can never double-advance a run (spec §13 concurrency). All functions run
inside the caller's transaction-scoped connection (Database.conn) and never
open their own; multi-statement functions are therefore atomic with their
caller's transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from dl_control.audit.service import write_event
from dl_control.workflows.errors import (
    ApprovalNotPendingError,
    DuplicateActiveRunError,
    LeaseLostError,
    UnknownWorkflowError,
    WorkflowDisabledError,
)


@dataclass(frozen=True)
class ClaimedRun:
    """The columns the driver needs from a freshly claimed run."""

    id: UUID
    workflow_id: str
    workflow_version: str
    input: dict[str, Any]
    current_step: str | None


async def start_run(
    conn: psycopg.AsyncConnection,
    *,
    workflow_id: str,
    trigger: str,
    run_input: dict[str, Any] | None = None,
    correlation_key: str | None = None,
    started_by_agent_id: UUID | None = None,
    actor_user_id: UUID | None = None,
) -> UUID:
    """The single run-creation primitive (P13c triggers and the P13d agent API
    both call this). Pins the run to the flow's latest_version (spec §9).

    Raises UnknownWorkflowError / WorkflowDisabledError / DuplicateActiveRunError.
    A raise aborts the caller's transaction — call last, or in its own txn.
    """
    cur = await conn.execute(
        "SELECT enabled, latest_version FROM workflow WHERE id = %s",
        (workflow_id,),
    )
    row = await cur.fetchone()
    if row is None:
        raise UnknownWorkflowError(workflow_id)
    enabled, latest = row
    if not enabled:
        raise WorkflowDisabledError(workflow_id)
    if latest is None:
        raise UnknownWorkflowError(f"{workflow_id}: no version registered")
    try:
        cur = await conn.execute(
            "INSERT INTO workflow_run (workflow_id, workflow_version, trigger, "
            "input, correlation_key, started_by_agent_id) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (
                workflow_id,
                latest,
                trigger,
                Jsonb(run_input or {}),
                correlation_key,
                started_by_agent_id,
            ),
        )
    except UniqueViolation as exc:  # uq_workflow_active_run (§5.2)
        raise DuplicateActiveRunError(f"{workflow_id}/{correlation_key}") from exc
    run_id = (await cur.fetchone())[0]
    await write_event(
        conn,
        actor_user_id=actor_user_id,
        action="workflow.run_started",
        target=str(run_id),
        meta={"workflow": workflow_id, "trigger": trigger, "correlation_key": correlation_key},
    )
    return run_id


# Spec §6.2, verbatim shape: pending, expired-lease running, or wake-gated
# waiting states whose durable deadline has passed (incl. the P13d agent-call
# timeout — listing waiting_agent now means P13d adds no claim change).
_CLAIM_SQL = """
UPDATE workflow_run
   SET lease_owner = %(worker)s,
       lease_expires_at = now() + make_interval(secs => %(ttl)s),
       status = 'running',
       updated_at = now()
 WHERE id = (
   SELECT id FROM workflow_run
    WHERE status = 'pending'
       OR (status = 'running' AND lease_expires_at < now())
       OR (status IN ('waiting_timer','waiting_retry','waiting_agent')
           AND wake_at IS NOT NULL AND wake_at <= now())
    ORDER BY COALESCE(wake_at, created_at)
    FOR UPDATE SKIP LOCKED
    LIMIT 1)
 RETURNING id, workflow_id, workflow_version, input, current_step
"""


async def claim_next_run(
    conn: psycopg.AsyncConnection,
    *,
    worker: str,
    ttl_seconds: int,
) -> ClaimedRun | None:
    """Atomically claim the most-due runnable run (Postgres-authoritative
    lease, spec §6.2). Returns None when nothing is due."""
    cur = await conn.execute(_CLAIM_SQL, {"worker": worker, "ttl": ttl_seconds})
    row = await cur.fetchone()
    if row is None:
        return None
    return ClaimedRun(
        id=row[0],
        workflow_id=row[1],
        workflow_version=row[2],
        input=row[3],
        current_step=row[4],
    )


async def renew_lease(
    conn: psycopg.AsyncConnection,
    *,
    run_id: UUID,
    worker: str,
    ttl_seconds: int,
) -> bool:
    """Extend the lease during long work (§6.2). False = lease lost."""
    cur = await conn.execute(
        "UPDATE workflow_run "
        "SET lease_expires_at = now() + make_interval(secs => %s) "
        "WHERE id = %s AND lease_owner = %s AND status = 'running'",
        (ttl_seconds, run_id, worker),
    )
    return cur.rowcount == 1


async def begin_step(
    conn: psycopg.AsyncConnection,
    *,
    run_id: UUID,
    worker: str,
    step_key: str,
    ttl_seconds: int,
) -> tuple[str, int, datetime | None]:
    """Guarded step entry — one transaction (Codex r1): renew the lease,
    persist current_step (so first-step waits are observable), and ensure the
    step row exists. Returns the PRIOR (status, attempt, started_at) — the
    timer recompute (D-P13B-4) needs the pre-existing started_at.
    Raises LeaseLostError on a guard miss."""
    cur = await conn.execute(
        "UPDATE workflow_run SET current_step = %s, "
        "lease_expires_at = now() + make_interval(secs => %s), updated_at = now() "
        "WHERE id = %s AND lease_owner = %s AND status = 'running'",
        (step_key, ttl_seconds, run_id, worker),
    )
    if cur.rowcount != 1:
        raise LeaseLostError(f"run {run_id}: begin_step lost the lease")
    await conn.execute(
        "INSERT INTO workflow_step (run_id, step_key) VALUES (%s, %s) "
        "ON CONFLICT (run_id, step_key) DO NOTHING",
        (run_id, step_key),
    )
    cur = await conn.execute(
        "SELECT status, attempt, started_at FROM workflow_step WHERE run_id = %s AND step_key = %s",
        (run_id, step_key),
    )
    return await cur.fetchone()


async def mark_step_running(
    conn: psycopg.AsyncConnection,
    *,
    run_id: UUID,
    worker: str,
    step_key: str,
    bump_attempt: bool,
) -> int:
    """Move the step to 'running', lease-guarded — a stale worker must not
    bump attempts or enter handler execution (Codex r1). The guard is an
    UPDATE on workflow_run (taking its row lock, so it serializes against a
    concurrent reclaim and re-evaluates after it commits); a joined snapshot
    read would race a reclaim committing mid-statement (Codex task-3 P1).
    Handler executions bump attempt; timer arming does not. started_at is
    COALESCE-kept: it is the durable timer anchor (D-P13B-4). Returns the
    (possibly bumped) attempt; raises LeaseLostError on a guard miss."""
    cur = await conn.execute(
        "UPDATE workflow_run SET updated_at = now() "
        "WHERE id = %s AND lease_owner = %s AND status = 'running'",
        (run_id, worker),
    )
    if cur.rowcount != 1:
        raise LeaseLostError(f"run {run_id}: step start lost the lease")
    cur = await conn.execute(
        "UPDATE workflow_step SET status = 'running', attempt = attempt + %s, "
        "started_at = COALESCE(started_at, now()) "
        "WHERE run_id = %s AND step_key = %s RETURNING attempt",
        (1 if bump_attempt else 0, run_id, step_key),
    )
    return (await cur.fetchone())[0]


async def load_outputs(
    conn: psycopg.AsyncConnection,
    *,
    run_id: UUID,
) -> dict[str, Any]:
    """Outputs of every succeeded step, keyed by step_key (handler context)."""
    cur = await conn.execute(
        "SELECT step_key, output FROM workflow_step WHERE run_id = %s AND status = 'succeeded'",
        (run_id,),
    )
    return {r[0]: r[1] for r in await cur.fetchall()}


async def park_run(
    conn: psycopg.AsyncConnection,
    *,
    run_id: UUID,
    worker: str,
    status: str,
    wake_at: datetime | None,
    step_key: str | None = None,
    step_error: str | None = None,
) -> None:
    """Move a leased 'running' run into a waiting state, persisting the wake
    deadline and releasing the lease in one statement (§6.2 step 5).

    Clock-bound waits (waiting_timer/waiting_retry/waiting_agent — the latter
    is the P13d agent-call timeout, enforced now so it can never be parked
    strandable) MUST carry wake_at; waiting_approval/waiting_manual MUST NOT
    (resumed only by an externally-driven one-transaction flip)."""
    if status in ("waiting_timer", "waiting_retry", "waiting_agent") and wake_at is None:
        raise ValueError(f"{status} requires wake_at")
    if status in ("waiting_approval", "waiting_manual") and wake_at is not None:
        raise ValueError(f"{status} must not set wake_at")
    cur = await conn.execute(
        "UPDATE workflow_run SET status = %s, wake_at = %s, "
        "lease_owner = NULL, lease_expires_at = NULL, updated_at = now() "
        "WHERE id = %s AND lease_owner = %s AND status = 'running'",
        (status, wake_at, run_id, worker),
    )
    if cur.rowcount != 1:
        raise LeaseLostError(f"run {run_id}: park to {status} lost the lease")
    if step_error is not None and step_key is not None:
        await conn.execute(
            "UPDATE workflow_step SET error = %s WHERE run_id = %s AND step_key = %s",
            (step_error, run_id, step_key),
        )


async def park_retry(
    conn: psycopg.AsyncConnection,
    *,
    run_id: UUID,
    worker: str,
    step_key: str,
    error: str,
    next_attempt_at: datetime,
) -> None:
    """Known-clean failure backoff: run → waiting_retry with wake_at, step →
    pending with next_attempt_at mirroring it (spec §5.3/§6.2 step 4)."""
    await park_run(
        conn,
        run_id=run_id,
        worker=worker,
        status="waiting_retry",
        wake_at=next_attempt_at,
    )
    await conn.execute(
        "UPDATE workflow_step SET status = 'pending', error = %s, "
        "next_attempt_at = %s WHERE run_id = %s AND step_key = %s",
        (error, next_attempt_at, run_id, step_key),
    )


async def advance(
    conn: psycopg.AsyncConnection,
    *,
    run_id: UUID,
    worker: str,
    step_key: str,
    output: Any,
    next_step: str,
) -> None:
    """Step success + current_step move + wake_at clear — one transaction,
    guarded (W4: persist after each step)."""
    cur = await conn.execute(
        "UPDATE workflow_run SET current_step = %s, wake_at = NULL, "
        "updated_at = now() "
        "WHERE id = %s AND lease_owner = %s AND status = 'running'",
        (next_step, run_id, worker),
    )
    if cur.rowcount != 1:
        raise LeaseLostError(f"run {run_id}: advance lost the lease")
    await conn.execute(
        "UPDATE workflow_step SET status = 'succeeded', output = %s, "
        "finished_at = now() WHERE run_id = %s AND step_key = %s",
        (Jsonb(output), run_id, step_key),
    )


async def finish_run(
    conn: psycopg.AsyncConnection,
    *,
    run_id: UUID,
    worker: str,
    status: str,
    step_key: str | None = None,
    step_status: str | None = None,
    output: Any = None,
    error: str | None = None,
) -> None:
    """Terminal transition (succeeded / failed / cancelled), guarded;
    finalizes the current step row in the same transaction when given."""
    cur = await conn.execute(
        "UPDATE workflow_run SET status = %s, wake_at = NULL, finished_at = now(), "
        "lease_owner = NULL, lease_expires_at = NULL, updated_at = now() "
        "WHERE id = %s AND lease_owner = %s AND status = 'running'",
        (status, run_id, worker),
    )
    if cur.rowcount != 1:
        raise LeaseLostError(f"run {run_id}: finish as {status} lost the lease")
    if step_key is not None and step_status is not None:
        await conn.execute(
            "UPDATE workflow_step SET status = %s, output = %s, error = %s, "
            "finished_at = now() WHERE run_id = %s AND step_key = %s",
            (step_status, Jsonb(output) if output is not None else None, error, run_id, step_key),
        )


async def release_run(
    conn: psycopg.AsyncConnection,
    *,
    run_id: UUID,
    worker: str,
) -> None:
    """Graceful-drain release: put a leased 'running' run back to 'pending' so
    a restart resumes it immediately instead of waiting out the lease TTL."""
    cur = await conn.execute(
        "UPDATE workflow_run SET status = 'pending', lease_owner = NULL, "
        "lease_expires_at = NULL, updated_at = now() "
        "WHERE id = %s AND lease_owner = %s AND status = 'running'",
        (run_id, worker),
    )
    if cur.rowcount != 1:
        raise LeaseLostError(f"run {run_id}: release lost the lease")


async def ensure_approval(
    conn: psycopg.AsyncConnection,
    *,
    run_id: UUID,
    step_key: str,
    prompt: str,
) -> str:
    """Idempotently create the approval row; return its current state
    ('pending' | 'approved' | 'rejected')."""
    await conn.execute(
        "INSERT INTO workflow_approval (run_id, step_key, prompt) "
        "VALUES (%s, %s, %s) ON CONFLICT (run_id, step_key) DO NOTHING",
        (run_id, step_key, prompt),
    )
    cur = await conn.execute(
        "SELECT state FROM workflow_approval WHERE run_id = %s AND step_key = %s",
        (run_id, step_key),
    )
    return (await cur.fetchone())[0]


async def decide_approval(
    conn: psycopg.AsyncConnection,
    *,
    run_id: UUID,
    step_key: str,
    approved: bool,
    decided_by: UUID | None,
) -> None:
    """Record the decision AND flip the run back to 'pending' in the caller's
    single transaction (§6.2 step 5 one-transaction rule — waiting_approval
    has no wake_at, so a crash between the two writes would strand the run).
    The runner consumes the decision on its next claim (D-P13B-5)."""
    cur = await conn.execute(
        "UPDATE workflow_approval SET state = %s, decided_by = %s, "
        "decided_at = now() "
        "WHERE run_id = %s AND step_key = %s AND state = 'pending'",
        ("approved" if approved else "rejected", decided_by, run_id, step_key),
    )
    if cur.rowcount != 1:
        raise ApprovalNotPendingError(f"{run_id}/{step_key}: approval not pending")
    cur = await conn.execute(
        "UPDATE workflow_run SET status = 'pending', updated_at = now() "
        "WHERE id = %s AND status = 'waiting_approval'",
        (run_id,),
    )
    if cur.rowcount != 1:
        raise ApprovalNotPendingError(f"{run_id}: run is not waiting_approval")
    await write_event(
        conn,
        actor_user_id=None,
        action="workflow.approval_decided",
        target=str(run_id),
        meta={
            "step": step_key,
            "approved": approved,
            "decided_by": str(decided_by) if decided_by else None,
        },
    )
