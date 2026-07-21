"""P13c — workflow admin UI (spec §10): list/detail pages, enable toggle,
manual start, schedule CRUD; Task-8 adds run detail + approvals + manual
controls + waiting_manual resolutions. Every mutation is CSRF-protected,
audited (in the DAL), and followed by a Redis wake nudge when it creates
runnable work (D-P13C-9)."""

from __future__ import annotations

import html
import json
import uuid

import psycopg
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from dl_control.audit.service import write_event
from dl_control.auth.middleware import AuthedRequest, require_admin, require_csrf
from dl_control.auth.sessions import SessionStore
from dl_control.db import Database
from dl_control.i18n_routes import translator_for
from dl_control.workflows import manual, queries, runs, schedules
from dl_control.workflows.errors import (
    ApprovalNotPendingError,
    DuplicateActiveRunError,
    ManualTransitionError,
    UnknownScheduleError,
    UnknownWorkflowError,
    WorkflowDisabledError,
)
from dl_control.workflows.wake import publish_wake


def _banner(status_code: int, message: str) -> HTMLResponse:
    # Error text can echo user input (cron strings, JSON parse errors) —
    # escape it (Codex r2).
    return HTMLResponse(
        status_code=status_code,
        content=f'<div class="banner-error">{html.escape(message)}</div>',
    )


def _parse_input_json(raw: str) -> dict:
    """'' → {}; non-object or invalid JSON → ValueError."""
    if not raw.strip():
        return {}
    parsed = json.loads(raw)  # raises ValueError (JSONDecodeError) on bad JSON
    if not isinstance(parsed, dict):
        raise ValueError("input must be a JSON object")
    return parsed


