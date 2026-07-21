"""Manual run controls + the waiting_manual resolution state machine (spec §10).

Every transition is one status-conditional UPDATE — the arbiter — plus its
audit row, in the caller's single transaction (the §6.2 one-transaction
resume rule: a raise rolls back every write). rowcount = 0 means the run (or
ledger row) was not in the expected state → ManualTransitionError (409 in
the UI). The runner stays the only writer out of 'running'; these helpers
touch only unleased statuses (D-P13C-5).

P13c implemented the LEDGER-ROW resolutions (confirm-committed /
confirm-not-sent / abandon). P13d adds the unacked-agent-dispatch
resolutions (repost-same-correlation / supersede — spec §10 second bullet)
and the cancellation supersede (§5.6).
"""

from __future__ import annotations

from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from dl_control.audit.service import write_event
from dl_control.workflows.errors import ManualTransitionError

# Unleased, non-terminal statuses an admin may cancel. 'running' is excluded
# (the runner owns leased runs) and so is 'waiting_manual' — spec §10 makes
# the enumerated resolutions its ONLY legal exits (D-P13C-5; abandon is the
# fail path). waiting_agent is listed now so P13d's parked agent calls are
# cancellable without touching this arbiter.
CANCELLABLE = ("pending", "waiting_approval", "waiting_timer", "waiting_retry", "waiting_agent")
# Waiting states an admin may force-fail / force-complete out of (the W5
# "finished manually" path). 'pending' is excluded — cancel covers it;
# 'waiting_manual' is excluded — force-complete would mark a run succeeded
# while a 'started' ledger row stays unresolved.
FORCEABLE = ("waiting_approval", "waiting_timer", "waiting_retry", "waiting_agent")


async def cancel_run(
    conn: psycopg.AsyncConnection,
    *,
    run_id: UUID,
    actor_user_id: UUID | None,
) -> None:
    cur = await conn.execute(
        "UPDATE workflow_run SET status = 'cancelled', wake_at = NULL, "
        "finished_at = now(), updated_at = now() "
        "WHERE id = %s AND status = ANY(%s)",
        (run_id, list(CANCELLABLE)),
    )
    if cur.rowcount != 1:
        raise ManualTransitionError(f"run {run_id}: not cancellable")
    # §5.6: cancellation marks in-flight correlations superseded in the SAME
    # transaction, so a late callback simply loses at the arbiter.
    await conn.execute(
        "UPDATE agent_call SET status = 'superseded' "
        "WHERE run_id = %s AND status IN ('posted', 'dispatched')",
        (run_id,),
    )
    await write_event(
        conn,
        actor_user_id=actor_user_id,
        action="workflow.run_cancelled",
        target=str(run_id),
        meta={"reason": "manual"},
    )


async def fail_run(
    conn: psycopg.AsyncConnection,
    *,
    run_id: UUID,
    actor_user_id: UUID | None,
) -> None:
    cur = await conn.execute(
        "UPDATE workflow_run SET status = 'failed', wake_at = NULL, "
        "finished_at = now(), updated_at = now() "
        "WHERE id = %s AND status = ANY(%s)",
        (run_id, list(FORCEABLE)),
    )
    if cur.rowcount != 1:
        raise ManualTransitionError(f"run {run_id}: not in a waiting state")
    await write_event(
        conn,
        actor_user_id=actor_user_id,
        action="workflow.run_failed",
        target=str(run_id),
        meta={"reason": "manual"},
    )


