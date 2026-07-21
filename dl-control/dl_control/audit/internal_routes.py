"""P6 — internal audit endpoint used by dl-llm-proxy to log LLM calls.

The proxy has no DB credentials and the audit_log RLS policy
(migrations/0002_audit_log.sql:19-28) blocks direct writes for non-system
roles. This endpoint is the bridge: dl-control authenticates via shared
DL_INTERNAL_API_KEY and writes the row under role=system.
"""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from dl_control.audit.service import write_event
from dl_control.db import Database


def _verify_internal_bearer(request: Request, settings) -> None:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer")
    token = auth[len("Bearer ") :]
    expected = (
        settings.dl_internal_api_key.get_secret_value() if settings.dl_internal_api_key else None
    )
    if not expected or not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="invalid bearer")


class InternalAuditRequest(BaseModel):
    action: str = Field(..., min_length=1, max_length=64)
    actor_agent_id: str = Field(..., min_length=36, max_length=36)
    meta: dict[str, Any] = Field(default_factory=dict)


def make_internal_audit_router(db: Database, settings) -> APIRouter:
    router = APIRouter()

    @router.post("/api/internal/audit")
    async def post_audit(request: Request, body: InternalAuditRequest):
        _verify_internal_bearer(request, settings)
        async with db.conn(user_id=None, role="system") as conn:
            audit_id = await write_event(
                conn,
                actor_user_id=None,
                action=body.action,
                target=body.actor_agent_id,
                meta=body.meta,
            )
        return {"audit_id": audit_id}

    return router
