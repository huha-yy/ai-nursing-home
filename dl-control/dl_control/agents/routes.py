"""Agent HTML views including P8 precreated apply and settings routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from dl_control.agents import service
from dl_control.agents.schemas import AgentCreate
from dl_control.auth.middleware import AuthedRequest, require_admin, require_csrf
from dl_control.auth.sessions import SessionStore
from dl_control.db import Database
from dl_control.i18n_routes import translator_for
from dl_control.agents.provisioning.skill_catalog import CUSTOM_SKILL_NAMES


def make_router(
    *,
    db: Database,
    sessions: SessionStore,
    templates: Jinja2Templates,
    settings=None,
) -> APIRouter:
    r = APIRouter()
    admin = require_admin(sessions)

    @r.get("/admin/agents", response_class=HTMLResponse)
    async def agents_list(request: Request, authed: AuthedRequest = admin):
        agents = await service.list_agents(db, actor_user_id=authed.user_id)
        # Build categorized skill list for the create form
        vendor_skills = ["admin-agent-mgmt", "feishu", "kg-search"]
        custom_skills = sorted(CUSTOM_SKILL_NAMES)
        return templates.TemplateResponse(
            request,
            "admin/agents_list.html",
            {
                "current_user": authed.user_id,
                "csrf_token": authed.csrf_token,
                "active": "agents",
                "agents": agents,
                "vendor_skills": vendor_skills,
                "custom_skills": custom_skills,
            },
        )

    @r.get("/admin/agents/{agent_id}", response_class=HTMLResponse)
    async def agent_detail(request: Request, agent_id: uuid.UUID, authed: AuthedRequest = admin):
        agent = await service.get_agent(db, actor_user_id=authed.user_id, agent_id=str(agent_id))
        if agent is None:
            raise HTTPException(status_code=404, detail="agent not found")
        vendor_skills = ["admin-agent-mgmt", "feishu", "kg-search"]
        custom_skills = sorted(CUSTOM_SKILL_NAMES)
        return templates.TemplateResponse(
            request,
            "admin/agent_detail.html",
            {
                "current_user": authed.user_id,
                "csrf_token": authed.csrf_token,
                "active": "agents",
                "agent": agent,
                "vendor_skills": vendor_skills,
                "custom_skills": custom_skills,
            },
        )

    # ── Create agent form + P8 apply/settings (settings-gated) ──
    if settings is not None:
        csrf = require_csrf(sessions, site_host=settings.site_host)

        @r.post("/admin/agents/create", dependencies=[csrf])
        async def create_agent_form(
            request: Request,
            authed: AuthedRequest = admin,
        ):
            t = translator_for(request)
            form = await request.form()
            display_name = str(form.get("display_name", "")).strip()
            if not display_name:
                return HTMLResponse(
                    status_code=400,
                    content=f'<div class="banner-error">{t("agents.create_form.name_required")}</div>',
                )
            tier = str(form.get("tier", "tier0")).strip()
            if tier not in ("tier0", "tier1"):
                tier = "tier0"
            skills_raw = form.getlist("skill_list")
            skill_list = [s.strip() for s in skills_raw if s.strip()]

            req = AgentCreate(
                display_name=display_name,
                tier=tier,
                skill_list=skill_list,
            )
            try:
                agent = await service.create_agent(
                    db,
                    actor_user_id=authed.user_id,
                    req=req,
                )
            except Exception as exc:
                return HTMLResponse(
                    status_code=500,
                    content=f'<div class="banner-error">{t("agents.err.create_failed")}: {exc}</div>',
                )
            return RedirectResponse(
                f"/admin/agents/{agent.id}",
                status_code=303,
            )

        @r.post(
            "/admin/agents/{agent_id}/precreated/apply",
            dependencies=[csrf],
        )
        @r.post(
            "/admin/agents/{agent_id}/precreated/apply",
            dependencies=[csrf],
        )
        async def precreated_apply(
            request: Request,
            agent_id: uuid.UUID,
            authed: AuthedRequest = admin,
        ):
            form = await request.form()
            expected_current_sha = form.get("expected_current_sha", "")
            prov_cfg = request.app.state.prov_cfg

            from dl_control.agents.provisioning.service import AgentNotFoundError
            from dl_control.precreated.apply import apply_seed
            from dl_control.precreated.errors import SeedShaConflict

            try:
                await apply_seed(
                    db,
                    agent_id=str(agent_id),
                    expected_current_sha=str(expected_current_sha),
                    actor_user_id=authed.user_id,
                    cfg=prov_cfg,
                )
            except SeedShaConflict:
                t = translator_for(request)
                return HTMLResponse(
                    status_code=409,
                    content=(f'<div class="banner-error">{t("agents.err.seed_changed")}</div>'),
                )
            except AgentNotFoundError:
                raise HTTPException(status_code=404) from None

            return RedirectResponse(
                f"/admin/agents/{agent_id}",
                status_code=303,
            )

        @r.post(
            "/admin/agents/{agent_id}/skills",
            dependencies=[csrf],
        )
        async def update_skills_form(
            request: Request,
            agent_id: uuid.UUID,
            authed: AuthedRequest = admin,
        ):
            t = translator_for(request)
            form = await request.form()
            skills_raw = form.getlist("skill_list")
            skill_list = [s.strip() for s in skills_raw if s.strip()]

            try:
                await service.update_agent_skills(
                    db,
                    actor_user_id=authed.user_id,
                    agent_id=str(agent_id),
                    skill_list=skill_list,
                    cfg=request.app.state.prov_cfg,
                )
            except Exception as exc:
                return HTMLResponse(
                    status_code=500,
                    content=f'<div class="banner-error">{t("agents.err.skills_save_failed")}: {exc}</div>',
                )
            return RedirectResponse(
                f"/admin/agents/{agent_id}",
                status_code=303,
            )

        # ── P8 settings routes ──────────────────────────────────
        @r.get(
            "/admin/settings/precreated",
            response_class=HTMLResponse,
        )
        async def precreated_settings(
            request: Request,
            authed: AuthedRequest = admin,
        ):
            from pathlib import Path

            prov_cfg = request.app.state.prov_cfg

            async with db.conn(user_id=authed.user_id, role="admin") as conn:
                cur = await conn.execute(
                    "SELECT ps.precreated_id, ps.suppressed_at, "
                    "ps.suppressed_by, u.username "
                    "FROM precreated_suppressions ps "
                    "LEFT JOIN users u ON u.id = ps.suppressed_by "
                    "ORDER BY ps.precreated_id"
                )
                rows = await cur.fetchall()

            suppressed = []
            for row in rows:
                precreated_id = row[0]
                seed_path = Path(prov_cfg.precreated_agents_root) / precreated_id / "agent.yaml"
                seed_exists = seed_path.is_file()
                display_name = precreated_id
                if seed_exists:
                    try:
                        from dl_control.precreated import loader as seed_loader

                        seed = seed_loader.load_seed(
                            Path(prov_cfg.precreated_agents_root),
                            precreated_id,
                        )
                        display_name = seed.display_name
                    except Exception:
                        pass
                suppressed.append(
                    {
                        "precreated_id": precreated_id,
                        "display_name": display_name,
                        "suppressed_at": row[1],
                        "suppressed_by_username": row[3] or "—",
                        "seed_exists": seed_exists,
                    }
                )

            return templates.TemplateResponse(
                request,
                "admin/settings/precreated.html",
                {
                    "current_user": authed.user_id,
                    "csrf_token": authed.csrf_token,
                    "active": "settings",
                    "suppressed": suppressed,
                },
            )

        @r.post(
            "/admin/precreated/{precreated_id}/unsuppress",
            dependencies=[csrf],
        )
        async def precreated_unsuppress(
            request: Request,
            precreated_id: str,
            authed: AuthedRequest = admin,
        ):
            from dl_control.audit.service import write_event
            from dl_control.precreated import suppressions

            async with db.conn(user_id=authed.user_id, role="admin") as conn:
                await suppressions.unsuppress(
                    conn,
                    precreated_id=precreated_id,
                )
                await write_event(
                    conn,
                    actor_user_id=authed.user_id,
                    action="precreated_unsuppressed",
                    target=precreated_id,
                )
            return RedirectResponse(
                "/admin/settings/precreated",
                status_code=303,
            )

        @r.post(
            "/admin/precreated/{precreated_id}/recreate-now",
            dependencies=[csrf],
        )
        async def precreated_recreate_now(
            request: Request,
            precreated_id: str,
            authed: AuthedRequest = admin,
        ):
            from dl_control.audit.service import write_event
            from dl_control.precreated import suppressions
            from dl_control.precreated.reconciler import reconcile_precreated

            prov_cfg = request.app.state.prov_cfg
            docker = request.app.state.docker

            async with db.conn(user_id=authed.user_id, role="admin") as conn:
                await suppressions.unsuppress(
                    conn,
                    precreated_id=precreated_id,
                )
                await write_event(
                    conn,
                    actor_user_id=authed.user_id,
                    action="precreated_unsuppressed",
                    target=precreated_id,
                )

            await reconcile_precreated(db, docker=docker, cfg=prov_cfg)

            return RedirectResponse(
                "/admin/settings/precreated",
                status_code=303,
            )

        @r.post(
            "/admin/precreated/{precreated_id}/forget",
            dependencies=[csrf],
        )
        async def precreated_forget(
            request: Request,
            precreated_id: str,
            authed: AuthedRequest = admin,
        ):
            from dl_control.audit.service import write_event
            from dl_control.precreated import suppressions

            async with db.conn(user_id=authed.user_id, role="admin") as conn:
                await suppressions.unsuppress(
                    conn,
                    precreated_id=precreated_id,
                )
                await write_event(
                    conn,
                    actor_user_id=authed.user_id,
                    action="precreated_suppression_forgotten",
                    target=precreated_id,
                )
            return RedirectResponse(
                "/admin/settings/precreated",
                status_code=303,
            )

    return r
