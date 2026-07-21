"""Admin HTMX routes for the pairings table (spec §9)."""

from __future__ import annotations

import contextlib
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from dl_control.auth.middleware import (
    AuthedRequest,
    require_admin,
    require_csrf,
)
from dl_control.auth.sessions import SessionStore
from dl_control.channels.feishu.pairings_service import (
    AccountMismatchError,
    PendingRequestNotFoundError,
    TombstoneExistsError,
    approve_pairing,
    delete_tombstone,
    revoke_pairing,
)
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
    r = APIRouter(prefix="/admin/agents/{agent_id}/feishu/pairings")
    admin = require_admin(sessions)
    csrf = require_csrf(sessions, site_host=settings.site_host)

    @r.post("/approve", response_class=HTMLResponse)
    async def approve(
        agent_id: UUID,
        request: Request,
        pending_id: str = Form(...),
        authed: AuthedRequest = admin,
        _csrf: None = csrf,
    ):
        """Approve a pending pairing request."""
        t = translator_for(request)
        pending_path = (
            Path(settings.agents_root) / str(agent_id) / "credentials" / "feishu-pairing.json"
        )
        pendings = read_pending(pending_path)
        pending = next((p for p in pendings if p.id == pending_id), None)
        if pending is None:
            return await _render_pairings_fragment(
                db,
                settings,
                templates,
                request,
                agent_id,
                authed=authed,
            )

        error = None
        try:
            await approve_pairing(
                db,
                agent_id,
                pending=pending,
                admin_user_id=UUID(authed.user_id),
                reconciler_state=reconciler_state,
            )
        except TombstoneExistsError:
            error = t("feishu.err.tombstone_exists")
        except AccountMismatchError:
            error = t("feishu.err.account_mismatch")
        except PendingRequestNotFoundError:
            pass

        return await _render_pairings_fragment(
            db,
            settings,
            templates,
            request,
            agent_id,
            authed=authed,
            error=error,
        )

    @r.post("/{pairing_id}/revoke", response_class=HTMLResponse)
    async def revoke(
        agent_id: UUID,
        pairing_id: UUID,
        request: Request,
        authed: AuthedRequest = admin,
        _csrf: None = csrf,
    ):
        """Revoke an approved pairing."""
        with contextlib.suppress(PendingRequestNotFoundError):
            await revoke_pairing(
                db,
                pairing_id,
                admin_user_id=UUID(authed.user_id),
                reconciler_state=reconciler_state,
            )
        return await _render_pairings_fragment(
            db,
            settings,
            templates,
            request,
            agent_id,
            authed=authed,
        )

    @r.post("/{pairing_id}/tombstone", response_class=HTMLResponse)
    async def tombstone(
        agent_id: UUID,
        pairing_id: UUID,
        request: Request,
        authed: AuthedRequest = admin,
        _csrf: None = csrf,
    ):
        """Delete a tombstone."""
        with contextlib.suppress(PendingRequestNotFoundError):
            await delete_tombstone(
                db,
                pairing_id,
                admin_user_id=UUID(authed.user_id),
                reconciler_state=reconciler_state,
            )
        return await _render_pairings_fragment(
            db,
            settings,
            templates,
            request,
            agent_id,
            authed=authed,
        )

    return r


async def _render_pairings_fragment(
    db: Database,
    settings: Settings,
    templates: Jinja2Templates,
    request: Request,
    agent_id: UUID,
    *,
    authed: AuthedRequest,
    error: str | None = None,
) -> HTMLResponse:
    """Query pairings table + pending file, render combined fragment."""
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

    pending_path = (
        Path(settings.agents_root) / str(agent_id) / "credentials" / "feishu-pairing.json"
    )
    pendings = read_pending(pending_path)
    known_norm = {r[2] for r in rows}
    pendings = [p for p in pendings if normalize_feishu_sender(p.sender_id) not in known_norm]

    return templates.TemplateResponse(
        request,
        "admin/feishu/_pairings_table.html",
        {
            "agent": {"id": str(agent_id)},
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
            "error": error,
            "csrf_token": authed.csrf_token,
        },
    )