async def force_complete_run(
    conn: psycopg.AsyncConnection,
    *,
    run_id: UUID,
    actor_user_id: UUID | None,
) -> None:
    """The W5 'rare mid-step crashes are finished manually' terminal path."""
    cur = await conn.execute(
        "UPDATE workflow_run SET status = 'succeeded', wake_at = NULL, "
        "finished_at = now(), updated_at = now() "
        "WHERE id = %s AND status = ANY(%s) RETURNING current_step",
        (run_id, list(FORCEABLE)),
    )
    row = await cur.fetchone()
    if row is None:
        raise ManualTransitionError(f"run {run_id}: not in a waiting state")
    if row[0]:
        await conn.execute(
            "UPDATE workflow_step SET status = 'skipped', finished_at = now(), "
            "error = COALESCE(error, 'manually completed') "
            "WHERE run_id = %s AND step_key = %s "
            "AND status IN ('pending', 'running')",
            (run_id, row[0]),
        )
    await write_event(
        conn,
        actor_user_id=actor_user_id,
        action="workflow.run_force_completed",
        target=str(run_id),
        meta={},
    )


async def retry_run(
    conn: psycopg.AsyncConnection,
    *,
    run_id: UUID,
    actor_user_id: UUID | None,
) -> None:
    """failed → pending with the current step reset for a fresh attempt cycle
    (D-P13C-6). Committed ledger effects stay committed — the re-execution
    skips them by contract."""
    cur = await conn.execute(
        "UPDATE workflow_run SET status = 'pending', wake_at = NULL, "
        "finished_at = NULL, updated_at = now() "
        "WHERE id = %s AND status = 'failed' RETURNING current_step",
        (run_id,),
    )
    row = await cur.fetchone()
    if row is None:
        raise ManualTransitionError(f"run {run_id}: only failed runs can be retried")
    if row[0]:
        await conn.execute(
            "UPDATE workflow_step SET status = 'pending', attempt = 0, "
            "error = NULL, finished_at = NULL "
            "WHERE run_id = %s AND step_key = %s",
            (run_id, row[0]),
        )
    await write_event(
        conn,
        actor_user_id=actor_user_id,
        action="workflow.run_retried",
        target=str(run_id),
        meta={},
    )


async def confirm_committed(
    conn: psycopg.AsyncConnection,
    *,
    run_id: UUID,
    idempotency_key: str,
    note: str,
    actor_user_id: UUID | None,
) -> None:
    """Operator confirmed the ambiguous effect DID land (spec §10): ledger row
    started → committed with the operator's evidence; run resumes. One
    transaction — a guard miss rolls back both writes (D-P13C-7)."""
    cur = await conn.execute(
        "UPDATE side_effect_ledger SET status = 'committed', response = %s, "
        "updated_at = now() "
        "WHERE idempotency_key = %s AND run_id = %s AND status = 'started'",
        (
            Jsonb(
                {
                    "manual_confirmation": note,
                    "confirmed_by": str(actor_user_id) if actor_user_id else None,
                }
            ),
            idempotency_key,
            run_id,
        ),
    )
    if cur.rowcount != 1:
        raise ManualTransitionError(
            f"{idempotency_key}: ledger row is not 'started' for run {run_id}"
        )
    await _resume_from_manual(conn, run_id=run_id)
    await write_event(
        conn,
        actor_user_id=actor_user_id,
        action="workflow.manual_resolved",
        target=str(run_id),
        meta={"action": "confirm_committed", "key": idempotency_key},
    )


async def confirm_not_sent(
    conn: psycopg.AsyncConnection,
    *,
    run_id: UUID,
    idempotency_key: str,
    actor_user_id: UUID | None,
) -> None:
    """Operator confirmed the effect did NOT land: ledger row started → failed
    (the step's next execution re-arms and retries it); the step gets a fresh
    attempt budget (D-P13C-13 — the runner bumps attempt pre-handler, so a
    stale counter would re-call the provider past Retry.max_attempts); run
    resumes. All in the caller's one transaction."""
    cur = await conn.execute(
        "UPDATE side_effect_ledger SET status = 'failed', updated_at = now() "
        "WHERE idempotency_key = %s AND run_id = %s AND status = 'started' "
        "RETURNING step_key",
        (idempotency_key, run_id),
    )
    row = await cur.fetchone()
    if row is None:
        raise ManualTransitionError(
            f"{idempotency_key}: ledger row is not 'started' for run {run_id}"
        )
    await conn.execute(
        "UPDATE workflow_step SET status = 'pending', attempt = 0, "
        "error = NULL, finished_at = NULL "
        "WHERE run_id = %s AND step_key = %s",
        (run_id, row[0]),
    )
    await _resume_from_manual(conn, run_id=run_id)
    await write_event(
        conn,
        actor_user_id=actor_user_id,
        action="workflow.manual_resolved",
        target=str(run_id),
        meta={"action": "confirm_not_sent", "key": idempotency_key},
    )


