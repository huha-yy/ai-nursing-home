"""JSON admin API for the agent registry — /api/v1/admin/agents (spec §8.5).

Cookie-authed, admin-only; mutations are CSRF-protected. This is the
registry's reachable CRUD surface; the HTML UI is read-only in P1.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from dl_control.agents import service
from dl_control.agents.provisioning import service as prov_service
from dl_control.agents.provisioning.errors import ProvisioningError
from dl_control.agents.provisioning.service import (
    AgentBusyError,
    AgentNotFoundError,
    ProvisioningConfig,
    TierNotSupportedError,
)
from dl_control.agents.schemas import AgentCreate, AgentUpdate
from dl_control.auth.middleware import AuthedRequest, require_admin, require_csrf
from dl_control.auth.sessions import SessionStore
from dl_control.db import Database
from dl_control.secrets_redaction import SecretInPayloadError
from dl_control.settings import Settings


def make_router(
    *, db: Database, sessions: SessionStore, settings: Settings, docker=None
) -> APIRouter:
    r = APIRouter(prefix="/api/v1/admin/agents")
    admin = require_admin(sessions)
    csrf = require_csrf(sessions, site_host=settings.site_host)
    prov_cfg = ProvisioningConfig.from_settings(settings)

    async def _run_lifecycle(op, agent_id: str, actor_user_id: str):
        """Shared exception mapping for provision/restart (spec §10.1)."""
        try:
            await op(
                db,
                docker,
                prov_cfg,
                actor_user_id=actor_user_id,
                agent_id=agent_id,
            )
        except AgentNotFoundError as exc:
            raise HTTPException(status_code=404, detail="agent not found") from exc
        except TierNotSupportedError as exc:
            raise HTTPException(
                status_code=409,
                detail="tier-1 agents are not provisionable until P4",
            ) from exc
        except AgentBusyError as exc:
            raise HTTPException(
                status_code=409, detail="agent is in flight or not in a valid state"
            ) from exc
        except ProvisioningError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @r.get("")
    async def list_agents(authed: AuthedRequest = admin):
        agents = await service.list_agents(db, actor_user_id=authed.user_id)
        return {"agents": [a.model_dump() for a in agents]}

    @r.post("", status_code=201, dependencies=[csrf])
    async def create_agent(req: AgentCreate, authed: AuthedRequest = admin):
        try:
            agent = await service.create_agent(db, actor_user_id=authed.user_id, req=req)
        except SecretInPayloadError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return agent.model_dump()

    @r.get("/{agent_id}")
    async def get_agent(agent_id: uuid.UUID, authed: AuthedRequest = admin):
        agent = await service.get_agent(db, actor_user_id=authed.user_id, agent_id=str(agent_id))
        if agent is None:
            raise HTTPException(status_code=404, detail="agent not found")
        return agent.model_dump()

    @r.patch("/{agent_id}", dependencies=[csrf])
    async def update_agent(agent_id: uuid.UUID, req: AgentUpdate, authed: AuthedRequest = admin):
        try:
            agent = await service.update_agent(
                db, actor_user_id=authed.user_id, agent_id=str(agent_id), req=req
            )
        except SecretInPayloadError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if agent is None:
            raise HTTPException(status_code=404, detail="agent not found")
        return agent.model_dump()

    @r.delete("/{agent_id}", status_code=204, dependencies=[csrf])
    async def delete_agent(agent_id: uuid.UUID, authed: AuthedRequest = admin):
        ok = await service.delete_agent(
            db,
            actor_user_id=authed.user_id,
            agent_id=str(agent_id),
            docker=docker,
            cfg=prov_cfg,
        )
        if not ok:
            raise HTTPException(status_code=404, detail="agent not found")

    # --- P2 provisioning endpoints (spec §10) ---

    @r.post("/{agent_id}/provision", dependencies=[csrf])
    async def provision_agent_endpoint(agent_id: uuid.UUID, authed: AuthedRequest = admin):
        await _run_lifecycle(prov_service.provision_agent, str(agent_id), authed.user_id)
        return RedirectResponse(url=f"/admin/agents/{agent_id}", status_code=303)

    @r.post("/{agent_id}/restart", dependencies=[csrf])
    async def restart_agent_endpoint(agent_id: uuid.UUID, authed: AuthedRequest = admin):
        await _run_lifecycle(prov_service.restart_agent, str(agent_id), authed.user_id)
        return RedirectResponse(url=f"/admin/agents/{agent_id}", status_code=303)

    return r
