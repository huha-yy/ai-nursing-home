"""P7 — aiohttp web app for the watcher (healthz + admin + Redis listener)."""

from __future__ import annotations

import asyncio
import json
import secrets
from pathlib import Path

import structlog
from aiohttp import web
from redis.asyncio import Redis
from redis.exceptions import TimeoutError as RedisTimeoutError

from dl_ota_watcher.state_store import OtaState, save_state

logger = structlog.get_logger()


def create_app(
    state,  # the State object holding current state info
    ota_state,  # OtaState dataclass from state_store
    ota_state_path,  # Path to ota_state.json
    journal_path,  # Path to ota_journal.json
    redis,  # redis.asyncio.Redis connection
    data_root,  # Path to /app/data
    internal_api_key: str = "",  # P7: protect /admin/clear-journal
) -> web.Application:
    app = web.Application()

    async def healthz(request: web.Request) -> web.Response:
        """Return current watcher state as JSON."""
        return web.json_response(
            {
                "status": "ok",
                "current_state": state.current_state,
                "current_version": ota_state.current_version,
            }
        )

    async def clear_journal(request: web.Request) -> web.Response:
        """DELETE the journal file (operator recovery)."""
        auth = request.headers.get("Authorization", "")
        if internal_api_key and not secrets.compare_digest(auth, f"Bearer {internal_api_key}"):
            raise web.HTTPUnauthorized(text="missing or invalid bearer token")
        try:
            jp = Path(journal_path)
            if jp.exists():
                jp.unlink()
                logger.info("journal_cleared", path=str(jp))
            return web.json_response({"cleared": True})
        except Exception as exc:
            logger.error("clear_journal_failed", error=str(exc))
            return web.json_response({"cleared": False, "error": str(exc)}, status=500)

    app.router.add_get("/healthz", healthz)
    app.router.add_post("/admin/clear-journal", clear_journal)
    return app


async def start_redis_listener(
    redis: Redis,
    ota_state: OtaState,
    ota_state_path: str | Path,
    trigger_queue: asyncio.Queue,
) -> None:
    """Subscribe to dato:ota:trigger and dato:ota:clear-suppression.

    Messages on trigger are pushed into the asyncio Queue for the poll loop.
    Messages on clear-suppression clear the suppression marker in ota_state.
    """
    pubsub = redis.pubsub()
    await pubsub.subscribe("dato:ota:trigger", "dato:ota:clear-suppression")
    try:
        while True:
            try:
                async for msg in pubsub.listen():
                    if msg["type"] != "message":
                        continue
                    channel = (
                        msg["channel"].decode()
                        if isinstance(msg["channel"], bytes)
                        else msg["channel"]
                    )
                    data = msg["data"].decode() if isinstance(msg["data"], bytes) else msg["data"]
                    if channel == "dato:ota:trigger":
                        await trigger_queue.put(json.loads(data))
                        logger.info("ota_trigger_received", data=json.loads(data))
                    elif channel == "dato:ota:clear-suppression":
                        payload = json.loads(data)
                        digest = payload.get("target_digest")
                        if digest:
                            ota_state.clear_self_swap_suppression(digest)
                            save_state(ota_state, Path(ota_state_path))
                            logger.info("suppression_cleared", digest=digest)
            except (asyncio.TimeoutError, TimeoutError, RedisTimeoutError):
                logger.warning("redis_listener_timeout — reconnecting")
                await asyncio.sleep(1)
                try:
                    await pubsub.unsubscribe("dato:ota:trigger", "dato:ota:clear-suppression")
                except Exception:
                    pass
                pubsub = redis.pubsub()
                await pubsub.subscribe("dato:ota:trigger", "dato:ota:clear-suppression")
    except asyncio.CancelledError:
        pass
    finally:
        await pubsub.unsubscribe("dato:ota:trigger", "dato:ota:clear-suppression")


async def publish_status(
    redis: Redis,
    state,
    ota_state: OtaState,
) -> None:
    """Publish the current watcher state to the Redis dashboard key."""
    payload = {
        "current_version": ota_state.current_version,
        "current_state": state.current_state,
        "last_check_at": state.last_check_at,
        "available_version": state.available_version,
        "last_update_status": state.last_update_status,
    }
    await redis.set("dato:ota:status", json.dumps(payload))
