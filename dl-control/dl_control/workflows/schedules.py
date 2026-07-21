"""workflow_schedule DAL + the scheduler tick (spec §5.8, §8).

Cron times are Asia/Shanghai (D-P13C-1). Firing is advance-then-enqueue across two
transactions (D-P13C-2): the schedule advances first, so a crash between the
two yields one missed fire (self-healing at the next fire), never a double
run. A schedule whose cron no longer parses disables itself (D-P13C-4).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg
from cronsim import CronSim, CronSimError
from psycopg.types.json import Jsonb

from dl_control.audit.service import write_event
from dl_control.db import Database
from dl_control.workflows import runs
from dl_control.workflows.errors import (
    DuplicateActiveRunError,
    UnknownScheduleError,
    UnknownWorkflowError,
    WorkflowDisabledError,
)

logger = logging.getLogger(__name__)

_TZ = ZoneInfo("Asia/Shanghai")


def next_fire(cron: str, after: datetime) -> datetime:
    """Next Asia/Shanghai fire strictly after `after`. Raises ValueError on a bad
    expression (CronSim errors are normalized so callers need one except)."""
    if after.tzinfo is None:
        after = after.replace(tzinfo=_TZ)
    try:
        return next(CronSim(cron, after))
    except StopIteration as exc:  # expression that never fires again
        raise ValueError(f"cron {cron!r} has no future fire time") from exc
    except CronSimError as exc:
        raise ValueError(f"bad cron expression {cron!r}: {exc}") from exc


async def create_schedule(
    conn: psycopg.AsyncConnection,
    *,
    workflow_id: str,
    cron: str,
    input_template: dict[str, Any],
    actor_user_id: UUID | None,
) -> UUID:
    """Validates the cron (ValueError) and seeds next_fire_at. The workflow FK
    rejects an unknown workflow_id (UnknownWorkflowError)."""
    fire = next_fire(cron, datetime.now(_TZ))
    try:
        cur = await conn.execute(
            "INSERT INTO workflow_schedule (workflow_id, cron, input_template, "
            "next_fire_at) VALUES (%s, %s, %s, %s) RETURNING id",
            (workflow_id, cron, Jsonb(input_template), fire),
        )
    except psycopg.errors.ForeignKeyViolation as exc:
        raise UnknownWorkflowError(workflow_id) from exc
    schedule_id = (await cur.fetchone())[0]
    await write_event(
        conn,
        actor_user_id=actor_user_id,
        action="workflow.schedule_created",
        target=str(schedule_id),
        meta={"workflow": workflow_id, "cron": cron},
    )
    return schedule_id


async def delete_schedule(
    conn: psycopg.AsyncConnection,
    *,
    schedule_id: UUID,
    actor_user_id: UUID | None,
) -> None:
    cur = await conn.execute("DELETE FROM workflow_schedule WHERE id = %s", (schedule_id,))
    if cur.rowcount != 1:
        raise UnknownScheduleError(str(schedule_id))
    await write_event(
        conn,
        actor_user_id=actor_user_id,
        action="workflow.schedule_deleted",
        target=str(schedule_id),
        meta={},
    )


async def set_schedule_enabled(
    conn: psycopg.AsyncConnection,
    *,
    schedule_id: UUID,
    enabled: bool,
    actor_user_id: UUID | None,
) -> None:
    """Re-enabling recomputes next_fire_at from now — a long-disabled schedule
    must not fire a burst of stale catch-up runs."""
    cur = await conn.execute("SELECT cron FROM workflow_schedule WHERE id = %s", (schedule_id,))
    row = await cur.fetchone()
    if row is None:
        raise UnknownScheduleError(str(schedule_id))
    if enabled:
        fire = next_fire(row[0], datetime.now(_TZ))
        await conn.execute(
            "UPDATE workflow_schedule SET enabled = true, next_fire_at = %s WHERE id = %s",
            (fire, schedule_id),
        )
    else:
        await conn.execute(
            "UPDATE workflow_schedule SET enabled = false WHERE id = %s", (schedule_id,)
        )
    await write_event(
        conn,
        actor_user_id=actor_user_id,
        action="workflow.schedule_enabled_changed",
        target=str(schedule_id),
        meta={"enabled": enabled},
    )


async def list_schedules(
    conn: psycopg.AsyncConnection,
    *,
    workflow_id: str,
) -> list[dict[str, Any]]:
    cur = await conn.execute(
        "SELECT id, cron, input_template, enabled, last_fired_at, next_fire_at "
        "FROM workflow_schedule WHERE workflow_id = %s ORDER BY id",
        (workflow_id,),
    )
    return [
        {
            "id": r[0],
            "cron": r[1],
            "input_template": r[2],
            "enabled": r[3],
            "last_fired_at": r[4],
            "next_fire_at": r[5],
        }
        for r in await cur.fetchall()
    ]


# NULL next_fire_at (never computed) is due now: a fire is never silently
# lost; the tick seeds the forward deadline.
_DUE_SQL = """
SELECT id, workflow_id, cron, input_template
  FROM workflow_schedule
 WHERE enabled AND (next_fire_at IS NULL OR next_fire_at <= now())
   FOR UPDATE SKIP LOCKED
