"""Read-only admin-UI queries (spec §10). All run on the caller's admin/system
connection; no mutations here."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg

def _fmt(dt) -> str | None:
    """Format datetime to YYYY-MM-DD HH:MM:SS Beijing time."""
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%d %H:%M:%S")


_ACTIVE = (
    "pending",
    "running",
    "waiting_approval",
    "waiting_agent",
    "waiting_timer",
    "waiting_retry",
    "waiting_manual",
)


async def get_workflow(
    conn: psycopg.AsyncConnection,
    *,
    workflow_id: str,
) -> dict[str, Any] | None:
    cur = await conn.execute(
        "SELECT id, display_name, description, enabled, latest_version, "
        "default_trigger, default_agent_id FROM workflow WHERE id = %s",
        (workflow_id,),
    )
    r = await cur.fetchone()
    if r is None:
        return None
    return {
        "id": r[0],
        "display_name": r[1],
        "description": r[2],
        "enabled": r[3],
        "latest_version": r[4],
        "default_trigger": r[5],
        "default_agent_id": str(r[6]) if r[6] else None,
    }


async def list_workflows(conn: psycopg.AsyncConnection) -> list[dict[str, Any]]:
    cur = await conn.execute(
        "SELECT w.id, w.display_name, w.description, w.enabled, "
        "w.latest_version, w.default_trigger, "
        "(SELECT count(*) FROM workflow_run r "
        " WHERE r.workflow_id = w.id AND r.status = ANY(%s)) AS active_runs "
        "FROM workflow w ORDER BY w.id",
        (list(_ACTIVE),),
    )
    return [
        {
            "id": r[0],
            "display_name": r[1],
            "description": r[2],
            "enabled": r[3],
            "latest_version": r[4],
            "default_trigger": r[5],
            "active_runs": r[6],
        }
        for r in await cur.fetchall()
    ]


async def list_runs(
    conn: psycopg.AsyncConnection,
    *,
    workflow_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    where = "WHERE workflow_id = %s" if workflow_id else ""
    params: tuple = (workflow_id, limit) if workflow_id else (limit,)
    cur = await conn.execute(
        "SELECT id, workflow_id, workflow_version, status, trigger, "
        f"correlation_key, current_step, created_at, finished_at, wake_at "
        f"FROM workflow_run {where} ORDER BY created_at DESC LIMIT %s",
        params,
    )
    return [
        {
            "id": r[0],
            "workflow_id": r[1],
            "workflow_version": r[2],
            "status": r[3],
            "trigger": r[4],
            "correlation_key": r[5],
            "current_step": r[6],
            "created_at": _fmt(r[7]),
            "finished_at": _fmt(r[8]),
            "wake_at": _fmt(r[9]),
        }
        for r in await cur.fetchall()
    ]


async def get_run_timeline(
    conn: psycopg.AsyncConnection,
    *,
    run_id: UUID,
) -> dict[str, Any] | None:
    """The run-detail page payload: run header + steps + approvals + ledger."""
    cur = await conn.execute(
        "SELECT id, workflow_id, workflow_version, status, trigger, input, "
        "correlation_key, current_step, wake_at, created_at, updated_at, "
        "finished_at FROM workflow_run WHERE id = %s",
        (run_id,),
    )
    r = await cur.fetchone()
    if r is None:
        return None
    run = {
        "id": r[0],
        "workflow_id": r[1],
        "workflow_version": r[2],
        "status": r[3],
        "trigger": r[4],
        "input": r[5],
        "correlation_key": r[6],
        "current_step": r[7],
        "wake_at": _fmt(r[8]),
        "created_at": _fmt(r[9]),
        "updated_at": _fmt(r[10]),
        "finished_at": _fmt(r[11]),
    }
    cur = await conn.execute(
        "SELECT step_key, status, attempt, started_at, finished_at, "
        "next_attempt_at, output, error FROM workflow_step "
        "WHERE run_id = %s ORDER BY started_at NULLS LAST, step_key",
        (run_id,),
    )
    steps = [
        {
            "step_key": s[0],
            "status": s[1],
            "attempt": s[2],
            "started_at": _fmt(s[3]),
            "finished_at": _fmt(s[4]),
            "next_attempt_at": _fmt(s[5]),
            "output": s[6],
            "error": s[7],
        }
        for s in await cur.fetchall()
    ]
    cur = await conn.execute(
        "SELECT step_key, prompt, state, decided_by, decided_at, created_at "
        "FROM workflow_approval WHERE run_id = %s ORDER BY created_at",
        (run_id,),
    )
    approvals = [
        {
            "step_key": a[0],
            "prompt": a[1],
            "state": a[2],
            "decided_by": a[3],
            "decided_at": _fmt(a[4]),
            "created_at": _fmt(a[5]),
        }
        for a in await cur.fetchall()
    ]
    cur = await conn.execute(
        "SELECT idempotency_key, step_key, attempt, target, status, response, "
        "created_at, updated_at FROM side_effect_ledger "
        "WHERE run_id = %s ORDER BY created_at",
        (run_id,),
    )
    ledger = [
        {
            "idempotency_key": e[0],
            "step_key": e[1],
            "attempt": e[2],
            "target": e[3],
            "status": e[4],
            "response": e[5],
            "created_at": _fmt(e[6]),
            "updated_at": _fmt(e[7]),
        }
        for e in await cur.fetchall()
    ]
    cur = await conn.execute(
        "SELECT correlation_id, step_key, attempt, agent_id, status, "
        "dispatch_count, response, created_at, responded_at FROM agent_call "
        "WHERE run_id = %s ORDER BY attempt",
        (run_id,),
    )
    agent_call_rows = [
        {
            "correlation_id": c[0],
            "step_key": c[1],
            "attempt": c[2],
            "agent_id": c[3],
            "status": c[4],
            "dispatch_count": c[5],
            "response": c[6],
            "created_at": _fmt(c[7]),
            "responded_at": _fmt(c[8]),
        }
        for c in await cur.fetchall()
    ]
    return {
        "run": run,
        "steps": steps,
        "approvals": approvals,
        "ledger": ledger,
        "agent_calls": agent_call_rows,
    }


async def list_pending_approvals(
    conn: psycopg.AsyncConnection,
) -> list[dict[str, Any]]:
    """The approval inbox: pending gates on runs actually waiting for them."""
    cur = await conn.execute(
        "SELECT a.run_id, a.step_key, a.prompt, a.created_at, r.workflow_id "
        "FROM workflow_approval a JOIN workflow_run r ON r.id = a.run_id "
        "WHERE a.state = 'pending' AND r.status = 'waiting_approval' "
        "ORDER BY a.created_at"
    )
    return [
        {"run_id": r[0], "step_key": r[1], "prompt": r[2], "created_at": _fmt(r[3]), "workflow_id": r[4]}
        for r in await cur.fetchall()
    ]


async def list_waiting_manual(
    conn: psycopg.AsyncConnection,
) -> list[dict[str, Any]]:
    """The parked-run inbox: waiting_manual runs + their unresolved ledger rows."""
    cur = await conn.execute(
        "SELECT id, workflow_id, current_step, updated_at FROM workflow_run "
        "WHERE status = 'waiting_manual' ORDER BY updated_at"
    )
    out = []
    for run_id, workflow_id, current_step, updated_at in await cur.fetchall():
        lcur = await conn.execute(
            "SELECT idempotency_key, target FROM side_effect_ledger "
            "WHERE run_id = %s AND status = 'started' ORDER BY created_at",
            (run_id,),
        )
        out.append(
            {
                "run_id": run_id,
                "workflow_id": workflow_id,
                "current_step": current_step,
                "updated_at": _fmt(updated_at),
                "unresolved": [
                    {"idempotency_key": le[0], "target": le[1]} for le in await lcur.fetchall()
                ],
            }
        )
    return out


async def list_grants(
    conn: psycopg.AsyncConnection,
    *,
    workflow_id: str,
) -> list[dict[str, Any]]:
    """Grants for the workflow-detail page (§5.7 — managed alongside enable)."""
    cur = await conn.execute(
        "SELECT g.agent_id, a.display_name, a.status, g.granted_at "
        "FROM workflow_agent_grant g JOIN agents a ON a.id = g.agent_id "
        "WHERE g.workflow_id = %s ORDER BY a.display_name",
        (workflow_id,),
    )
    return [
        {"agent_id": r[0], "display_name": r[1], "status": r[2], "granted_at": _fmt(r[3])}
        for r in await cur.fetchall()
    ]


async def list_grantable_agents(
    conn: psycopg.AsyncConnection,
) -> list[dict[str, Any]]:
    """Active agents for the add-grant select."""
    cur = await conn.execute(
        "SELECT id, display_name FROM agents WHERE status = 'active' ORDER BY display_name"
    )
    return [{"id": r[0], "display_name": r[1]} for r in await cur.fetchall()]


async def list_active_agents(
    conn: psycopg.AsyncConnection,
) -> list[dict[str, Any]]:
    """Active agents for the default_agent_id dropdown."""
    cur = await conn.execute(
        "SELECT id, display_name FROM agents WHERE status = 'active' ORDER BY display_name"
    )
    return [{"id": str(r[0]), "display_name": r[1]} for r in await cur.fetchall()]
