"""GET /admin/audit — admin-only audit log viewer (paginated, 50/page)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from dl_control.auth.middleware import AuthedRequest, require_admin
from dl_control.auth.sessions import SessionStore
from dl_control.db import Database

_PAGE_SIZE = 50


def make_router(
    *,
    db: Database,
    sessions: SessionStore,
    templates: Jinja2Templates,
) -> APIRouter:
    r = APIRouter()
    admin = require_admin(sessions)

    @r.get("/admin/audit", response_class=HTMLResponse)
    async def audit_view(request: Request, page: int = 1, authed: AuthedRequest = admin):
        page = max(page, 1)
        offset = (page - 1) * _PAGE_SIZE
        async with db.conn(user_id=authed.user_id, role="admin") as conn:
            cur = await conn.execute(
                "SELECT a.occurred_at, a.actor_user_id, a.action, a.target, a.meta, "
                "COALESCE(u.username, n.name, '—') as actor_name "
                "FROM audit_log a "
                "LEFT JOIN users u ON a.actor_user_id = u.id "
                "LEFT JOIN nursing_users n ON a.actor_user_id::text = n.user_id "
                "ORDER BY a.occurred_at DESC LIMIT %s OFFSET %s",
                (_PAGE_SIZE, offset),
            )
            rows = await cur.fetchall()
            cur = await conn.execute(
                "SELECT "
                "count(*) FILTER (WHERE occurred_at >= CURRENT_DATE), "
                "count(*) FILTER (WHERE occurred_at >= CURRENT_DATE - INTERVAL '7 days') "
                "FROM audit_log"
            )
            today, week = await cur.fetchone()
        events = [
            {
                "occurred_at": row[0].strftime("%m月%d日 %H:%M"),
                "actor": row[5],
                "action": row[2],
                "target": row[3],
                "meta": row[4],
            }
            for row in rows
        ]
        return templates.TemplateResponse(
            request,
            "admin/audit_list.html",
            {
                "current_user": authed.user_id,
                "csrf_token": authed.csrf_token,
                "active": "audit",
                "events": events,
                "page": page,
                "events_today": today,
                "events_week": week,
                "has_next": len(rows) == _PAGE_SIZE,
            },
        )

    return r
