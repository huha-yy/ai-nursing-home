"""HTTP routes: GET/POST /login, POST /logout, POST /auth/nursing-login."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from redis.asyncio import Redis

from dl_control.audit.service import write_event
from dl_control.auth.middleware import (
    COOKIE_NAME,
    clear_session_cookie,
    require_csrf,
    set_session_cookie,
    ua_fingerprint,
)
from dl_control.auth.service import LoginError, try_login, try_nursing_login
from dl_control.auth.sessions import SessionStore
from dl_control.db import Database
from dl_control.i18n_routes import translator_for
from dl_control.settings import Settings


def make_router(
    *,
    db: Database,
    sessions: SessionStore,
    redis: Redis,
    templates: Jinja2Templates,
    settings: Settings,
) -> APIRouter:
    r = APIRouter()
    csrf = require_csrf(sessions, site_host=settings.site_host)

    @r.get("/login", response_class=HTMLResponse)
    async def login_get(request: Request):
        return templates.TemplateResponse(request, "login.html", {})

    @r.post("/login")
    async def login_post(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
    ):
        ip = request.client.host if request.client else "unknown"
        try:
            result = await try_login(
                db,
                redis,
                username=username,
                password=password,
                ip=ip,
                rate_limit_fails=settings.login_rate_limit_fails,
                rate_limit_window=settings.login_rate_limit_window_seconds,
            )
        except LoginError:
            t = translator_for(request)
            return templates.TemplateResponse(
                request,
                "login.html",
                {"error": t("auth.err.invalid")},
                status_code=401,
            )
        sess = await sessions.create(
            user_id=result.user_id,
            role=result.role,
            ip=ip,
            ua_fingerprint=ua_fingerprint(request.headers.get("user-agent")),
        )
        resp = RedirectResponse(url="/admin", status_code=302)
        set_session_cookie(resp, token=sessions.sign(sess.sid))
        return resp

    @r.post("/auth/nursing-login")
    async def nursing_login_post(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
    ):
        ip = request.client.host if request.client else "unknown"
        try:
            result = await try_nursing_login(
                db,
                redis,
                username=username,
                password=password,
                ip=ip,
                rate_limit_fails=settings.login_rate_limit_fails,
                rate_limit_window=settings.login_rate_limit_window_seconds,
            )
        except LoginError:
            t = translator_for(request)
            return templates.TemplateResponse(
                request,
                "login.html",
                {"error": t("auth.err.invalid")},
                status_code=401,
            )
        sess = await sessions.create(
            user_id=result.user_id,
            role=result.role,
            ip=ip,
            ua_fingerprint=ua_fingerprint(request.headers.get("user-agent")),
            name=result.name,
            dept=result.dept,
            building=result.building,
            floor=result.floor,
            username=result.username,
        )
        resp = RedirectResponse(url="/admin", status_code=302)
        set_session_cookie(resp, token=sessions.sign(sess.sid))
        return resp

    @r.post("/logout", dependencies=[csrf])
    async def logout(request: Request):
        raw = request.cookies.get(COOKIE_NAME, "")
        sid = sessions.unsign(raw) if raw else None
        if sid:
            sess = await sessions.load(sid)
            if sess is not None:
                await sessions.delete(sid)
                async with db.conn(user_id=sess.user_id, role=sess.role) as conn:
                    await write_event(
                        conn,
                        actor_user_id=sess.user_id,
                        action="logout",
                        target="session",
                    )
        resp = RedirectResponse(url="/login", status_code=302)
        clear_session_cookie(resp)
        return resp

    return r
