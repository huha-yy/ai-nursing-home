"""GET/POST /admin/password-change — forced first-login rotation (spec §6.6)."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from dl_control.audit.service import write_event
from dl_control.auth.middleware import AuthedRequest, require_admin, require_csrf
from dl_control.auth.service import hash_password, verify_password
from dl_control.auth.sessions import SessionStore
from dl_control.db import Database
from dl_control.i18n_routes import translator_for
from dl_control.settings import Settings

_MIN_PASSWORD_LEN = 8


def make_router(
    *,
    db: Database,
    sessions: SessionStore,
    templates: Jinja2Templates,
    settings: Settings,
) -> APIRouter:
    r = APIRouter()
    admin = require_admin(sessions)
    csrf = require_csrf(sessions, site_host=settings.site_host)

    def _render(request, authed, *, error=None, status_code=200):
        return templates.TemplateResponse(
            request,
            "admin/password_change.html",
            {
                "current_user": authed.user_id,
                "csrf_token": authed.csrf_token,
                "error": error,
            },
            status_code=status_code,
        )

    @r.get("/admin/password-change", response_class=HTMLResponse)
    async def page(request: Request, authed: AuthedRequest = admin):
        return _render(request, authed)

    @r.post("/admin/password-change", dependencies=[csrf])
    async def submit(
        request: Request,
        current_password: str = Form(...),
        new_password: str = Form(...),
        new_password_confirm: str = Form(...),
        authed: AuthedRequest = admin,
    ):
        t = translator_for(request)
        if new_password != new_password_confirm:
            return _render(request, authed, error=t("auth.pw.err.mismatch"), status_code=400)
        if len(new_password) < _MIN_PASSWORD_LEN:
            return _render(
                request,
                authed,
                error=t("auth.pw.err.too_short").format(n=_MIN_PASSWORD_LEN),
                status_code=400,
            )
        if new_password == current_password:
            return _render(request, authed, error=t("auth.pw.err.same"), status_code=400)
        async with db.conn(user_id=authed.user_id, role=authed.role) as conn:
            cur = await conn.execute(
                "SELECT password_hash FROM users WHERE id = %s", (authed.user_id,)
            )
            row = await cur.fetchone()
            if row is None or not verify_password(current_password, row[0]):
                return _render(
                    request, authed, error=t("auth.pw.err.wrong_current"), status_code=401
                )
            # Rotation invalidates every session for the user (spec §6.6).
            # Must run before the DB update so a Redis failure rolls back
            # rather than committing the password change with live sessions.
            await sessions.delete_all_for_user(authed.user_id)
            await conn.execute(
                "UPDATE users SET password_hash = %s, must_change_password = false WHERE id = %s",
                (hash_password(new_password), authed.user_id),
            )
            await write_event(
                conn,
                actor_user_id=authed.user_id,
                action="password_changed",
                target="user",
            )
        return RedirectResponse(url="/login", status_code=302)

    return r
