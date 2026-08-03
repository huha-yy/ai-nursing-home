"""FastAPI app factory with lazy ASGI entrypoint (LazyApp pattern)."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from dl_ocr.settings import load_settings

logger = logging.getLogger(__name__)


async def build_app() -> FastAPI:
    """Fully-wired FastAPI app.  Heavy init (model loading) happens in the
    lifespan warmup, NOT here — the container starts fast."""
    s = load_settings()

    app = FastAPI()

    # Deferred imports so the module is importable before the venv is ready.
    from dl_ocr.routes import make_router

    app.include_router(make_router())

    from dl_ocr.startup import warm_up

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        await warm_up(app, s)
        yield

    app.router.lifespan_context = _lifespan

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
