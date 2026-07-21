"""P7 — admin OTA routes: trigger + clear-suppression.

Form-encoded POST routes (the dashboard widget submits via plain HTML form).
Return 303 redirect to /admin. CSRF-protected. Trigger is rate-limited 1/min.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from dl_control.audit.service import write_event
from dl_control.auth.middleware import (
    AuthedRequest,
    require_admin,
    require_csrf,
)
from dl_control.auth.sessions import SessionStore
from dl_control.db import Database


async def _check_admin_rate_limit(redis, admin_user_id: str, *, key: str) -> None:
    rl_key = f"rl_ota_admin:{key}:{admin_user_id}"
    count = await redis.incr(rl_key)
    if count == 1:
        await redis.expire(rl_key, 60)
    if count > 1:
        raise HTTPException(status_code=429, detail="rate_limited")


def make_ota_admin_router(
    *,
    db: Database,
    redis,
    sessions: SessionStore,
    settings,
) -> APIRouter:
    router = APIRouter()
    admin = require_admin(sessions)
    csrf = require_csrf(sessions, site_host=settings.site_host)

    @router.post("/api/admin/ota/trigger", dependencies=[csrf])
    async def post_trigger(
        request: Request,
        authed: AuthedRequest = admin,
    ):
        await _check_admin_rate_limit(redis, authed.user_id, key="trigger")
        form = await request.form()
        force = form.get("force", "false").lower() in ("true", "1", "yes")
        async with db.conn(user_id=authed.user_id, role="admin") as conn:
            await write_event(
                conn,
                actor_user_id=authed.user_id,
                action="ota.trigger",
                target=None,
                meta={"force": force},
            )
        await redis.publish("dato:ota:trigger", json.dumps({"force": force}))
        return RedirectResponse("/admin", status_code=303)

    @router.post("/api/admin/ota/clear-suppression", dependencies=[csrf])
    async def post_clear_suppression(
        request: Request,
        authed: AuthedRequest = admin,
    ):
        form = await request.form()
        target_digest = form.get("target_digest", "")
        if not target_digest:
            raise HTTPException(status_code=400, detail="target_digest required")
        async with db.conn(user_id=authed.user_id, role="admin") as conn:
            await write_event(
                conn,
                actor_user_id=authed.user_id,
                action="ota.clear_suppression",
                target=target_digest,
                meta={},
            )
        await redis.publish(
            "dato:ota:clear-suppression",
            json.dumps({"target_digest": target_digest}),
        )
        return RedirectResponse("/admin", status_code=303)

    return router
