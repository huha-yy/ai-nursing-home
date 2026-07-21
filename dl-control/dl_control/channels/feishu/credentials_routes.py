"""Admin HTMX routes for the Feishu credential wizard (spec §9)."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from dl_control.agents.provisioning.service import ProvisioningConfig
from dl_control.auth.middleware import (
    AuthedRequest,
    require_admin,
    require_csrf,
)
from dl_control.auth.sessions import SessionStore
from dl_control.channels.feishu.credentials_service import (
    DuplicateAppIdError,
    save_feishu_credentials,
)
from dl_control.channels.feishu.feishu_client import FeishuValidationError
from dl_control.channels.feishu.pending_reader import read_pending
from dl_control.channels.feishu.reconciler import ReconcilerState
from dl_control.channels.normalize import normalize_feishu_sender
from dl_control.db import Database
from dl_control.i18n_routes import translator_for
from dl_control.settings import Settings


def make_router(
    *,
    db: Database,
    sessions: SessionStore,
    settings: Settings,
    templates: Jinja2Templates,
    reconciler_state: ReconcilerState,
) -> APIRouter:
    r = APIRouter(prefix="/admin/agents/{agent_id}/feishu")
    admin = require_admin(sessions)
    csrf = require_csrf(sessions, site_host=settings.site_host)
    prov_cfg = ProvisioningConfig.from_settings(settings)

    @r.get("", response_class=HTMLResponse)
    async def wizard_page(
        agent_id: UUID,
        request: Request,
        authed: AuthedRequest = admin,
    ):
        """Render Feishu wizard page (form + pending + pairings)."""
        t = translator_for(request)
        async with db.conn(user_id=authed.user_id, role="admin") as conn:
            cur = await conn.execute(
                "SELECT id, display_name, tier, "
                "       channel_config -> 'feishu' ->> 'app_id' AS app_id, "
                "       channel_config -> 'feishu' ->> 'account_id' AS account_id, "
                "       feishu_configured, needs_restart, status "
                "FROM agents WHERE id = %s",
                (str(agent_id),),
            )
            agent_row = await cur.fetchone()
            if agent_row is None:
                from starlette.responses import Response

                return Response(t("feishu.err.agent_not_found"), status_code=404)

        agent_ctx = {
            "id": str(agent_id),
            "display_name": agent_row[1],
            "tier": agent_row[2],
            "feishu_app_id": agent_row[3] or "",
            "feishu_account_id": agent_row[4] or "",
            "feishu_configured": agent_row[5],
            "needs_restart": agent_row[6],
            "status": agent_row[7],
        }

        # Gather pairings context for the combined page
        async with db.conn(user_id=None, role="system") as conn:
            cur = await conn.execute(
                "SELECT id, sender_id_raw, sender_id_normalized, sender_name, "
                "       status, approved_at, revoked_at "
                "FROM pairings WHERE agent_id = %s ORDER BY approved_at DESC",
                (str(agent_id),),
            )
            rows = list(await cur.fetchall())

        approved = [r for r in rows if r[4] == "approved"]
        tombs = [r for r in rows if r[4] == "revoked"]

        # OpenClaw writes pending pairings to ~/.openclaw/credentials/, which is
        # bind-mounted at <agents_root>/<agent_id>/credentials/ (NOT state/oauth).
        pending_path = (
            Path(settings.agents_root) / str(agent_id) / "credentials" / "feishu-pairing.json"
        )
        pendings = read_pending(pending_path)
        known_norm = {r[2] for r in rows}
        pendings = [p for p in pendings if normalize_feishu_sender(p.sender_id) not in known_norm]

        return templates.TemplateResponse(
            request,
            "admin/feishu/wizard.html",
            {
                "current_user": authed.user_id,
                "csrf_token": authed.csrf_token,
                "active": "agents",
                "agent": agent_ctx,
                "approved": [
                    {
                        "id": r[0],
                        "sender_id_raw": r[1],
                        "sender_id_normalized": r[2],
                        "sender_name": r[3],
                        "status": r[4],
                        "approved_at": r[5],
                    }
                    for r in approved
                ],
                "tombs": [
                    {
                        "id": r[0],
                        "sender_id_raw": r[1],
                        "sender_id_normalized": r[2],
                        "sender_name": r[3],
                        "revoked_at": r[6],
                    }
                    for r in tombs
                ],
                "pending": [
                    {
                        "id": p.id,
                        "code": p.code,
                        "sender_id": p.sender_id,
                        "sender_name": p.sender_name,
                        "created_at": p.created_at,
                    }
                    for p in pendings
                ],
                "error": None,
                "success": False,
            },
        )

    @r.post("/credentials", response_class=HTMLResponse)
    async def save_credentials(
        agent_id: UUID,
        request: Request,
        app_id: str = Form(...),
        app_secret: str = Form(...),
        account_id: str = Form(...),
        authed: AuthedRequest = admin,
        _csrf: None = csrf,
    ):
        """Save Feishu credentials."""
        t = translator_for(request)
        try:
            await save_feishu_credentials(
                db,
                agent_id,
                app_id=app_id,
                app_secret=app_secret,
                account_id=account_id,
                prov_cfg=prov_cfg,
                feishu_base_url=settings.feishu_validate_base_url,
                admin_user_id=UUID(authed.user_id),
            )
            # Saving credentials does not change the lifecycle status. Read it
            # so the needs-restart banner can offer one-click Restart only when
            # the agent is 'active' (else it links to the detail page to
            # provision) — see _needs_restart_banner.html.
            async with db.conn(user_id=authed.user_id, role="admin") as conn:
                cur = await conn.execute(
                    "SELECT status FROM agents WHERE id = %s", (str(agent_id),)
                )
                status_row = await cur.fetchone()
            return templates.TemplateResponse(
                request,
                "admin/feishu/_wizard_form.html",
                {
                    "agent": {
                        "id": str(agent_id),
                        "feishu_app_id": app_id,
                        "feishu_account_id": account_id,
                        "feishu_configured": True,
                        "needs_restart": True,
                        "status": status_row[0] if status_row else None,
                    },
                    "error": None,
                    "success": True,
                    "csrf_token": authed.csrf_token,
                },
            )
        except DuplicateAppIdError:
            error = t("feishu.err.duplicate_app")
        except FeishuValidationError as exc:
            error = f"{t('feishu.err.rejected')}: {exc}"
        except ValueError:
            # The save path's only ValueError is safe_account_key()'s account-id
            # grammar validation (credentials_service.py:50) — map to a localized
            # key rather than echoing str(exc).
            error = t("feishu.err.bad_account_id")

        return templates.TemplateResponse(
            request,
            "admin/feishu/_wizard_form.html",
            {
                "agent": {
                    "id": str(agent_id),
                    "feishu_app_id": app_id,
                    "feishu_account_id": account_id,
                    "feishu_configured": False,
                    "needs_restart": False,
                },
                "error": error,
                "success": False,
                "csrf_token": authed.csrf_token,
            },
        )

    return r
