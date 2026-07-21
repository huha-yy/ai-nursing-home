"""P7 — internal-bearer OTA endpoints used by dl-ota-watcher.

Mirrors the P6 dl_control/audit/internal_routes.py pattern exactly.
"""

from __future__ import annotations

import secrets
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import UUID4, BaseModel, Field

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


class OtaAuditRequest(BaseModel):
    ota_version: str = Field(..., min_length=1, max_length=128)
    state: str = Field(..., min_length=1, max_length=128)
    journal_snapshot: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(..., min_length=1, max_length=64)


class OtaRollRequest(BaseModel):
    job_id: UUID4
    ota_version: str
    target_digest: str
    mode: Literal["apply", "rollback"] = "apply"


class OtaRollResponse(BaseModel):
    job_id: str
    status: str
    accepted_at: float | None = None
    completed: list[str] = Field(default_factory=list)
    failed_agent: str | None = None
    failed_rollback_agent: str | None = None
    started_at: float | None = None
    finished_at: float | None = None


def make_ota_internal_router(db: Database, redis, docker, settings) -> APIRouter:
    router = APIRouter()

    @router.post("/api/internal/ota/audit", status_code=201)
    async def post_audit(request: Request, body: OtaAuditRequest):
        _verify_internal_bearer(request, settings)
        async with db.conn(user_id=None, role="system") as conn:
            audit_id = await write_event(
                conn,
                actor_user_id=None,
                action=body.state,
                target=body.ota_version,
                meta={
                    "state": body.state,
                    "journal_snapshot": body.journal_snapshot,
                    "source": "dl-ota-watcher",
                    "watcher_timestamp": body.timestamp,
                },
            )
        return {"audit_id": audit_id}

    # ── roll-openclaw ────────────────────────────────────────────────

    from dl_control.ota.roll_openclaw import (
        get_job,
        run_loop,
        start_job,
    )

    @router.post("/api/internal/ota/roll-openclaw")
    async def post_roll(
        request: Request,
        body: OtaRollRequest,
        background: BackgroundTasks,
    ):
        _verify_internal_bearer(request, settings)
        existing = await get_job(redis, str(body.job_id))
        if existing is not None:
            return JSONResponse(
                status_code=200,
                content=OtaRollResponse(**existing).model_dump(),
            )
        state = await start_job(
            redis,
            job_id=str(body.job_id),
            ota_version=body.ota_version,
            target_digest=body.target_digest,
            mode=body.mode,
            ttl=settings.dato_ota_roll_job_ttl_seconds,
        )
        background.add_task(
            run_loop,
            redis=redis,
            db=db,
            docker=docker,
            settings=settings,
            job_id=str(body.job_id),
            target_digest=body.target_digest,
            mode=body.mode,
            ttl=settings.dato_ota_roll_job_ttl_seconds,
            health_window_seconds=settings.dato_ota_health_window_seconds,
        )
        return JSONResponse(
            status_code=202,
            content=OtaRollResponse(**state).model_dump(),
        )

    @router.get("/api/internal/ota/roll-openclaw/{job_id}")
    async def get_roll(request: Request, job_id: UUID):
        _verify_internal_bearer(request, settings)
        state = await get_job(redis, str(job_id))
        if state is None:
            raise HTTPException(404, detail="not_found")
        return OtaRollResponse(**state).model_dump()

    return router
