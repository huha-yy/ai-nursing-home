"""Admin HTMX routes for the GBrain OAuth credentials wizard.

Follows the same pattern as dl_control.channels.feishu.credentials_routes.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from dl_control.agents.gbrain.credentials_service import (
    save_gbrain_credentials,
)
from dl_control.agents.provisioning.service import ProvisioningConfig
from dl_control.auth.middleware import (
    AuthedRequest,
    require_admin,
    require_csrf,
)
from dl_control.auth.sessions import SessionStore
from dl_control.db import Database
from dl_control.i18n_routes import translator_for
from dl_control.settings import Settings


def make_router(
    *,
    db: Database,
    sessions: SessionStore,
    settings: Settings,
    templates: Jinja2Templates,
) -> APIRouter:
    r = APIRouter(prefix="/admin/agents/{agent_id}/gbrain")
    admin = require_admin(sessions)
    csrf = require_csrf(sessions, site_host=settings.site_host)
    prov_cfg = ProvisioningConfig.from_settings(settings)

    @r.get("", response_class=HTMLResponse)
    async def wizard_page(
        agent_id: UUID,
        request: Request,
        authed: AuthedRequest = admin,
    ):
        """Render GBrain credentials wizard page."""
        t = translator_for(request)
        async with db.conn(user_id=authed.user_id, role="admin") as conn:
            cur = await conn.execute(
                "SELECT id, display_name, tier, needs_restart, status "
                "FROM agents WHERE id = %s",
                (str(agent_id),),
            )
            agent_row = await cur.fetchone()
            if agent_row is None:
                from starlette.responses import Response
                return Response(t("gbrain.err.agent_not_found"), status_code=404)

        agent_ctx = {
            "id": str(agent_id),
            "display_name": agent_row[1],
            "tier": agent_row[2],
            "needs_restart": agent_row[3],
            "status": agent_row[4],
        }

        return templates.TemplateResponse(
            request,
            "admin/gbrain/wizard.html",
            {
                "current_user": authed.user_id,
                "csrf_token": authed.csrf_token,
                "active": "agents",
                "agent": agent_ctx,
                "error": None,
                "success": False,
            },
        )

    @r.post("/credentials", response_class=HTMLResponse)
    async def save_credentials(
        agent_id: UUID,
        request: Request,
        client_id: str = Form(...),
        client_secret: str = Form(...),
        authed: AuthedRequest = admin,
        _csrf: None = csrf,
    ):
        """Save GBrain OAuth credentials."""
        t = translator_for(request)
        try:
            await save_gbrain_credentials(
                db,
                agent_id,
                client_id=client_id,
                client_secret=client_secret,
                prov_cfg=prov_cfg,
                admin_user_id=UUID(authed.user_id),
            )
            async with db.conn(user_id=authed.user_id, role="admin") as conn:
                cur = await conn.execute(
                    "SELECT status FROM agents WHERE id = %s", (str(agent_id),)
                )
                status_row = await cur.fetchone()
            return templates.TemplateResponse(
                request,
                "admin/gbrain/_wizard_form.html",
                {
                    "agent": {
                        "id": str(agent_id),
                        "gbrain_configured": True,
                        "needs_restart": True,
                        "status": status_row[0] if status_row else None,
                    },
                    "error": None,
                    "success": True,
                    "csrf_token": authed.csrf_token,
                },
            )
        except Exception as exc:
            error = f"{t('gbrain.err.save_failed')}: {exc}"

        return templates.TemplateResponse(
            request,
            "admin/gbrain/_wizard_form.html",
            {
                "agent": {
                    "id": str(agent_id),
                    "gbrain_configured": False,
                    "needs_restart": False,
                },
                "error": error,
                "success": False,
                "csrf_token": authed.csrf_token,
            },
        )

    return r
