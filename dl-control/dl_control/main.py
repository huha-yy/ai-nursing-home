"""FastAPI app factory + lazy ASGI entrypoint."""

from __future__ import annotations

import asyncio
import fcntl
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from dl_shared.rate_limit import RateLimitMiddleware
from fastapi import FastAPI
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from redis.asyncio import Redis
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request as _Request
from starlette.responses import RedirectResponse as StarletteRedirect

from dl_control import i18n
from dl_control.auth import routes as auth_routes
from dl_control.auth.errors import MustRotatePasswordError
from dl_control.auth.middleware import require_password_rotated
from dl_control.auth.sessions import SessionStore
from dl_control.db import Database
from dl_control.logging import configure_logging
from dl_control.settings import load_settings

PACKAGE_DIR = Path(__file__).parent


def _i18n_context(request: _Request) -> dict:
    lang = i18n.normalize_lang(request.cookies.get(i18n.LANG_COOKIE))
    return {
        "lang": lang,
        "html_lang": i18n.HTML_LANG[lang],
        "t": i18n.translator(lang),
    }


TEMPLATES = Jinja2Templates(
    directory=str(PACKAGE_DIR / "templates"),
    context_processors=[_i18n_context],
)


async def build_app() -> FastAPI:
    """Build a fully wired FastAPI app. Migrations are NOT run here — the
    dato-control-migrate one-shot owns them (spec §4.1, §9)."""
    configure_logging()
    s = load_settings()

    # P2: fail fast if the agents data root is not a writable directory
    # (spec §4.2). The host/container path correspondence is an operator
    # invariant; this catches a missing or read-only mount.
    agents_root = Path(s.agents_root)
    if not (agents_root.is_dir() and os.access(agents_root, os.W_OK)):
        raise RuntimeError(
            f"agents root {agents_root} is missing or not writable — "
            "check the dato-control agents-root bind mount"
        )

    db = Database(dsn=s.db_url.get_secret_value())
    await db.connect()
    # Fail fast if the schema is missing rather than serving an empty DB.
    try:
        async with db.conn(user_id=None, role="system") as conn:
            await conn.execute("SELECT 1 FROM users LIMIT 1")
    except Exception as exc:  # noqa: BLE001
        await db.close()
        raise RuntimeError(
            "dl-control schema is missing — run the dato-control-migrate "
            "one-shot before starting the app"
        ) from exc

    # P2: sweep agents stranded in 'provisioning' by a prior crash (spec §9.4).
    from dl_control.agents.provisioning.service import reconcile_stale_provisioning

    await reconcile_stale_provisioning(db)

    # P13c+: populate workflow config cache from the DB.
    from dl_control.workflows import config_cache

    await config_cache.populate(db)

    redis = Redis.from_url(s.redis_url.get_secret_value(), decode_responses=True)
    sessions = SessionStore(
        redis=redis,
        ttl_seconds=s.session_ttl_seconds,
        secret_key=s.secret_key.get_secret_value(),
    )

    from dl_control.agents.provisioning.docker_client import DockerClient

    docker = DockerClient.from_host(s.docker_host)

    # P8: construct ProvisioningConfig and reconcile precreated agents at startup.
    from dl_control.agents.provisioning.service import ProvisioningConfig

    prov_cfg = ProvisioningConfig.from_settings(s)

    from dl_control.precreated.reconciler import reconcile_precreated

    await reconcile_precreated(db, docker=docker, cfg=prov_cfg)

    app = FastAPI(dependencies=[require_password_rotated(db=db, store=sessions)])

    # P11: recover active agents whose containers are gone/stopped (spec SS3).
    from dl_control.agents.provisioning.service import reconcile_active_agents

    app.state.active_agents_reconcile_task = asyncio.create_task(
        reconcile_active_agents(
            db,
            docker,
            prov_cfg,
            concurrency=s.reconcile_concurrency,
        )
    )

    # Coarse per-IP flood gate on the login route (spec §9). The finer
    # per-username/per-IP lockout lives inside try_login.
    def _login_rate_key(request) -> str:
        if request.url.path == "/login" and request.method == "POST":
            return request.client.host if request.client else "unknown"
        return ""

    app.add_middleware(
        RateLimitMiddleware,
        redis=redis,
        max_requests=20,
        window_seconds=s.login_rate_limit_window_seconds,
        key_fn=_login_rate_key,
        prefix="rl_login:",
    )

    from dl_control.middleware.health_signal import HealthSignalMiddleware

    app.add_middleware(HealthSignalMiddleware, db=db)

    app.mount(
        "/static",
        StaticFiles(directory=str(PACKAGE_DIR / "static")),
        name="static",
    )
    app.include_router(
        auth_routes.make_router(
            db=db,
            sessions=sessions,
            redis=redis,
            templates=TEMPLATES,
            settings=s,
        )
    )

    from dl_control import i18n_routes

    app.include_router(i18n_routes.make_router(settings=s))

    from dl_control.agents import api as agents_api
    from dl_control.auth import password_change

    app.include_router(
        password_change.make_router(
            db=db,
            sessions=sessions,
            templates=TEMPLATES,
            settings=s,
        )
    )

    app.include_router(agents_api.make_router(db=db, sessions=sessions, settings=s, docker=docker))

    from dl_control.agents import routes as agents_routes
    from dl_control.audit import routes as audit_routes
    from dl_control.dashboard import routes as dashboard_routes

    app.include_router(
        agents_routes.make_router(
            db=db,
            sessions=sessions,
            templates=TEMPLATES,
            settings=s,
        )
    )
    app.include_router(
        audit_routes.make_router(
            db=db,
            sessions=sessions,
            templates=TEMPLATES,
        )
    )
    app.include_router(
        dashboard_routes.make_router(
            db=db,
            sessions=sessions,
            templates=TEMPLATES,
            redis=redis,
        )
    )

    @app.get("/api/health")
    async def health():
        async with db.conn(user_id=None, role="system") as conn:
            await conn.execute("SELECT 1")
        return {"status": "ok"}

    @app.get("/")
    async def root():
        return RedirectResponse(url="/admin", status_code=302)

    # -- Nursing web UI routes (Task 4) --
    from dl_control.auth.middleware import COOKIE_NAME as _NURSING_COOKIE

    _NURSING_ROLES = frozenset(
        {"director", "nursing_dept", "logistics_dept", "building", "floor", "general"}
    )

    @app.get("/chat", response_class=HTMLResponse)
    async def nursing_chat(request: _Request):
        raw = request.cookies.get(_NURSING_COOKIE, "")
        sid = sessions.unsign(raw) if raw else None
        sess = await sessions.load(sid) if sid else None
        if sess is None or sess.role not in _NURSING_ROLES:
            return RedirectResponse(url="/login", status_code=302)
        nursing_user = {
            "user_id": sess.user_id,
            "username": sess.username,
            "name": sess.name,
            "role": sess.role,
            "dept": sess.dept,
            "building": sess.building,
            "floor": sess.floor,
        }
        return TEMPLATES.TemplateResponse(
            request,
            "nursing/chat.html",
            {"active": "chat", "nursing_user": nursing_user, "csrf_token": sess.csrf_token},
        )

    @app.get("/nursing/test-roles", response_class=HTMLResponse)
    async def nursing_test_roles(request: _Request):
        return TEMPLATES.TemplateResponse(
            request,
            "nursing/test-roles.html",
            {"active": "test"},
        )

    @app.get("/api/nursing/alerts")
    async def nursing_alerts():
        """Return pending (unhandled) health alerts for the dashboard."""
        async with db.conn(user_id=None, role="system") as conn:
            rows = await conn.execute(
                "SELECT id, resident_id, content, category, severity, "
                "created_at, handled "
                "FROM nursing_health_alerts "
                "WHERE handled = FALSE "
                "ORDER BY created_at DESC "
                "LIMIT 50"
            )
            alerts = []
            for row in await rows.fetchall():
                alerts.append(
                    {
                        "id": row[0],
                        "resident_id": row[1],
                        "content": row[2],
                        "category": row[3],
                        "severity": row[4],
                        "created_at": str(row[5]),
                        "handled": row[6],
                    }
                )
        return {"alerts": alerts}

    @app.exception_handler(MustRotatePasswordError)
    async def _rotate_handler(_request, exc: MustRotatePasswordError):
        return JSONResponse(status_code=423, content={"detail": str(exc)})

    @app.exception_handler(StarletteHTTPException)
    async def _http_handler(request, exc: StarletteHTTPException):
        if exc.status_code in (302, 303):
            location = "/login"
            if exc.headers and "location" in exc.headers:
                location = exc.headers["location"]
            return StarletteRedirect(url=location, status_code=exc.status_code)
        return await http_exception_handler(request, exc)

    async def shutdown() -> None:
        await docker.close()
        await db.close()
        await redis.aclose()

    shutdown_event = asyncio.Event()

    # P4: audit mirror reconciler — drains audit_log_outbox into per-agent DBs.
    from dl_control.audit.audit_mirror import audit_mirror_loop

    mirror_lock_fd = -1
    mirror_task = None
    owner_dsn = s.owner_dsn.get_secret_value() if s.owner_dsn else None
    if owner_dsn:
        try:
            mirror_lock_path = str(agents_root / ".dato-audit-mirror.lock")
            mirror_lock_fd = os.open(mirror_lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            fcntl.flock(mirror_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            if mirror_lock_fd >= 0:
                os.close(mirror_lock_fd)
            mirror_lock_fd = -1
            logging.getLogger(__name__).warning(
                "Could not acquire audit-mirror lock; mirror task skipped"
            )
        else:
            mirror_task = asyncio.create_task(
                audit_mirror_loop(
                    db,
                    owner_dsn,
                    shutdown_event,
                    poll_seconds=s.audit_mirror_poll_seconds,
                )
            )

    # P13c: register shipped flows (spec §9) — disabled by default; the admin
    # enables them in the UI. A failure (e.g. FlowVersionConflict — a vendor
    # packaging bug) must not brick the appliance (D-P13C-11): log + continue;
    # runs pinned to a missing version already fail loudly in the runner.
    from dl_control.workflows.flows.catalog import SHIPPED_FLOWS
    from dl_control.workflows.registry import register_flows

    try:
        async with db.conn(user_id=None, role="system") as conn:
            await register_flows(conn, SHIPPED_FLOWS)
    except Exception as exc:  # noqa: BLE001
        structlog.get_logger().error("workflow_flow_registration_failed", error=str(exc))

    # P13b: workflow runner — single-writer leased loop (workflow spec §6.2).
    # Postgres is the authoritative lease; this flock only prevents a second
    # dl-control process from running a competing loop on the same box.
    from dl_control.workflows.runner import runner_loop

    workflow_lock_fd = -1
    workflow_task = None
    workflow_scheduler_task = None
    workflow_listener_task = None
    try:
        workflow_lock_path = str(agents_root / ".dato-workflow.lock")
        workflow_lock_fd = os.open(workflow_lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(workflow_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        if workflow_lock_fd >= 0:
            os.close(workflow_lock_fd)
        workflow_lock_fd = -1
        structlog.get_logger().warning("could not acquire workflow-runner lock; runner skipped")
    else:
        from dl_control.workflows.schedules import scheduler_loop
        from dl_control.workflows.wake import wake_listener

        workflow_wake_event = asyncio.Event()
        workflow_listener_task = asyncio.create_task(
            wake_listener(redis, workflow_wake_event, shutdown_event)
        )
        workflow_scheduler_task = asyncio.create_task(
            scheduler_loop(
                db,
                shutdown_event,
                tick_seconds=s.workflow_schedule_tick_seconds,
            )
        )
        from dl_control.workflows.dispatch import DispatchConfig

        workflow_dispatch_cfg = DispatchConfig(
            agents_root=s.agents_root,
            receiver_port=s.workflow_agent_receiver_port,
            http_timeout_seconds=s.workflow_agent_dispatch_timeout_seconds,
            repost_backoff_seconds=s.workflow_agent_repost_backoff_seconds,
            repost_max=s.workflow_agent_repost_max,
        )
        workflow_task = asyncio.create_task(
            runner_loop(
                db,
                shutdown_event,
                worker=f"dl-control-{os.getpid()}",
                lease_ttl_seconds=s.workflow_lease_ttl_seconds,
                poll_seconds=s.workflow_poll_seconds,
                wake_event=workflow_wake_event,
                dispatch_cfg=workflow_dispatch_cfg,
            )
        )

    # P6 — re-render Tier 1 configs if templates have changed since the
    # last boot. CURRENT_TEMPLATE_VERSION is bumped in
    # dl_control/agents/reprovision.py whenever openclaw.json.j2 changes.
    try:
        from dl_control.agents.provisioning.service import ProvisioningConfig
        from dl_control.agents.reprovision import reprovision_tier1_agents

        p6_cfg = ProvisioningConfig.from_settings(s)
        p6_summary = await reprovision_tier1_agents(
            db=db,
            docker=docker,
            cfg=p6_cfg,
            reason="startup_template_check",
        )
        structlog.get_logger().info(
            "p6_startup_reprovision",
            reprovisioned=len(p6_summary["reprovisioned"]),
            skipped=len(p6_summary["skipped"]),
            failed=len(p6_summary["failed"]),
        )
    except Exception as exc:
        structlog.get_logger().error(
            "p6_startup_reprovision_error",
            error=str(exc),
        )


    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        yield
        shutdown_event.set()
        tasks = [
            t
            for t in (
                mirror_task,
                workflow_task,
                workflow_scheduler_task,
                workflow_listener_task,
                _app.state.active_agents_reconcile_task,
            )
            if t is not None
        ]
        await asyncio.gather(
            *tasks,
            return_exceptions=True,
        )
        if workflow_lock_fd >= 0:
            os.close(workflow_lock_fd)
        if mirror_lock_fd >= 0:
            os.close(mirror_lock_fd)
        await shutdown()

    app.router.lifespan_context = _lifespan
    app.state.shutdown = shutdown
    app.state.prov_cfg = prov_cfg
    app.state.docker = docker
    app.state.settings = s
    app.state.workflow_runner_task = workflow_task

    # P3: GBrain OAuth credential wizard
    from dl_control.agents.gbrain import routes as gbrain_creds_routes

    app.include_router(
        gbrain_creds_routes.make_router(
            db=db,
            sessions=sessions,
            settings=s,
            templates=TEMPLATES,
        )
    )

    # P5: agent token verify endpoint (internal API for dl-cognee).
    from dl_control.libraries.routes import make_library_router, make_verify_router

    app.include_router(make_verify_router(db=db, settings=s))
    app.include_router(make_library_router(db=db, sessions=sessions, settings=s))

    # P6: internal audit write path for dl-llm-proxy.
    from dl_control.audit.internal_routes import make_internal_audit_router

    app.include_router(make_internal_audit_router(db, s))

    # P6: internal llm-status endpoint for dashboard widget.
    from dl_control.llm.routes import make_llm_status_router

    app.include_router(make_llm_status_router(db, s))

    # P13c: workflow event-intake (internal) endpoint.
    from dl_control.workflows.internal_routes import make_workflow_internal_router

    app.include_router(make_workflow_internal_router(db, redis, s))

    # P13d: agent-facing workflow API — result callback + start/get (spec §7).
    from dl_control.workflows.agent_routes import make_agent_router

    app.include_router(make_agent_router(db, redis))

    # P13d+: admin internal API — Agent Manager system management (spec §8.5).
    from dl_control.agents.internal_routes import make_admin_internal_router

    app.include_router(make_admin_internal_router(db=db, docker=docker, cfg=prov_cfg, redis=redis))

    # P13c: workflow admin UI (list/detail, enable, schedules, approvals, controls).
    from dl_control.workflows import admin_routes as workflow_admin_routes

    app.include_router(
        workflow_admin_routes.make_router(
            db=db,
            sessions=sessions,
            templates=TEMPLATES,
            settings=s,
            redis=redis,
        )
    )
    return app


class LazyApp:
    """ASGI entrypoint that defers I/O until the first request."""

    def __init__(self) -> None:
        self._app: FastAPI | None = None
        self._lock = asyncio.Lock()

    async def __call__(self, scope, receive, send):
        if self._app is None:
            async with self._lock:
                if self._app is None:
                    self._app = await build_app()
        await self._app(scope, receive, send)


app = LazyApp()
