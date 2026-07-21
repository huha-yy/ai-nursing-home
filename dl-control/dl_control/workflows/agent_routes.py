"""P13d — agent-facing API: the result callback (workflow→agent leg, §7.1
step 3) and the agent→workflow API (§7.2). All endpoints authenticate the
calling agent by its per-agent DL_INTERNAL_TOKEN — sha256 against
agents.internal_token_hash, the P5 verify idiom (libraries/routes.py) — never
by payload identifiers. start is allow-listed via workflow_agent_grant
(§5.7); get is scoped on the durable started_by_agent_id column (§7.2)."""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from dl_control.audit.service import write_event
from dl_control.db import Database
from dl_control.workflows import agent_calls, runs
from dl_control.workflows.errors import (
    DuplicateActiveRunError,
    UnknownWorkflowError,
    WorkflowDisabledError,
)
from dl_control.workflows.wake import publish_wake

logger = logging.getLogger(__name__)


class AgentCallbackRequest(BaseModel):
    correlation_id: UUID
    status: Literal["ok", "error"]
    result: dict[str, Any] | None = None
    error: str | None = Field(default=None, max_length=2000)


class AgentStartRunRequest(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)
    correlation_key: str | None = Field(default=None, min_length=1, max_length=200)


def _bearer_token(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer ") or not auth[7:].strip():
        raise HTTPException(status_code=401, detail="missing bearer token")
    return auth[7:].strip()


async def _resolve_agent(
    conn: psycopg.AsyncConnection,
    token: str,
) -> tuple[UUID, str] | None:
    """Token → (agent_id, status), or None for an unknown token."""
    token_hash = hashlib.sha256(token.encode()).digest()
    cur = await conn.execute(
        "SELECT id, status FROM agents WHERE internal_token_hash = %s", (token_hash,)
    )
    row = await cur.fetchone()
    return (row[0], row[1]) if row else None


def make_agent_router(db: Database, redis) -> APIRouter:
    router = APIRouter()

    @router.post("/api/internal/workflows/agent-callback")
    async def agent_callback(request: Request, body: AgentCallbackRequest):
        token = _bearer_token(request)
        async with db.conn(user_id=None, role="system") as conn:
            agent = await _resolve_agent(conn, token)
            if agent is None:
                raise HTTPException(status_code=401, detail="invalid token")
            # Identity equality is the auth bar (§5.6); agent *status* is
            # deliberately not checked — an agent finishing a task
            # mid-restart may still report its result (D-P13D-9).
            payload = {"status": body.status, "result": body.result, "error": body.error}
            run_id = await agent_calls.record_response(
                conn, correlation_id=body.correlation_id, agent_id=agent[0], payload=payload
            )
            if run_id is None:
                # Stale (timed_out/superseded), unknown, or another agent's
                # correlation — uniform 409, logged, never applied (§5.6).
                logger.info(
                    "agent callback rejected (agent=%s correlation=%s)",
                    agent[0],
                    body.correlation_id,
                )
                raise HTTPException(status_code=409, detail="correlation is not awaiting a result")
            await write_event(
                conn,
                actor_user_id=None,
                action="workflow.agent_call_responded",
                target=str(run_id),
                meta={
                    "correlation_id": str(body.correlation_id),
                    "agent_id": str(agent[0]),
                    "status": body.status,
                },
            )
        await publish_wake(redis, reason="agent_callback")
        return {"applied": True, "run_id": str(run_id)}

    @router.post("/api/v1/workflows/{workflow_id}/runs", status_code=201)
    async def agent_start_run(
        request: Request,
        workflow_id: str,
        body: AgentStartRunRequest,
    ):
        token = _bearer_token(request)
        try:
            async with db.conn(user_id=None, role="system") as conn:
                agent = await _resolve_agent(conn, token)
                if agent is None:
                    raise HTTPException(status_code=401, detail="invalid token")
                agent_id, status = agent
                if status != "active":
                    raise HTTPException(status_code=403, detail="agent not active")
                cur = await conn.execute(
                    "SELECT 1 FROM workflow_agent_grant WHERE agent_id = %s AND workflow_id = %s",
                    (agent_id, workflow_id),
                )
                if await cur.fetchone() is None:
                    # Grant check BEFORE existence checks: unknown and
                    # ungranted flows are the same 403 (D-P13D-12 — the flow
                    # catalog is not enumerable through this endpoint).
                    raise HTTPException(status_code=403, detail="no grant for this workflow")
                run_id = await runs.start_run(
                    conn,
                    workflow_id=workflow_id,
                    trigger="agent",
                    run_input=body.input,
                    correlation_key=body.correlation_key,
                    started_by_agent_id=agent_id,
                )
        except UnknownWorkflowError:
            raise HTTPException(status_code=404, detail="unknown workflow") from None
        except WorkflowDisabledError:
            raise HTTPException(status_code=409, detail="workflow disabled") from None
        except DuplicateActiveRunError:
            raise HTTPException(status_code=409, detail="duplicate active run") from None
        await publish_wake(redis, reason="agent_start")
        return {"run_id": str(run_id)}

    @router.get("/api/v1/workflow-runs/{run_id}")
    async def agent_get_run(request: Request, run_id: UUID):
        token = _bearer_token(request)
        async with db.conn(user_id=None, role="system") as conn:
            agent = await _resolve_agent(conn, token)
            if agent is None:
                raise HTTPException(status_code=401, detail="invalid token")
            agent_id, status = agent
            if status != "active":
                raise HTTPException(status_code=403, detail="agent not active")
            cur = await conn.execute(
                "SELECT workflow_id, status, current_step, started_by_agent_id "
                "FROM workflow_run WHERE id = %s",
                (run_id,),
            )
            row = await cur.fetchone()
            if row is None or row[3] != agent_id:
                # Owner-column scoping (§7.2): non-owners get the same 404 as
                # a missing run — existence is not leaked.
                raise HTTPException(status_code=404, detail="unknown run")
            workflow_id, run_status, current_step = row[0], row[1], row[2]
            output = error = None
            if current_step:
                cur = await conn.execute(
                    "SELECT output, error FROM workflow_step WHERE run_id = %s AND step_key = %s",
                    (run_id, current_step),
                )
                srow = await cur.fetchone()
                if srow:
                    output, error = srow
        return {
            "run_id": str(run_id),
            "workflow_id": workflow_id,
            "status": run_status,
            "current_step": current_step,
            "output": output,
            "error": error,
        }

    return router
