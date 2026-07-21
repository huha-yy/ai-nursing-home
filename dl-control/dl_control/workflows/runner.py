"""Workflow runner — single-writer leased loop (spec §6.2, W4/W5).

One in-process background task, peer to the existing reconcilers. Each tick
claims at most one due run (Postgres-authoritative atomic claim) and drives
it step by step — persisting after every step — until it parks in a waiting
state, finishes, loses its lease, or shutdown drains it. External mutations
inside handlers go through ctx.ledgered (ledger.py); this module is the ONLY
writer of workflow_run status transitions out of 'running'.

Wake latency: the loop polls every poll_seconds (default 1 s). The spec's
Redis pub/sub nudge is a latency optimization whose wake sources (triggers,
UI callbacks — P13c) and agent-call timeouts / callbacks (P13d) are now wired;
polling is the spec's stated correctness baseline (§6.2 — "losing Redis
degrades latency … never correctness").
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from dl_control.audit.service import write_event
from dl_control.db import Database
from dl_control.workflows import agent_calls, runs
from dl_control.workflows.dispatch import DispatchConfig, dispatch_task
from dl_control.workflows.errors import (
    LeaseLostError,
    LedgerConflictError,
    UnresolvedEffectError,
)
from dl_control.workflows.ledger import request_hash
from dl_control.workflows.loader import load_flow
from dl_control.workflows.model import (
    DONE,
    AgentTask,
    Flow,
    Retry,
    Step,
    StepContext,
    StepResult,
)

logger = logging.getLogger(__name__)

Resolver = Callable[[str], Flow]
Dispatcher = Callable[..., Any]  # dispatch.dispatch_task's signature; tests inject fakes
_RECHECK = object()  # arbiter lost — re-read the correlation row and re-branch


async def runner_loop(
    db: Database,
    shutdown_event: asyncio.Event,
    *,
    worker: str,
    lease_ttl_seconds: int,
    poll_seconds: float,
    resolver: Resolver = load_flow,
    wake_event: asyncio.Event | None = None,
    dispatch_cfg: DispatchConfig | None = None,
    dispatcher: Dispatcher = dispatch_task,
) -> None:
    """The boot-launched loop: claim-and-drive until shutdown. Never raises."""
    logger.info("workflow runner started (worker=%s)", worker)
    while not shutdown_event.is_set():
        try:
            progressed = await run_once(
                db,
                worker=worker,
                lease_ttl_seconds=lease_ttl_seconds,
                resolver=resolver,
                shutdown_event=shutdown_event,
                dispatch_cfg=dispatch_cfg,
                dispatcher=dispatcher,
            )
        except Exception:
            logger.exception("workflow runner tick failed")
            progressed = False
        if not progressed:
            await _idle_wait(shutdown_event, wake_event, poll_seconds=poll_seconds)
    logger.info("workflow runner stopped (worker=%s)", worker)


async def _idle_wait(
    shutdown_event: asyncio.Event,
    wake_event: asyncio.Event | None,
    *,
    poll_seconds: float,
) -> None:
    """Sleep until shutdown, a wake nudge, or the poll timeout — whichever
    first. The wake event is cleared BEFORE waiting, so a nudge that landed
    while the loop was busy driving costs at most one extra poll interval
    (D-P13C-9); polling remains the correctness baseline."""
    waiters = [asyncio.create_task(shutdown_event.wait())]
    if wake_event is not None:
        wake_event.clear()
        waiters.append(asyncio.create_task(wake_event.wait()))
    try:
        await asyncio.wait(waiters, timeout=poll_seconds, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in waiters:
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t


async def run_once(
    db: Database,
    *,
    worker: str,
    lease_ttl_seconds: int,
    resolver: Resolver = load_flow,
    shutdown_event: asyncio.Event | None = None,
    dispatch_cfg: DispatchConfig | None = None,
    dispatcher: Dispatcher = dispatch_task,
) -> bool:
    """Claim at most one due run and drive it to its next wait / terminal
    state. Returns True iff a run was claimed (so the loop can re-poll
    immediately while there is work)."""
    async with db.conn(user_id=None, role="system") as conn:
        claimed = await runs.claim_next_run(
            conn,
            worker=worker,
            ttl_seconds=lease_ttl_seconds,
        )
    if claimed is None:
        return False
    try:
        await _drive(
            db,
            claimed,
            worker=worker,
            ttl=lease_ttl_seconds,
            resolver=resolver,
            shutdown_event=shutdown_event or asyncio.Event(),
            dispatch_cfg=dispatch_cfg,
            dispatcher=dispatcher,
        )
    except LeaseLostError:
        # A competing claimer (expired-lease reclaim) owns the run now.
        # Abandon without further writes — it will finish the work (§13).
        logger.warning("run %s: lease lost; abandoning", claimed.id)
    except Exception:
        # Leave the run leased-'running'; the expired-lease reclaim (§6.2)
        # picks it up after the TTL. The loop itself must never die.
        logger.exception("run %s: driver crashed", claimed.id)
    return True


async def _drive(
    db: Database,
    run: runs.ClaimedRun,
    *,
    worker: str,
    ttl: int,
    resolver: Resolver,
    shutdown_event: asyncio.Event,
    dispatch_cfg: DispatchConfig | None = None,
    dispatcher: Dispatcher = dispatch_task,
) -> None:
    """Resolve the PINNED flow version (§9) and execute steps until the run
    parks, finishes, or shutdown drains it back to 'pending'."""
    async with db.conn(user_id=None, role="system") as conn:
        cur = await conn.execute(
            "SELECT code_ref FROM workflow_version WHERE workflow_id = %s AND version = %s",
            (run.workflow_id, run.workflow_version),
        )
        row = await cur.fetchone()
    if row is None:
        await _fail(
            db,
            run,
            worker,
            step_key=None,
            error=f"pinned version {run.workflow_version} not registered",
        )
        return
    try:
        flow = resolver(row[0])
    except Exception as exc:  # noqa: BLE001 — any load failure fails the run
        await _fail(db, run, worker, step_key=None, error=f"flow load failed: {exc}")
        return
    if flow.id != run.workflow_id or flow.version != run.workflow_version:
        await _fail(
            db,
            run,
            worker,
            step_key=None,
            error=(
                f"loaded flow {flow.id}@{flow.version} != pinned "
                f"{run.workflow_id}@{run.workflow_version}"
            ),
        )
        return

    step_key: str | None = run.current_step or flow.first_key
    while step_key is not None:
        if shutdown_event.is_set():
            async with db.conn(user_id=None, role="system") as conn:
                await runs.release_run(conn, run_id=run.id, worker=worker)
            logger.info("run %s: released for shutdown drain", run.id)
            return
        step_key = await _execute_step(
            db,
            run,
            flow,
            step_key,
            worker=worker,
            ttl=ttl,
            dispatch_cfg=dispatch_cfg,
            dispatcher=dispatcher,
        )


async def _execute_step(
    db: Database,
    run: runs.ClaimedRun,
    flow: Flow,
    step_key: str,
    *,
    worker: str,
    ttl: int,
    dispatch_cfg: DispatchConfig | None = None,
    dispatcher: Dispatcher = dispatch_task,
) -> str | None:
    """Execute one step. Returns the next step key to run under this claim,
    or None when the run parked, finished, or failed."""
    step = flow.by_key.get(step_key)
    if step is None:
        await _fail(
            db,
            run,
            worker,
            step_key=None,
            error=f"unknown step {step_key!r} in {flow.id}@{flow.version}",
        )
        return None

    now = datetime.now(UTC)
    async with db.conn(user_id=None, role="system") as conn:
        # Guarded step entry: lease renewal + current_step persist + step-row
        # upsert in one transaction; raises LeaseLostError if usurped.
        prior = await runs.begin_step(
            conn,
            run_id=run.id,
            worker=worker,
            step_key=step_key,
            ttl_seconds=ttl,
        )

    if step.timer is not None:
        return await _drive_timer(db, run, flow, step, prior, worker=worker, now=now)

    if step.approval is not None and not await _approval_clears(
        db,
        run,
        step,
        worker=worker,
    ):
        return None

    if step.call_agent is not None:
        return await _drive_call_agent(
            db,
            run,
            flow,
            step,
            worker=worker,
            dcfg=dispatch_cfg,
            dispatcher=dispatcher,
        )

    return await _run_handler(db, run, flow, step, worker=worker, ttl=ttl)


async def _drive_timer(
    db: Database,
    run: runs.ClaimedRun,
    flow: Flow,
    step: Step,
    prior: tuple[str, int, datetime | None],
    *,
    worker: str,
    now: datetime,
) -> str | None:
    _status, _attempt, started_at = prior
    duration = timedelta(seconds=step.timer.total_seconds)
    async with db.conn(user_id=None, role="system") as conn:
        if started_at is None:
            # First arrival — arm. started_at (the durable anchor) and the
            # park (wake_at) land in this one transaction: no
            # armed-without-deadline crash window (D-P13B-4).
            await runs.mark_step_running(
                conn,
                run_id=run.id,
                worker=worker,
                step_key=step.key,
                bump_attempt=False,
            )
            await runs.park_run(
                conn,
                run_id=run.id,
                worker=worker,
                status="waiting_timer",
                wake_at=now + duration,
            )
            return None
        deadline = started_at + duration
        if now < deadline:
            # Claimed before the deadline — only possible via stale-lease
            # reclaim of a run that crashed between claiming and parking.
            # Re-park to the recomputed authoritative deadline (D-P13B-4).
            await runs.park_run(
                conn,
                run_id=run.id,
                worker=worker,
                status="waiting_timer",
                wake_at=deadline,
            )
            return None
        nxt = flow.next_key(step.key)
        if nxt is None:
            await runs.finish_run(
                conn,
                run_id=run.id,
                worker=worker,
                status="succeeded",
                step_key=step.key,
                step_status="succeeded",
            )
            await write_event(
                conn,
                actor_user_id=None,
                action="workflow.run_succeeded",
                target=str(run.id),
                meta={"workflow": run.workflow_id},
            )
            return None
        await runs.advance(
            conn,
            run_id=run.id,
            worker=worker,
            step_key=step.key,
            output=None,
            next_step=nxt,
        )
        return nxt


async def _approval_clears(
    db: Database,
    run: runs.ClaimedRun,
    step: Step,
    *,
    worker: str,
) -> bool:
    """Pre-step approval gate (§5.5). True = approved, proceed to the handler;
    False = parked waiting_approval or terminally cancelled."""
    async with db.conn(user_id=None, role="system") as conn:
        state = await runs.ensure_approval(
            conn,
            run_id=run.id,
            step_key=step.key,
            prompt=step.approval.prompt,
        )
        if state == "approved":
            return True
        if state == "rejected":
            # A deliberate admin outcome, not an error → cancelled (D-P13B-5).
            await runs.finish_run(
                conn,
                run_id=run.id,
                worker=worker,
                status="cancelled",
                step_key=step.key,
                step_status="skipped",
                error="approval rejected",
            )
            await write_event(
                conn,
                actor_user_id=None,
                action="workflow.run_cancelled",
                target=str(run.id),
                meta={"workflow": run.workflow_id, "step": step.key, "reason": "approval_rejected"},
            )
            return False
        # pending — park with NO wake_at; the resume is decide_approval's
        # one-transaction flip back to 'pending' (§6.2 step 5).
        await runs.park_run(
            conn,
            run_id=run.id,
            worker=worker,
            status="waiting_approval",
            wake_at=None,
        )
        await write_event(
            conn,
            actor_user_id=None,
            action="workflow.approval_requested",
            target=str(run.id),
            meta={"workflow": run.workflow_id, "step": step.key},
        )
        return False


async def _run_handler(
    db: Database,
    run: runs.ClaimedRun,
    flow: Flow,
    step: Step,
    *,
    worker: str,
    ttl: int,
) -> str | None:
    async with db.conn(user_id=None, role="system") as conn:
        attempt = await runs.mark_step_running(
            conn,
            run_id=run.id,
            worker=worker,
            step_key=step.key,
            bump_attempt=True,
        )
        outputs = await runs.load_outputs(conn, run_id=run.id)
    ctx = StepContext(
        run_id=run.id,
        workflow_id=run.workflow_id,
        step_key=step.key,
        attempt=attempt,
        input=run.input,
        outputs=outputs,
        db=db,
    )
    handler_task = asyncio.create_task(step.handler(ctx))
    heartbeat = asyncio.create_task(_lease_heartbeat(db, run_id=run.id, worker=worker, ttl=ttl))
    try:
        # Race the handler against the heartbeat: the heartbeat only completes
        # early when lease renewal failed, and a stale worker must not keep
        # executing the step concurrently with the new owner (Codex task-5
        # P1). Cancellation mid-ledgered_call leaves the ledger row 'started',
        # so the reclaiming owner parks in waiting_manual — the W5
        # manual-confirm path, never a silent double-execution.
        done, _pending = await asyncio.wait(
            {handler_task, heartbeat},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if handler_task not in done:
            handler_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await handler_task
            raise LeaseLostError(f"run {run.id}: lease lost mid-handler at {step.key}")
        result = await handler_task
    except LeaseLostError:
        raise  # abandon — never classified as a step failure
    except UnresolvedEffectError as exc:
        await _park_manual(db, run, step.key, worker=worker, error=str(exc))
        return None
    except LedgerConflictError as exc:
        # A changed request under a stable idempotency key is a flow-code bug,
        # not an operational ambiguity (D-P13B-3).
        await _fail(db, run, worker, step_key=step.key, error=str(exc))
        return None
    except Exception as exc:  # noqa: BLE001 — known-clean by construction (D-P13B-2)
        return await _retry_or_fail(db, run, step, attempt, exc, worker=worker)
    finally:
        heartbeat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat

    output, goto = _normalize(result)
    if goto == DONE:
        nxt = None
    elif goto is not None:
        if goto not in flow.by_key:
            await _fail(db, run, worker, step_key=step.key, error=f"unknown goto {goto!r}")
            return None
        nxt = goto
    else:
        nxt = flow.next_key(step.key)

    async with db.conn(user_id=None, role="system") as conn:
        if nxt is None:
            await runs.finish_run(
                conn,
                run_id=run.id,
                worker=worker,
                status="succeeded",
                step_key=step.key,
                step_status="succeeded",
                output=output,
            )
            await write_event(
                conn,
                actor_user_id=None,
                action="workflow.run_succeeded",
                target=str(run.id),
                meta={"workflow": run.workflow_id},
            )
        else:
            await runs.advance(
                conn,
                run_id=run.id,
                worker=worker,
                step_key=step.key,
                output=output,
                next_step=nxt,
            )
    return nxt


async def _retry_or_fail(
    db: Database,
    run: runs.ClaimedRun,
    step: Step,
    attempt: int,
    exc: Exception,
    *,
    worker: str,
) -> None:
    retry = step.retry or Retry(max_attempts=1)
    if attempt >= retry.max_attempts:
        await _fail(
            db,
            run,
            worker,
            step_key=step.key,
            error=f"attempt {attempt}/{retry.max_attempts}: {exc}",
        )
        return None
    delay = retry.delay_after(attempt)
    wake = datetime.now(UTC) + timedelta(seconds=delay)
    async with db.conn(user_id=None, role="system") as conn:
        await runs.park_retry(
            conn,
            run_id=run.id,
            worker=worker,
            step_key=step.key,
            error=str(exc),
            next_attempt_at=wake,
        )
    logger.warning(
        "run %s step %s failed (attempt %d/%d), retrying at %s: %s",
        run.id,
        step.key,
        attempt,
        retry.max_attempts,
        wake.isoformat(),
        exc,
    )
    return None


async def _fail(
    db: Database,
    run: runs.ClaimedRun,
    worker: str,
    *,
    step_key: str | None,
    error: str,
) -> None:
    async with db.conn(user_id=None, role="system") as conn:
        await runs.finish_run(
            conn,
            run_id=run.id,
            worker=worker,
            status="failed",
            step_key=step_key,
            step_status="failed" if step_key else None,
            error=error,
        )
        await write_event(
            conn,
            actor_user_id=None,
            action="workflow.run_failed",
            target=str(run.id),
            meta={"workflow": run.workflow_id, "step": step_key, "error": error[:500]},
        )
    logger.error("run %s failed at %s: %s", run.id, step_key, error)


async def _park_manual(
    db: Database,
    run: runs.ClaimedRun,
    step_key: str,
    *,
    worker: str,
    error: str,
) -> None:
    async with db.conn(user_id=None, role="system") as conn:
        await runs.park_run(
            conn,
            run_id=run.id,
            worker=worker,
            status="waiting_manual",
            wake_at=None,
            step_key=step_key,
            step_error=error,
        )
        await write_event(
            conn,
            actor_user_id=None,
            action="workflow.waiting_manual",
            target=str(run.id),
            meta={"workflow": run.workflow_id, "step": step_key, "error": error[:500]},
        )
    logger.warning("run %s parked waiting_manual at %s: %s", run.id, step_key, error)


async def _drive_call_agent(
    db: Database,
    run: runs.ClaimedRun,
    flow: Flow,
    step: Step,
    *,
    worker: str,
    dcfg: DispatchConfig | None,
    dispatcher: Dispatcher,
) -> str | None:
    """The call_agent state machine (spec §7.1, D-P13D-6). Branches on the
    latest agent_call row; loops only when an arbiter was lost (_RECHECK —
    a result raced in and must be consumed)."""
    if dcfg is None:
        await _fail(db, run, worker, step_key=step.key, error="agent dispatch is not configured")
        return None
    retry = step.retry or Retry(max_attempts=1)
    while True:
        async with db.conn(user_id=None, role="system") as conn:
            row = await agent_calls.get_latest_call(conn, run_id=run.id, step_key=step.key)
        if row is None or row.status in ("timed_out", "superseded"):
            result = await _mint_and_dispatch(
                db, run, step, worker=worker, dcfg=dcfg, dispatcher=dispatcher
            )
        elif row.status == "responded":
            result = await _consume_response(db, run, flow, step, row, retry, worker=worker)
        elif row.status == "posted":
            if row.dispatch_count >= dcfg.repost_max:
                await _park_manual(
                    db,
                    run,
                    step.key,
                    worker=worker,
                    error=(
                        f"agent dispatch unacked after {row.dispatch_count} "
                        f"posts (correlation {row.correlation_id}) — "
                        "repost or supersede"
                    ),
                )
                return None
            result = await _repost(
                db, run, step, row, worker=worker, dcfg=dcfg, dispatcher=dispatcher
            )
        else:  # dispatched — the response deadline drives this branch
            result = await _timeout_or_repark(db, run, step, row, retry, worker=worker)
        if result is not _RECHECK:
            return result


async def _mint_and_dispatch(
    db: Database,
    run: runs.ClaimedRun,
    step: Step,
    *,
    worker: str,
    dcfg: DispatchConfig,
    dispatcher: Dispatcher,
):
    """First arrival / fresh attempt: bump the step attempt (the D-P13D-8
    budget counter), build + validate the task, persist 'posted' BEFORE
    posting (§5.6), then dispatch."""
    async with db.conn(user_id=None, role="system") as conn:
        await runs.mark_step_running(
            conn, run_id=run.id, worker=worker, step_key=step.key, bump_attempt=True
        )
        outputs = await runs.load_outputs(conn, run_id=run.id)
    task = await _build_task(db, run, step, outputs, worker=worker)
    if task is None:
        return None
    async with db.conn(user_id=None, role="system") as conn:
        cur = await conn.execute("SELECT status FROM agents WHERE id = %s", (task.agent_id,))
        arow = await cur.fetchone()
    if arow is None or arow[0] != "active":
        # Also covers the §5.6 agent-hard-delete cascade (rows gone, fresh
        # mint would hit a missing FK target) — park, never crash the loop.
        await _park_manual(
            db,
            run,
            step.key,
            worker=worker,
            error=f"target agent {task.agent_id} is missing or not active",
        )
        return None
    rhash = request_hash({"agent_id": str(task.agent_id), "message": task.message})
    async with db.conn(user_id=None, role="system") as conn:
        await agent_calls.supersede_in_flight(conn, run_id=run.id, step_key=step.key)
        row = await agent_calls.insert_call(
            conn, run_id=run.id, step_key=step.key, agent_id=task.agent_id, request_hash=rhash
        )
        await write_event(
            conn,
            actor_user_id=None,
            action="workflow.agent_call_posted",
            target=str(run.id),
            meta={
                "workflow": run.workflow_id,
                "step": step.key,
                "agent_id": str(task.agent_id),
                "correlation_id": str(row.correlation_id),
                "attempt": row.attempt,
            },
        )
    return await _post_and_park(
        db, run, step, row, task, worker=worker, dcfg=dcfg, dispatcher=dispatcher
    )


async def _build_task(
    db: Database,
    run: runs.ClaimedRun,
    step: Step,
    outputs: dict[str, Any],
    *,
    worker: str,
) -> AgentTask | None:
    """prepare() with flow-bug containment: a raise fails the run (no retry —
    a broken prepare is deterministic, like an unknown goto)."""
    try:
        task = step.call_agent.prepare(run.input, outputs)
    except Exception as exc:  # noqa: BLE001 — any prepare failure is a flow bug
        await _fail(db, run, worker, step_key=step.key, error=f"call_agent prepare failed: {exc}")
        return None
    if not isinstance(task, AgentTask):
        await _fail(
            db,
            run,
            worker,
            step_key=step.key,
            error="call_agent prepare did not return an AgentTask",
        )
        return None
    return task


async def _repost(
    db: Database,
    run: runs.ClaimedRun,
    step: Step,
    row: agent_calls.AgentCallRow,
    *,
    worker: str,
    dcfg: DispatchConfig,
    dispatcher: Dispatcher,
):
    """Re-post the SAME correlation (§7.1 step 4 'posted' — safe iff the
    receiver dedups). prepare() is re-evaluated and must hash-match the
    minted correlation (D-P13D-5)."""
    async with db.conn(user_id=None, role="system") as conn:
        outputs = await runs.load_outputs(conn, run_id=run.id)
    task = await _build_task(db, run, step, outputs, worker=worker)
    if task is None:
        return None
    rhash = request_hash({"agent_id": str(task.agent_id), "message": task.message})
    if rhash != row.request_hash:
        await _fail(
            db,
            run,
            worker,
            step_key=step.key,
            error=(
                f"call_agent request changed under correlation "
                f"{row.correlation_id} (request_hash mismatch)"
            ),
        )
        return None
    return await _post_and_park(
        db, run, step, row, task, worker=worker, dcfg=dcfg, dispatcher=dispatcher
    )


async def _post_and_park(
    db: Database,
    run: runs.ClaimedRun,
    step: Step,
    row: agent_calls.AgentCallRow,
    task: AgentTask,
    *,
    worker: str,
    dcfg: DispatchConfig,
    dispatcher: Dispatcher,
):
    """The shared dispatch tail: durable post count → HTTP post → park.
    wake_at is the response deadline on ack, the repost backoff otherwise."""
    async with db.conn(user_id=None, role="system") as conn:
        counted = await agent_calls.increment_dispatch_count(
            conn, correlation_id=row.correlation_id
        )
    if not counted:
        return _RECHECK  # a response raced in — consume it instead of posting
    acked = await dispatcher(
        dcfg,
        agent_id=task.agent_id,
        correlation_id=row.correlation_id,
        run_id=run.id,
        step_key=step.key,
        message=task.message,
    )
    now = datetime.now(UTC)
    if acked:
        wake = now + timedelta(seconds=step.call_agent.timeout_seconds)
    else:
        wake = now + timedelta(seconds=dcfg.repost_backoff_seconds)
    async with db.conn(user_id=None, role="system") as conn:
        if acked:
            # Loser is fine: an ultra-fast callback already set 'responded'.
            await agent_calls.mark_dispatched(conn, correlation_id=row.correlation_id)
        await runs.park_run(
            conn, run_id=run.id, worker=worker, status="waiting_agent", wake_at=wake
        )
        # D-P13D-10: the callback's conditional resume loses while we hold
        # the lease — if the response already landed, make the run due now
        # instead of sleeping out the full timeout.
        cur = await conn.execute(
            "SELECT status FROM agent_call WHERE correlation_id = %s", (row.correlation_id,)
        )
        srow = await cur.fetchone()
        if srow is not None and srow[0] == "responded":
            await conn.execute(
                "UPDATE workflow_run SET wake_at = now() "
                "WHERE id = %s AND status = 'waiting_agent'",
                (run.id,),
            )
    return None


async def _timeout_or_repark(
    db: Database,
    run: runs.ClaimedRun,
    step: Step,
    row: agent_calls.AgentCallRow,
    retry: Retry,
    *,
    worker: str,
):
    """Dispatched: either an early (drain-release / crash-recovery) claim —
    re-park to the surviving deadline — or a genuine response timeout."""
    now = datetime.now(UTC)
    async with db.conn(user_id=None, role="system") as conn:
        cur = await conn.execute("SELECT wake_at FROM workflow_run WHERE id = %s", (run.id,))
        wake_at = (await cur.fetchone())[0]
    if wake_at is None or now < wake_at:
        # Claimed before the deadline (drain-release resume, or a crash
        # between mark_dispatched and park left wake_at stale/NULL) —
        # re-park, not a timeout (mirrors the D-P13B-4 timer recompute).
        deadline = wake_at or (now + timedelta(seconds=step.call_agent.timeout_seconds))
        async with db.conn(user_id=None, role="system") as conn:
            await runs.park_run(
                conn, run_id=run.id, worker=worker, status="waiting_agent", wake_at=deadline
            )
        return None
    async with db.conn(user_id=None, role="system") as conn:
        timed_out = await agent_calls.claim_timeout(conn, correlation_id=row.correlation_id)
        if timed_out:
            await write_event(
                conn,
                actor_user_id=None,
                action="workflow.agent_call_timed_out",
                target=str(run.id),
                meta={
                    "workflow": run.workflow_id,
                    "step": step.key,
                    "correlation_id": str(row.correlation_id),
                },
            )
    if not timed_out:
        return _RECHECK  # the callback won — a result exists; consume it (§5.6)
    return await _count_call_failure(
        db,
        run,
        step,
        retry,
        worker=worker,
        error=f"agent call timed out (correlation {row.correlation_id})",
    )


def _try_extract_agent_json(text: str) -> dict | None:
    """Best-effort extraction of a JSON dict from agent CLI output text.

    Tries, in order:
    1. Direct json.loads on the full text.
    2. Backward line scan for braces-delimited lines.
    3. Unescape double-escaped strings (the receiver's ``{"text": "..."}``
       fallback encodes agent output that may contain \\\\n / \\\\" artifacts).
    Returns None when nothing parseable is found.
    """
    import json

    # 1. Direct parse
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, TypeError):
        pass

    # 2. Backward line scan
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                data = json.loads(line)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                continue

    # 3. Unescape double-escaped strings
    if "\\\\n" in text or '\\\\"' in text:
        try:
            unescaped = text.encode("utf-8").decode("unicode_escape")
            for line in reversed(unescaped.splitlines()):
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    data = json.loads(line)
                    if isinstance(data, dict):
                        return data
        except Exception:
            pass

    return None


def _unwrap_openclaw_container(output: dict) -> dict | None:
    """Unwrap OpenClaw agent container format into the LLM's JSON payload.

    The ``openclaw agent --json`` mode returns a container dict:
      {"runId": "...", "result": {"meta": ..., "payloads": [{"text": "..."}]},
       "status": "ok", "summary": "..."}

    The actual LLM structured response is the **last line** of the first
    payload's ``text`` field.  Return that parsed dict, or None if this
    doesn't look like an OpenClaw container.
    """
    if not isinstance(output, dict):
        return None
    if not all(k in output for k in ("runId", "result", "status")):
        return None
    payloads = output.get("result", {}).get("payloads")
    if not isinstance(payloads, list) or not payloads:
        return None
    text = payloads[0].get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    # Search the last JSON dict in the text
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            import json

            try:
                data = json.loads(line)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                continue
    return None


async def _consume_response(
    db: Database,
    run: runs.ClaimedRun,
    flow: Flow,
    step: Step,
    row: agent_calls.AgentCallRow,
    retry: Retry,
    *,
    worker: str,
) -> str | None:
    """Consume a stored response exactly once: ok → the step output;
    error → superseded + the step retry policy (D-P13D-14)."""
    payload = row.response or {}
    if payload.get("status") == "ok":
        output = payload.get("result")
        # Try to unwrap {"text": "..."} fallback — when the agent CLI does not
        # emit clean JSON the receiver wraps the raw output.  Check whether the
        # wrapped text itself contains a JSON dict and prefer that.
        if isinstance(output, dict) and list(output.keys()) == ["text"]:
            text = output["text"]
            parsed = _try_extract_agent_json(text)
            if parsed is not None:
                output = parsed
        # Unwrap OpenClaw agent container format — the receiver now extracts the
        # full agent JSON (runId, result.payloads[0].text, ...).  The LLM's
        # structured output is the last JSON line in the first payload's text.
        if isinstance(output, dict) and "runId" in output:
            parsed = _unwrap_openclaw_container(output)
            if parsed is not None:
                output = parsed
        nxt = flow.next_key(step.key)
        async with db.conn(user_id=None, role="system") as conn:
            if nxt is None:
                await runs.finish_run(
                    conn,
                    run_id=run.id,
                    worker=worker,
                    status="succeeded",
                    step_key=step.key,
                    step_status="succeeded",
                    output=output,
                )
                await write_event(
                    conn,
                    actor_user_id=None,
                    action="workflow.run_succeeded",
                    target=str(run.id),
                    meta={"workflow": run.workflow_id},
                )
            else:
                await runs.advance(
                    conn,
                    run_id=run.id,
                    worker=worker,
                    step_key=step.key,
                    output=output,
                    next_step=nxt,
                )
        return nxt
    error = str(payload.get("error") or "agent returned an error")
    async with db.conn(user_id=None, role="system") as conn:
        await agent_calls.mark_consumed_error(conn, correlation_id=row.correlation_id)
    return await _count_call_failure(
        db,
        run,
        step,
        retry,
        worker=worker,
        error=f"agent error (correlation {row.correlation_id}): {error}",
    )


async def _count_call_failure(
    db: Database,
    run: runs.ClaimedRun,
    step: Step,
    retry: Retry,
    *,
    worker: str,
    error: str,
) -> None:
    """Attempt-budget bookkeeping for timeouts and agent errors (D-P13D-8):
    workflow_step.attempt (bumped once per mint, reset by D-P13C-6 manual
    retry) against the step's Retry policy."""
    async with db.conn(user_id=None, role="system") as conn:
        cur = await conn.execute(
            "SELECT attempt FROM workflow_step WHERE run_id = %s AND step_key = %s",
            (run.id, step.key),
        )
        attempt = (await cur.fetchone())[0]
    if attempt >= retry.max_attempts:
        await _fail(
            db,
            run,
            worker,
            step_key=step.key,
            error=f"attempt {attempt}/{retry.max_attempts}: {error}",
        )
        return None
    wake = datetime.now(UTC) + timedelta(seconds=retry.delay_after(attempt))
    async with db.conn(user_id=None, role="system") as conn:
        await runs.park_retry(
            conn, run_id=run.id, worker=worker, step_key=step.key, error=error, next_attempt_at=wake
        )
    logger.warning(
        "run %s step %s agent call failed (attempt %d/%d), retrying at %s: %s",
        run.id,
        step.key,
        attempt,
        retry.max_attempts,
        wake.isoformat(),
        error,
    )
    return None


async def _lease_heartbeat(
    db: Database,
    *,
    run_id,
    worker: str,
    ttl: int,
) -> None:
    """Extend the lease while a long handler runs (§6.2 lease renewal)."""
    try:
        while True:
            await asyncio.sleep(max(ttl / 3, 1.0))
            async with db.conn(user_id=None, role="system") as conn:
                if not await runs.renew_lease(
                    conn,
                    run_id=run_id,
                    worker=worker,
                    ttl_seconds=ttl,
                ):
                    logger.warning("run %s: heartbeat lost the lease", run_id)
                    return
    except asyncio.CancelledError:
        pass


def _normalize(result: Any) -> tuple[Any, str | None]:
    """Handler return → (output, goto). None / plain value / StepResult."""
    if result is None:
        return None, None
    if isinstance(result, StepResult):
        return result.output, result.goto
    return result, None
