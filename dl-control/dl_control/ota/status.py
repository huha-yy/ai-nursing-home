"""Redis-based OTA status read for the dashboard widget."""

from __future__ import annotations

import json

import structlog

logger = structlog.get_logger()


async def read_ota_status(redis) -> dict | None:
    """Read the watcher's published status from Redis.

    Returns None when the key is missing or unparseable (the
    watcher hasn't published yet or is restarting). Logs a warning
    on invalid JSON but does NOT raise — dashboard rendering must
    not break on a transient Redis glitch.
    """
    try:
        raw = await redis.get("dato:ota:status")
    except Exception:
        logger.warning("ota_status_read_failed", exc_info=True)
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("ota_status_decode_failed", raw=raw)
        return None
