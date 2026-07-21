"""GET /admin — the P1 dashboard (registry + audit counts + P6 LLM status)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from dl_control.agents import registry
from dl_control.auth.middleware import AuthedRequest, require_admin
from dl_control.auth.sessions import SessionStore
from dl_control.db import Database


def make_router(
    *,
    db: Database,
    sessions: SessionStore,
    templates: Jinja2Templates,
    redis=None,
) -> APIRouter:
    r = APIRouter()
    admin = require_admin(sessions)

    @r.get("/admin", response_class=HTMLResponse)
    async def dashboard(request: Request, authed: AuthedRequest = admin):
        async with db.conn(user_id=authed.user_id, role="admin") as conn:
            by_tier = await registry.count_agents_by_tier(conn)
            cur = await conn.execute(
                "SELECT "
                "count(*) FILTER (WHERE occurred_at >= CURRENT_DATE), "
                "count(*) FILTER (WHERE occurred_at >= CURRENT_DATE - INTERVAL '7 days') "
                "FROM audit_log"
            )
            today, week = await cur.fetchone()
            cur2 = await conn.execute("SELECT count(*) FROM precreated_suppressions")
            sc = await cur2.fetchone()
            suppressed_count = sc[0] if sc else 0

        # P6: fetch LLM status for the dashboard widget.
        llm_status = {}
        try:
            from dl_control.llm.status import get_llm_status
            from dl_control.settings import load_settings

            settings = load_settings()
            llm_status = await get_llm_status(
                model_name=settings.local_llm_default_model,
                keep_alive_seconds=settings.local_llm_keep_alive_seconds,
            )
        except Exception:
            pass

        # P7: fetch OTA status for the dashboard widget.
        ota = {}
        if redis is not None:
            try:
                from dl_control.ota.status import read_ota_status

                ota = await read_ota_status(redis) or {}
            except Exception:
                pass

        return templates.TemplateResponse(
            request,
            "admin/dashboard.html",
            {
                "current_user": authed.user_id,
                "csrf_token": authed.csrf_token,
                "active": "dashboard",
                "total_agents": sum(by_tier.values()),
                "tier0": by_tier.get("tier0", 0),
                "tier1": by_tier.get("tier1", 0),
                "events_today": today,
                "events_week": week,
                "llm_status": llm_status,
                "ota": ota,
                "suppressed_count": suppressed_count,
            },
        )

    return r
