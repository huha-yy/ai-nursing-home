"""Admin internal API — /api/internal/admin (spec §8.5, P13d+).

Authenticates the calling agent by its per-agent DL_INTERNAL_TOKEN (sha256
against agents.internal_token_hash) and authorizes only the precreated
`agent-manager` agent.  All endpoints use role=system to bypass RLS.

This is the agent-facing admin surface that enables Agent Manager to manage
agents, workflows, and schedules entirely through conversation.
"""

from __future__ import annotations

import hashlib
import logging
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from dl_control.agents import registry as agent_registry
from dl_control.agents import service as agent_service
from dl_control.agents.provisioning.service import (
    AgentBusyError,
    AgentNotFoundError,
    ProvisioningConfig,
    ProvisioningError,
    restart_agent,
)
from dl_control.agents.schemas import AgentCreate
from dl_control.db import Database
from dl_control.workflows import queries as workflow_queries
from dl_control.workflows import schedules as schedule_service

logger = logging.getLogger(__name__)


class ScheduleCreateRequest(BaseModel):
    cron: str = Field(..., min_length=1, max_length=200)
    input_template: dict = Field(default_factory=dict)


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


async def _authorize_admin_agent(
    conn: psycopg.AsyncConnection,
    token: str,
) -> UUID | None:
    """Resolve token AND verify the agent is the precreated agent-manager.
    Returns the agent_id on success, None if unauthorized."""
    agent = await _resolve_agent(conn, token)
    if agent is None:
        return None
    agent_id, status = agent
    if status != "active":
        return None
    cur = await conn.execute(
        "SELECT 1 FROM agents WHERE id = %s AND precreated_id = 'agent-manager'", (agent_id,)
    )
    return agent_id if await cur.fetchone() else None


