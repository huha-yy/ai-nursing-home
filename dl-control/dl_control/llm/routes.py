"""P6 — /api/internal/llm-status endpoint, authenticated by DL_INTERNAL_API_KEY."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, HTTPException, Request

from dl_control.db import Database
from dl_control.llm.status import get_llm_status


def make_llm_status_router(db: Database, settings) -> APIRouter:
    router = APIRouter()

    @router.get("/api/internal/llm-status")
    async def llm_status(request: Request):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            raise HTTPException(status_code=401)
        token = auth[len("Bearer ") :]
        expected = (
            settings.dl_internal_api_key.get_secret_value()
            if settings.dl_internal_api_key
            else None
        )
        if not expected or not secrets.compare_digest(token, expected):
            raise HTTPException(status_code=401)

        return await get_llm_status(
            model_name=settings.local_llm_default_model,
            keep_alive_seconds=settings.local_llm_keep_alive_seconds,
        )

    return router
