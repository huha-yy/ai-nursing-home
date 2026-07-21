"""dl-cognee startup — DB connections, bge-m3 warm-up."""

from __future__ import annotations

import logging

from fastapi import FastAPI

from dl_cognee.settings import Settings

logger = logging.getLogger(__name__)


async def warm_up(app: FastAPI, settings: Settings, embedder) -> None:
    # Store settings for route access.
    app.state.settings = settings

    # Open owner DB pool.
    import psycopg_pool

    app.state.owner_pool = psycopg_pool.AsyncConnectionPool(
        settings.owner_db_dsn.get_secret_value(),
        min_size=1,
        max_size=10,
    )
    # Probe.
    async with app.state.owner_pool.connection() as conn:
        await conn.execute("SELECT 1")

    # Model is loaded from Docker named volume at HF_HOME/hub.
    # bge-m3 is ~2.2GB, loaded via FlagEmbedding at embedder.warm_up().
    # No longer pre-baked in the image.

    # Warm bge-m3 embedder.
    await embedder.warm_up()
    app.state.embedder = embedder

    # Create httpx client for dl-control.
    import httpx

    app.state.control_client = httpx.AsyncClient(
        base_url=settings.dl_control_url,
        timeout=httpx.Timeout(10.0),
    )

    # Isolated DB pool LRU.
    app.state.iso_pools: dict[str, any] = {}

    # Verify cache.
    app.state.verify_cache: dict[bytes, tuple[dict, float]] = {}
    app.state.verify_cache_negative: dict[bytes, float] = {}

    app.state.ready = True
    logger.info("dl-cognee ready")