async def abandon_run(
    conn: psycopg.AsyncConnection,
    *,
    run_id: UUID,
    actor_user_id: UUID | None,
) -> None:
    """Fail the parked run; the ledger row stays 'started' as evidence."""
    cur = await conn.execute(
        "UPDATE workflow_run SET status = 'failed', wake_at = NULL, "
        "finished_at = now(), updated_at = now() "
        "WHERE id = %s AND status = 'waiting_manual'",
        (run_id,),
    )
    if cur.rowcount != 1:
        raise ManualTransitionError(f"run {run_id}: not waiting_manual")
    await conn.execute(
        "UPDATE agent_call SET status = 'superseded' "
        "WHERE run_id = %s AND status IN ('posted', 'dispatched')",
        (run_id,),
    )
    await write_event(
        conn,
        actor_user_id=actor_user_id,
        action="workflow.manual_resolved",
        target=str(run_id),
        meta={"action": "abandon"},
    )


async def repost_same_correlation(
    conn: psycopg.AsyncConnection,
    *,
    run_id: UUID,
    actor_user_id: UUID | None,
) -> None:
    """Operator default for an unacked dispatch (spec §10): fresh repost
    budget on the still-'posted' latest correlation + resume; the runner
    re-posts under the SAME correlation_id — safe because the receiver
    dedups (D-P13D-11). One transaction; a guard miss rolls back both."""
    cur = await conn.execute(
        "UPDATE agent_call SET dispatch_count = 0 "
        "WHERE correlation_id = ("
        "  SELECT correlation_id FROM agent_call WHERE run_id = %s "
        "  ORDER BY attempt DESC LIMIT 1) "
        "AND status = 'posted' RETURNING correlation_id",
        (run_id,),
    )
    row = await cur.fetchone()
    if row is None:
        raise ManualTransitionError(f"run {run_id}: latest agent call is not an unacked dispatch")
    await _resume_from_manual(conn, run_id=run_id)
    await write_event(
        conn,
        actor_user_id=actor_user_id,
        action="workflow.manual_resolved",
        target=str(run_id),
        meta={"action": "repost_same_correlation", "correlation_id": str(row[0])},
    )


async def supersede_dispatch(
    conn: psycopg.AsyncConnection,
    *,
    run_id: UUID,
    actor_user_id: UUID | None,
) -> None:
    """Explicit audited operator override (spec §10): mark in-flight
    correlations superseded + resume; the runner mints a FRESH correlation.
    Use only when the operator knows the first dispatch never landed."""
    cur = await conn.execute(
        "UPDATE agent_call SET status = 'superseded' "
        "WHERE run_id = %s AND status IN ('posted', 'dispatched')",
        (run_id,),
    )
    if cur.rowcount < 1:
        raise ManualTransitionError(f"run {run_id}: no in-flight agent dispatch to supersede")
    await _resume_from_manual(conn, run_id=run_id)
    await write_event(
        conn,
        actor_user_id=actor_user_id,
        action="workflow.manual_resolved",
        target=str(run_id),
        meta={"action": "supersede"},
    )


async def _resume_from_manual(
    conn: psycopg.AsyncConnection,
    *,
    run_id: UUID,
) -> None:
    cur = await conn.execute(
        "UPDATE workflow_run SET status = 'pending', updated_at = now() "
        "WHERE id = %s AND status = 'waiting_manual'",
        (run_id,),
    )
    if cur.rowcount != 1:
        # Aborts the caller's transaction — the ledger write rolls back too.
        raise ManualTransitionError(f"run {run_id}: not waiting_manual")