def make_admin_internal_router(
    *,
    db: Database,
    docker,
    cfg: ProvisioningConfig,
    redis=None,
) -> APIRouter:
    router = APIRouter()

    # ── Agent endpoints ──────────────────────────────────────────────

    @router.get("/api/internal/admin/agents")
    async def internal_list_agents(request: Request):
        token = _bearer_token(request)
        async with db.conn(user_id=None, role="system") as conn:
            authed = await _authorize_admin_agent(conn, token)
            if authed is None:
                raise HTTPException(status_code=403, detail="unauthorized")
            agents = await agent_registry.list_agents(conn)
            # Strip internal_token_hash (bytea) — can't be JSON-serialized
            for a in agents:
                a.pop("internal_token_hash", None)
            return {"agents": agents}

    @router.get("/api/internal/admin/agents/{agent_id}")
    async def internal_get_agent(request: Request, agent_id: UUID):
        token = _bearer_token(request)
        async with db.conn(user_id=None, role="system") as conn:
            authed = await _authorize_admin_agent(conn, token)
            if authed is None:
                raise HTTPException(status_code=403, detail="unauthorized")
            row = await agent_registry.get_agent(conn, str(agent_id))
            if row is None:
                raise HTTPException(status_code=404, detail="agent not found")
            return row

    @router.post("/api/internal/admin/agents", status_code=201)
    async def internal_create_agent(request: Request):
        token = _bearer_token(request)
        async with db.conn(user_id=None, role="system") as conn:
            authed = await _authorize_admin_agent(conn, token)
            if authed is None:
                raise HTTPException(status_code=403, detail="unauthorized")
        body = await request.json()
        req = AgentCreate(**body)
        try:
            agent = await agent_service.create_agent(
                db,
                actor_user_id=None,
                req=req,
            )
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return agent.model_dump()

    @router.delete("/api/internal/admin/agents/{agent_id}", status_code=204)
    async def internal_delete_agent(request: Request, agent_id: UUID):
        token = _bearer_token(request)
        async with db.conn(user_id=None, role="system") as conn:
            authed = await _authorize_admin_agent(conn, token)
            if authed is None:
                raise HTTPException(status_code=403, detail="unauthorized")
        ok = await agent_service.delete_agent(
            db,
            actor_user_id=None,
            agent_id=str(agent_id),
            docker=docker,
            cfg=cfg,
        )
        if not ok:
            raise HTTPException(status_code=404, detail="agent not found")

    @router.post("/api/internal/admin/agents/{agent_id}/restart")
    async def internal_restart_agent(request: Request, agent_id: UUID):
        token = _bearer_token(request)
        async with db.conn(user_id=None, role="system") as conn:
            authed = await _authorize_admin_agent(conn, token)
            if authed is None:
                raise HTTPException(status_code=403, detail="unauthorized")
        try:
            await restart_agent(
                db,
                docker,
                cfg,
                actor_user_id=None,
                agent_id=str(agent_id),
            )
        except AgentNotFoundError as exc:
            raise HTTPException(status_code=404, detail="agent not found") from exc
        except AgentBusyError as exc:
            raise HTTPException(
                status_code=409,
                detail="agent is in flight or not in a valid state",
            ) from exc
        except ProvisioningError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"restarted": True, "agent_id": str(agent_id)}

    # ── Workflow endpoints ───────────────────────────────────────────

    @router.get("/api/internal/admin/workflows")
    async def internal_list_workflows(request: Request):
        token = _bearer_token(request)
        async with db.conn(user_id=None, role="system") as conn:
            authed = await _authorize_admin_agent(conn, token)
            if authed is None:
                raise HTTPException(status_code=403, detail="unauthorized")
            workflows = await workflow_queries.list_workflows(conn)
            return {"workflows": workflows}

    @router.get("/api/internal/admin/workflows/{workflow_id}")
    async def internal_get_workflow(request: Request, workflow_id: str):
        token = _bearer_token(request)
        async with db.conn(user_id=None, role="system") as conn:
            authed = await _authorize_admin_agent(conn, token)
            if authed is None:
                raise HTTPException(status_code=403, detail="unauthorized")
            wf = await workflow_queries.get_workflow(conn, workflow_id=workflow_id)
            if wf is None:
                raise HTTPException(status_code=404, detail="workflow not found")
            return wf

    # ── Schedule endpoints ───────────────────────────────────────────

    @router.get("/api/internal/admin/workflows/{workflow_id}/schedules")
    async def internal_list_schedules(request: Request, workflow_id: str):
        token = _bearer_token(request)
        async with db.conn(user_id=None, role="system") as conn:
            authed = await _authorize_admin_agent(conn, token)
            if authed is None:
                raise HTTPException(status_code=403, detail="unauthorized")
            schedules = await schedule_service.list_schedules(
                conn,
                workflow_id=workflow_id,
            )
            return {"schedules": schedules}

    @router.post("/api/internal/admin/workflows/{workflow_id}/schedules", status_code=201)
    async def internal_create_schedule(
        request: Request,
        workflow_id: str,
        body: ScheduleCreateRequest,
    ):
        token = _bearer_token(request)
        try:
            async with db.conn(user_id=None, role="system") as conn:
                authed = await _authorize_admin_agent(conn, token)
                if authed is None:
                    raise HTTPException(status_code=403, detail="unauthorized")
                schedule_id = await schedule_service.create_schedule(
                    conn,
                    workflow_id=workflow_id,
                    cron=body.cron,
                    input_template=body.input_template,
                    actor_user_id=None,
                )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"schedule_id": str(schedule_id)}

    @router.delete(
        "/api/internal/admin/workflows/{workflow_id}/schedules/{schedule_id}",
        status_code=204,
    )
    async def internal_delete_schedule(
        request: Request,
        workflow_id: str,
        schedule_id: UUID,
    ):
        token = _bearer_token(request)
        from dl_control.workflows.errors import UnknownScheduleError

        async with db.conn(user_id=None, role="system") as conn:
            authed = await _authorize_admin_agent(conn, token)
            if authed is None:
                raise HTTPException(status_code=403, detail="unauthorized")
            try:
                await schedule_service.delete_schedule(
                    conn,
                    schedule_id=schedule_id,
                    actor_user_id=None,
                )
            except UnknownScheduleError as exc:
                raise HTTPException(status_code=404, detail="schedule not found") from exc

    # ── Workflow run endpoints ───────────────────────────────────────

    @router.get("/api/internal/admin/workflow-runs")
    async def internal_list_runs(
        request: Request,
        workflow_id: str | None = None,
        limit: int = 50,
    ):
        token = _bearer_token(request)
        async with db.conn(user_id=None, role="system") as conn:
            authed = await _authorize_admin_agent(conn, token)
            if authed is None:
                raise HTTPException(status_code=403, detail="unauthorized")
            from dl_control.workflows import queries as workflow_queries

            rows = await workflow_queries.list_runs(
                conn,
                workflow_id=workflow_id,
                limit=limit,
            )
            return {"runs": rows}

    @router.get("/api/internal/admin/workflow-runs/{run_id}")
    async def internal_get_run(request: Request, run_id: UUID):
        """Get full workflow run detail including step outputs and errors.
        Agent Manager uses this to check run results and retrieve published
        article URLs after a workflow completes."""
        token = _bearer_token(request)
        async with db.conn(user_id=None, role="system") as conn:
            authed = await _authorize_admin_agent(conn, token)
            if authed is None:
                raise HTTPException(status_code=403, detail="unauthorized")
            timeline = await workflow_queries.get_run_timeline(conn, run_id=run_id)
            if timeline is None:
                raise HTTPException(status_code=404, detail="run not found")
            return timeline

    @router.post("/api/internal/admin/workflows/{workflow_id}/start", status_code=201)
    async def internal_start_workflow(
        request: Request,
        workflow_id: str,
    ):
        """Start a workflow run as the system admin (bypasses agent grants).
        Agent Manager uses this to immediately execute any workflow."""
        token = _bearer_token(request)

        try:
            body = await request.json()
            run_input = body.get("input", {})
            correlation_key = body.get("correlation_key")
        except Exception:
            run_input = {}
            correlation_key = None

        from dl_control.workflows import runs as workflow_runs
        from dl_control.workflows.errors import (
            DuplicateActiveRunError,
            UnknownWorkflowError,
            WorkflowDisabledError,
        )

        try:
            async with db.conn(user_id=None, role="system") as conn:
                authed = await _authorize_admin_agent(conn, token)
                if authed is None:
                    raise HTTPException(status_code=403, detail="unauthorized")
                run_id = await workflow_runs.start_run(
                    conn,
                    workflow_id=workflow_id,
                    trigger="manual",
                    run_input=run_input,
                    correlation_key=correlation_key,
                    started_by_agent_id=None,
                    actor_user_id=None,
                )
        except UnknownWorkflowError:
            raise HTTPException(status_code=404, detail="unknown workflow") from None
        except WorkflowDisabledError:
            raise HTTPException(status_code=409, detail="workflow disabled") from None
        except DuplicateActiveRunError:
            raise HTTPException(status_code=409, detail="duplicate active run") from None

        if redis is not None:
            from dl_control.workflows.wake import publish_wake

            await publish_wake(redis, reason="admin_start")

        return {"run_id": str(run_id)}

    return router
