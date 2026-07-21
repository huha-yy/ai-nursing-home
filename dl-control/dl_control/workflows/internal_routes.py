"""P13c — event-intake endpoint (spec §8, D-P13C-8).

The integration point for event sources (Feishu handlers, webhooks, git
events, ops scripts): they authenticate with the shared DL_INTERNAL_API_KEY
(the P6 /api/internal/audit idiom) and dl-control enqueues the run."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from dl_control.audit.internal_routes import _verify_internal_bearer
from dl_control.db import Database
from dl_control.workflows import runs
from dl_control.workflows.errors import (
    DuplicateActiveRunError,
    UnknownWorkflowError,
    WorkflowDisabledError,
)
from dl_control.workflows.wake import publish_wake


class WorkflowEventRequest(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)
    correlation_key: str | None = Field(default=None, min_length=1, max_length=200)


def make_workflow_internal_router(db: Database, redis, settings) -> APIRouter:
    router = APIRouter()

    @router.post("/api/internal/workflows/{workflow_id}/events", status_code=201)
    async def post_event(
        request: Request,
        workflow_id: str,
        body: WorkflowEventRequest,
    ):
        _verify_internal_bearer(request, settings)
        try:
            async with db.conn(user_id=None, role="system") as conn:
                run_id = await runs.start_run(
                    conn,
                    workflow_id=workflow_id,
                    trigger="event",
                    run_input=body.input,
                    correlation_key=body.correlation_key,
                )
        except UnknownWorkflowError:
            raise HTTPException(status_code=404, detail="unknown workflow") from None
        except WorkflowDisabledError:
            raise HTTPException(status_code=409, detail="workflow disabled") from None
        except DuplicateActiveRunError:
            raise HTTPException(status_code=409, detail="duplicate active run") from None
        await publish_wake(redis, reason="event")
        return {"run_id": str(run_id)}

    return router