def make_router(
    *,
    db: Database,
    sessions: SessionStore,
    templates: Jinja2Templates,
    settings,
    redis,
) -> APIRouter:
    r = APIRouter()
    admin = require_admin(sessions)
    csrf = require_csrf(sessions, site_host=settings.site_host)

    def _ctx(request: Request, authed: AuthedRequest, **extra):
        return {
            "current_user": authed.user_id,
            "csrf_token": authed.csrf_token,
            "active": "workflows",
            **extra,
        }

    # ── pages ───────────────────────────────────────────────────────────

    @r.get("/admin/workflows", response_class=HTMLResponse)
    async def workflows_list(request: Request, authed: AuthedRequest = admin):
        async with db.conn(user_id=authed.user_id, role="admin") as conn:
            flows = await queries.list_workflows(conn)
            approvals = await queries.list_pending_approvals(conn)
            manual_runs = await queries.list_waiting_manual(conn)
            recent = await queries.list_runs(conn, limit=20)
        return templates.TemplateResponse(
            request,
            "admin/workflows/list.html",
            _ctx(
                request,
                authed,
                flows=flows,
                approvals=approvals,
                manual_runs=manual_runs,
                recent=recent,
            ),
        )

    @r.get("/admin/workflows/{workflow_id}", response_class=HTMLResponse)
    async def workflow_detail(
        request: Request,
        workflow_id: str,
        authed: AuthedRequest = admin,
    ):
        async with db.conn(user_id=authed.user_id, role="admin") as conn:
            flow = await queries.get_workflow(conn, workflow_id=workflow_id)
            if flow is None:
                raise HTTPException(status_code=404, detail="unknown workflow")
            scheds = await schedules.list_schedules(conn, workflow_id=workflow_id)
            flow_runs = await queries.list_runs(conn, workflow_id=workflow_id)
            grants = await queries.list_grants(conn, workflow_id=workflow_id)
            grantable = await queries.list_grantable_agents(conn)
            active_agents = await queries.list_active_agents(conn)
        return templates.TemplateResponse(
            request,
            "admin/workflows/detail.html",
            _ctx(
                request,
                authed,
                flow=flow,
                schedules=scheds,
                runs=flow_runs,
                grants=grants,
                grantable=grantable,
                active_agents=active_agents,
            ),
        )

    # ── workflow mutations ──────────────────────────────────────────────

    @r.post("/admin/workflows/{workflow_id}/enabled", dependencies=[csrf])
    async def set_enabled(
        request: Request,
        workflow_id: str,
        authed: AuthedRequest = admin,
    ):
        form = await request.form()
        enabled = str(form.get("enabled", "")).lower() in ("1", "true", "yes")
        actor = uuid.UUID(authed.user_id)
        async with db.conn(user_id=authed.user_id, role="admin") as conn:
            cur = await conn.execute(
                "UPDATE workflow SET enabled = %s, updated_at = now() WHERE id = %s",
                (enabled, workflow_id),
            )
            if cur.rowcount != 1:
                raise HTTPException(status_code=404, detail="unknown workflow")
            await write_event(
                conn,
                actor_user_id=actor,
                action="workflow.enabled_changed",
                target=workflow_id,
                meta={"enabled": enabled},
            )
        if enabled:
            await publish_wake(redis, reason="workflow_enabled")
        return RedirectResponse("/admin/workflows", status_code=303)

    @r.post("/admin/workflows/{workflow_id}/default-agent", dependencies=[csrf])
    async def set_default_agent(
        request: Request,
        workflow_id: str,
        authed: AuthedRequest = admin,
    ):
        form = await request.form()
        raw = str(form.get("default_agent_id", "")).strip()
        if raw:
            try:
                agent_id = uuid.UUID(raw)
            except ValueError:
                return _banner(400, "Invalid agent UUID")
        else:
            agent_id = None  # clear to fallback
        actor = uuid.UUID(authed.user_id)
        async with db.conn(user_id=authed.user_id, role="admin") as conn:
            cur = await conn.execute(
                "UPDATE workflow SET default_agent_id = %s, updated_at = now() WHERE id = %s",
                (agent_id, workflow_id),
            )
            if cur.rowcount != 1:
                raise HTTPException(status_code=404, detail="unknown workflow")
            await write_event(
                conn,
                actor_user_id=actor,
                action="workflow.default_agent_updated",
                target=workflow_id,
                meta={"default_agent_id": str(agent_id) if agent_id else None},
            )
        # Refresh the in-memory config cache
        from dl_control.workflows.config_cache import set_default as cache_set_default

        cache_set_default(workflow_id, agent_id)
        return RedirectResponse(f"/admin/workflows/{workflow_id}", status_code=303)

    @r.post("/admin/workflows/{workflow_id}/start", dependencies=[csrf])
    async def start_manual_run(
        request: Request,
        workflow_id: str,
        authed: AuthedRequest = admin,
    ):
        t = translator_for(request)
        form = await request.form()
        try:
            run_input = _parse_input_json(str(form.get("input_json", "")))
        except ValueError as exc:
            return _banner(400, f"{t('workflows.err.bad_json')}: {exc}")
        correlation_key = str(form.get("correlation_key", "")).strip() or None
        try:
            async with db.conn(user_id=authed.user_id, role="admin") as conn:
                run_id = await runs.start_run(
                    conn,
                    workflow_id=workflow_id,
                    trigger="manual",
                    run_input=run_input,
                    correlation_key=correlation_key,
                    actor_user_id=uuid.UUID(authed.user_id),
                )
        except UnknownWorkflowError:
            raise HTTPException(status_code=404, detail="unknown workflow") from None
        except WorkflowDisabledError:
            return _banner(409, t("workflows.err.disabled"))
        except DuplicateActiveRunError:
            return _banner(409, t("workflows.err.live_run_exists"))
        await publish_wake(redis, reason="manual_start")
        return RedirectResponse(f"/admin/workflow-runs/{run_id}", status_code=303)

    # ── schedules ───────────────────────────────────────────────────────

    @r.post("/admin/workflows/{workflow_id}/schedules", dependencies=[csrf])
    async def create_schedule(
        request: Request,
        workflow_id: str,
        authed: AuthedRequest = admin,
    ):
        t = translator_for(request)
        form = await request.form()
        cron = str(form.get("cron", "")).strip()
        try:
            tmpl = _parse_input_json(str(form.get("input_json", "")))
        except ValueError as exc:
            return _banner(400, f"{t('workflows.err.bad_json')}: {exc}")
        try:
            async with db.conn(user_id=authed.user_id, role="admin") as conn:
                await schedules.create_schedule(
                    conn,
                    workflow_id=workflow_id,
                    cron=cron,
                    input_template=tmpl,
                    actor_user_id=uuid.UUID(authed.user_id),
                )
        except ValueError as exc:
            return _banner(400, f"{t('workflows.err.bad_cron')}: {exc}")
        except UnknownWorkflowError:
            raise HTTPException(status_code=404, detail="unknown workflow") from None
        return RedirectResponse(f"/admin/workflows/{workflow_id}", status_code=303)

    @r.post("/admin/workflows/schedules/{schedule_id}/delete", dependencies=[csrf])
    async def delete_schedule(
        request: Request,
        schedule_id: uuid.UUID,
        authed: AuthedRequest = admin,
    ):
        form = await request.form()
        workflow_id = str(form.get("workflow_id", ""))
        try:
            async with db.conn(user_id=authed.user_id, role="admin") as conn:
                await schedules.delete_schedule(
                    conn, schedule_id=schedule_id, actor_user_id=uuid.UUID(authed.user_id)
                )
        except UnknownScheduleError:
            raise HTTPException(status_code=404, detail="unknown schedule") from None
        return RedirectResponse(
            f"/admin/workflows/{workflow_id}" if workflow_id else "/admin/workflows",
            status_code=303,
        )

    @r.post("/admin/workflows/schedules/{schedule_id}/enabled", dependencies=[csrf])
    async def set_schedule_enabled(
        request: Request,
        schedule_id: uuid.UUID,
        authed: AuthedRequest = admin,
    ):
        form = await request.form()
        workflow_id = str(form.get("workflow_id", ""))
        enabled = str(form.get("enabled", "")).lower() in ("1", "true", "yes")
        try:
            async with db.conn(user_id=authed.user_id, role="admin") as conn:
                await schedules.set_schedule_enabled(
                    conn,
                    schedule_id=schedule_id,
                    enabled=enabled,
                    actor_user_id=uuid.UUID(authed.user_id),
                )
        except UnknownScheduleError:
            raise HTTPException(status_code=404, detail="unknown schedule") from None
        return RedirectResponse(
            f"/admin/workflows/{workflow_id}" if workflow_id else "/admin/workflows",
            status_code=303,
        )

    # ── agent grants (spec §5.7) ────────────────────────────────────────

    @r.post("/admin/workflows/{workflow_id}/grants", dependencies=[csrf])
    async def add_grant(
        request: Request,
        workflow_id: str,
        authed: AuthedRequest = admin,
    ):
        t = translator_for(request)
        form = await request.form()
        try:
            agent_id = uuid.UUID(str(form.get("agent_id", "")))
        except ValueError:
            return _banner(400, t("workflows.err.agent_id_uuid"))
        actor = uuid.UUID(authed.user_id)
        try:
            async with db.conn(user_id=authed.user_id, role="admin") as conn:
                await conn.execute(
                    "INSERT INTO workflow_agent_grant "
                    "(agent_id, workflow_id, granted_by) VALUES (%s, %s, %s) "
                    "ON CONFLICT DO NOTHING",
                    (agent_id, workflow_id, actor),
                )
                await write_event(
                    conn,
                    actor_user_id=actor,
                    action="workflow.grant_added",
                    target=workflow_id,
                    meta={"agent_id": str(agent_id)},
                )
        except psycopg.errors.ForeignKeyViolation:
            raise HTTPException(status_code=404, detail="unknown agent or workflow") from None
        return RedirectResponse(f"/admin/workflows/{workflow_id}", status_code=303)

    @r.post("/admin/workflows/{workflow_id}/grants/{agent_id}/delete", dependencies=[csrf])
    async def revoke_grant(
        request: Request,
        workflow_id: str,
        agent_id: uuid.UUID,
        authed: AuthedRequest = admin,
    ):
        actor = uuid.UUID(authed.user_id)
        async with db.conn(user_id=authed.user_id, role="admin") as conn:
            cur = await conn.execute(
                "DELETE FROM workflow_agent_grant WHERE agent_id = %s AND workflow_id = %s",
                (agent_id, workflow_id),
            )
            if cur.rowcount != 1:
                raise HTTPException(status_code=404, detail="grant not found")
            await write_event(
                conn,
                actor_user_id=actor,
                action="workflow.grant_removed",
                target=workflow_id,
                meta={"agent_id": str(agent_id)},
            )
        return RedirectResponse(f"/admin/workflows/{workflow_id}", status_code=303)

    # ── run detail + actions (Task 8) ───────────────────────────────────

    @r.get("/admin/workflow-runs/{run_id}", response_class=HTMLResponse)
    async def run_detail(
        request: Request,
        run_id: uuid.UUID,
        authed: AuthedRequest = admin,
    ):
        async with db.conn(user_id=authed.user_id, role="admin") as conn:
            timeline = await queries.get_run_timeline(conn, run_id=run_id)
        if timeline is None:
            raise HTTPException(status_code=404, detail="unknown run")
        unresolved = [e for e in timeline["ledger"] if e["status"] == "started"]
        inflight_calls = [
            c for c in timeline["agent_calls"] if c["status"] in ("posted", "dispatched")
        ]
        pending_approvals = [a for a in timeline["approvals"] if a["state"] == "pending"]
        return templates.TemplateResponse(
            request,
            "admin/workflows/run_detail.html",
            _ctx(
                request,
                authed,
                unresolved=unresolved,
                inflight_calls=inflight_calls,
                pending_approvals=pending_approvals,
                **timeline,
            ),
        )

    @r.post("/admin/workflow-runs/{run_id}/approval", dependencies=[csrf])
    async def decide_approval_route(
        request: Request,
        run_id: uuid.UUID,
        authed: AuthedRequest = admin,
    ):
        t = translator_for(request)
        form = await request.form()
        step_key = str(form.get("step_key", ""))
        decision = str(form.get("decision", ""))
        if decision not in ("approve", "reject") or not step_key:
            return _banner(400, t("workflows.err.bad_decision"))
        try:
            async with db.conn(user_id=authed.user_id, role="admin") as conn:
                await runs.decide_approval(
                    conn,
                    run_id=run_id,
                    step_key=step_key,
                    approved=(decision == "approve"),
                    decided_by=uuid.UUID(authed.user_id),
                )
        except ApprovalNotPendingError as exc:
            return _banner(409, f"{t('workflows.err.approval_not_pending')}: {exc}")
        await publish_wake(redis, reason="approval_decided")
        return RedirectResponse(f"/admin/workflow-runs/{run_id}", status_code=303)

    _controls = {
        "cancel": manual.cancel_run,
        "retry": manual.retry_run,
        "fail": manual.fail_run,
        "complete": manual.force_complete_run,
    }

    @r.post("/admin/workflow-runs/{run_id}/control", dependencies=[csrf])
    async def run_control(
        request: Request,
        run_id: uuid.UUID,
        authed: AuthedRequest = admin,
    ):
        t = translator_for(request)
        form = await request.form()
        action = str(form.get("action", ""))
        handler = _controls.get(action)
        if handler is None:
            return _banner(400, f"{t('workflows.err.unknown_action')} {action!r}")
        try:
            async with db.conn(user_id=authed.user_id, role="admin") as conn:
                await handler(conn, run_id=run_id, actor_user_id=uuid.UUID(authed.user_id))
        except ManualTransitionError as exc:
            return _banner(409, f"{t('workflows.err.bad_state')}: {exc}")
        if action == "retry":
            await publish_wake(redis, reason="manual_retry")
        return RedirectResponse(f"/admin/workflow-runs/{run_id}", status_code=303)

    @r.post("/admin/workflow-runs/{run_id}/resolve", dependencies=[csrf])
    async def resolve_manual(
        request: Request,
        run_id: uuid.UUID,
        authed: AuthedRequest = admin,
    ):
        t = translator_for(request)
        form = await request.form()
        action = str(form.get("action", ""))
        key = str(form.get("idempotency_key", ""))
        note = str(form.get("note", ""))
        actor = uuid.UUID(authed.user_id)
        try:
            async with db.conn(user_id=authed.user_id, role="admin") as conn:
                if action == "confirm_committed":
                    if not key:
                        return _banner(400, t("workflows.err.idem_required"))
                    await manual.confirm_committed(
                        conn, run_id=run_id, idempotency_key=key, note=note, actor_user_id=actor
                    )
                elif action == "confirm_not_sent":
                    if not key:
                        return _banner(400, t("workflows.err.idem_required"))
                    await manual.confirm_not_sent(
                        conn, run_id=run_id, idempotency_key=key, actor_user_id=actor
                    )
                elif action == "abandon":
                    await manual.abandon_run(conn, run_id=run_id, actor_user_id=actor)
                elif action == "repost_same_correlation":
                    await manual.repost_same_correlation(conn, run_id=run_id, actor_user_id=actor)
                elif action == "supersede":
                    await manual.supersede_dispatch(conn, run_id=run_id, actor_user_id=actor)
                else:
                    return _banner(400, f"{t('workflows.err.unknown_action')} {action!r}")
        except ManualTransitionError as exc:
            return _banner(409, f"{t('workflows.err.not_resolvable')}: {exc}")
        if action != "abandon":
            await publish_wake(redis, reason="manual_resolved")
        return RedirectResponse(f"/admin/workflow-runs/{run_id}", status_code=303)

    return r
