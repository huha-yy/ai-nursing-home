"""Redis wake nudge (spec §6.2, D-P13C-9).

Latency optimization only: routes publish after creating runnable work; the
listener sets an in-process event the runner's idle wait also waits on.
Both sides tolerate Redis being down — the loop's polling is the
correctness baseline ("losing Redis degrades latency, never correctness").
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

logger = logging.getLogger(__name__)

WAKE_CHANNEL = "dato:workflow:wake"
_RECONNECT_SECONDS = 2.0


async def publish_wake(redis, *, reason: str) -> None:
    """Fire-and-forget nudge. Never raises."""
    try:
        await redis.publish(WAKE_CHANNEL, reason)
    except Exception:  # noqa: BLE001 — latency only, never correctness
        logger.debug("workflow wake publish failed (reason=%s)", reason, exc_info=True)


async def wake_listener(
    redis,
    wake_event: asyncio.Event,
    shutdown_event: asyncio.Event,
) -> None:
    """Subscribe to WAKE_CHANNEL and set wake_event per message. Reconnects
    forever with backoff; never raises; drains promptly on shutdown."""
    while not shutdown_event.is_set():
        pubsub = None
        try:
            pubsub = redis.pubsub()
            await pubsub.subscribe(WAKE_CHANNEL)
            while not shutdown_event.is_set():
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg is not None:
                    wake_event.set()
        except Exception:  # noqa: BLE001 — reconnect, never crash
            if shutdown_event.is_set():
                break
            logger.warning("workflow wake listener error; reconnecting", exc_info=True)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(shutdown_event.wait(), timeout=_RECONNECT_SECONDS)
        finally:
            if pubsub is not None:
                with contextlib.suppress(Exception):
                    await pubsub.aclose()
    logger.info("workflow wake listener stopped")
