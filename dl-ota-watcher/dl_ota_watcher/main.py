"""P7 — dl-ota-watcher entry point."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import aiodocker
import asyncpg
import structlog
from aiohttp import web
from redis.asyncio import Redis

from dl_ota_watcher.settings import load_settings
from dl_ota_watcher.state_machine import (
    _maybe_bootstrap_from_bundle,
    resume_from_journal,
    run_poll,
)
from dl_ota_watcher.state_store import OtaState, load_state
from dl_ota_watcher.web import create_app, publish_status, start_redis_listener

logger = structlog.get_logger()


@dataclass
class State:
    current_state: str = "idle"
    last_check_at: str | None = None
    available_version: str | None = None
    last_update_status: str | None = None


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _poll_loop(
    docker: aiodocker.Docker,
    settings,
    journal_path: Path,
    ota_state: OtaState,
    ota_state_path: Path,
    pool: asyncpg.Pool,
    redis: Redis,
    trigger_queue: asyncio.Queue,
    state: State,
) -> None:
    poll_interval = settings.poll_interval_seconds
    while True:
        try:
            trigger = None
            try:
                trigger = trigger_queue.get_nowait()
                logger.info("manual_trigger", force=trigger.get("force", False))
            except asyncio.QueueEmpty:
                pass

            if trigger or state.current_state == "idle":
                try:
                    state.current_state = "checking"
                    state.last_check_at = _utcnow_iso()
                    await run_poll(
                        docker=docker,
                        settings=settings,
                        journal_path=journal_path,
                        ota_state=ota_state,
                        ota_state_path=ota_state_path,
                        pool=pool,
                        redis_client=redis,
                    )
                    state.current_state = "idle"
                except Exception as exc:
                    logger.error("poll_cycle_error", error=str(exc))
                    state.current_state = "idle"

            await publish_status(redis, state, ota_state)
            await asyncio.sleep(poll_interval)
        except asyncio.CancelledError:
            break


def main() -> None:
    s = load_settings()
    data_root = Path(s.data_root)
    data_root.mkdir(parents=True, exist_ok=True)
    ota_state_path = data_root / "ota_state.json"
    journal_path = data_root / "ota_journal.json"
    ota_state = load_state(ota_state_path)
    state = State()

    async def _run() -> None:
        docker = aiodocker.Docker(url=s.docker_host)
        pool = await asyncpg.create_pool(
            s.app_dsn.get_secret_value(),
            min_size=1,
            max_size=3,
        )
        assert pool is not None
        redis = Redis.from_url(s.redis_url.get_secret_value(), decode_responses=True)
        try:
            trigger_queue: asyncio.Queue = asyncio.Queue()

            await resume_from_journal(
                docker=docker,
                settings=s,
                journal_path=str(journal_path),
                ota_state=ota_state,
                ota_state_path=str(ota_state_path),
                pool=pool,
                redis_client=redis,
            )

            await _maybe_bootstrap_from_bundle(
                state=ota_state,
                state_path=ota_state_path,
                minisign_pubkey=s.minisign_pubkey,
            )

            app = create_app(
                state=state,
                ota_state=ota_state,
                ota_state_path=ota_state_path,
                journal_path=journal_path,
                redis=redis,
                data_root=data_root,
                internal_api_key=(
                    s.internal_api_key.get_secret_value() if s.internal_api_key else ""
                ),
            )
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "0.0.0.0", 8080)
            await site.start()
            logger.info("web_server_started", host="0.0.0.0", port=8080)

            listener_task = asyncio.create_task(
                start_redis_listener(redis, ota_state, str(ota_state_path), trigger_queue)
            )
            poll_task = asyncio.create_task(
                _poll_loop(
                    docker,
                    s,
                    journal_path,
                    ota_state,
                    ota_state_path,
                    pool,
                    redis,
                    trigger_queue,
                    state,
                )
            )

            done, pending = await asyncio.wait(
                [listener_task, poll_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
        finally:
            await pool.close()
            await redis.aclose()
            await docker.close()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