"""


async def tick_schedules(db: Database) -> int:
    """Fire every due schedule (D-P13C-2 advance-then-enqueue). Returns the
    number of runs enqueued. Never raises for per-schedule problems."""
    now = datetime.now(_TZ)
    due: list[tuple[UUID, str, dict[str, Any]]] = []
    async with db.conn(user_id=None, role="system") as conn:
        cur = await conn.execute(_DUE_SQL)
        rows = await cur.fetchall()
        for sid, wid, cron, tmpl in rows:
            try:
                fire = next_fire(cron, now)
            except ValueError as exc:
                # Poison row (D-P13C-4): disable + audit, never crash the loop.
                await conn.execute(
                    "UPDATE workflow_schedule SET enabled = false WHERE id = %s", (sid,)
                )
                await write_event(
                    conn,
                    actor_user_id=None,
                    action="workflow.schedule_disabled_invalid",
                    target=str(sid),
                    meta={"workflow": wid, "error": str(exc)},
                )
                continue
            await conn.execute(
                "UPDATE workflow_schedule SET last_fired_at = now(), "
                "next_fire_at = %s WHERE id = %s",
                (fire, sid),
            )
            due.append((sid, wid, tmpl))
    fired = 0
    for sid, wid, tmpl in due:
        # One transaction per enqueue: a start_run raise aborts the caller's
        # transaction (its documented contract), and the schedule advance
        # above must survive the skip.
        try:
            async with db.conn(user_id=None, role="system") as conn:
                await runs.start_run(
                    conn,
                    workflow_id=wid,
                    trigger="cron",
                    run_input=tmpl,
                    correlation_key=(
                        tmpl.get("correlation_key") if isinstance(tmpl, dict) else None
                    ),
                )
            fired += 1
        except (DuplicateActiveRunError, WorkflowDisabledError, UnknownWorkflowError) as exc:
            async with db.conn(user_id=None, role="system") as conn:
                await write_event(
                    conn,
                    actor_user_id=None,
                    action="workflow.schedule_skipped",
                    target=str(sid),
                    meta={"workflow": wid, "reason": type(exc).__name__},
                )
            logger.warning("schedule %s skipped: %s", sid, exc)
    return fired


async def scheduler_loop(
    db: Database,
    shutdown_event: asyncio.Event,
    *,
    tick_seconds: float,
) -> None:
    """Boot-launched sibling of runner_loop under the same flock (D-P13C-3).
    Never raises."""
    logger.info("workflow scheduler started")
    while not shutdown_event.is_set():
        try:
            await tick_schedules(db)
        except Exception:
            logger.exception("workflow schedule tick failed")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(shutdown_event.wait(), timeout=tick_seconds)
    logger.info("workflow scheduler stopped")
