"""dl-cognee FastAPI app factory (lazy init — defers I/O until first request)."""

from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import Response


async def build_app() -> FastAPI:
    from dl_cognee.settings import load_settings

    s = load_settings()

    from dl_cognee.embedder import Embedder

    embedder = Embedder(model_name=s.embedding_model)

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        from dl_cognee.startup import warm_up

        await warm_up(app, s, embedder)
        yield
        # Shutdown: close owner pool and control client.
        if hasattr(app.state, "owner_pool"):
            await app.state.owner_pool.close()
        if hasattr(app.state, "control_client"):
            await app.state.control_client.aclose()
        # Close all isolated pools.
        if hasattr(app.state, "iso_pools"):
            for pool in app.state.iso_pools.values():
                with contextlib.suppress(Exception):
                    await pool.close()

    app = FastAPI(lifespan=_lifespan)

    @app.get("/health")
    async def health():
        if not getattr(app.state, "ready", False):
            return {"status": "starting"}
        return {"status": "ok"}

    @app.get("/metrics")
    async def metrics():
        from prometheus_client import generate_latest

        return Response(content=generate_latest(), media_type="text/plain")

    from dl_cognee.routes import make_router

    app.include_router(make_router())

    return app


class LazyApp:
    """ASGI entrypoint that defers DB connections + FlagEmbedding bge-m3 warm-up
    until the first request arrives (same pattern as dl-control).

    Handles lifespan events by delegating them to the inner FastAPI app
    (which runs the startup/shutdown handlers registered via lifespan=)."""

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
