"""Reconciler — single-writer projection of pairings → allowFrom files (spec §6)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

logger = logging.getLogger(__name__)

# Subdir under <agents_root>/<agent_id> where OpenClaw's pairing-store keeps
# feishu-pairing.json and feishu-<account>-allowFrom.json. It is bind-mounted
# at the agent's ~/.openclaw/credentials. The writer (reconcile_pairings) and
# the drift scanner (periodic_dirty_scan) MUST agree on this path, or every
# scan re-enqueues forever — keep them sharing this constant.
_OPENCLAW_CREDS_SUBDIR = "credentials"


@dataclass
class ReconcilerState:
    """Single in-process truth for queue/retry coordination (spec §6.2).

    Per-agent state: idle | queued | in_flight | retry_scheduled.
    The state-lock guarantees atomicity of enqueue / mark_* transitions.
    """

    attempts: dict[UUID, int] = field(default_factory=dict)
    in_flight: set[UUID] = field(default_factory=set)
    queued: set[UUID] = field(default_factory=set)
    scheduled_retries: dict[UUID, asyncio.Task] = field(default_factory=dict)
    queue: asyncio.Queue[UUID] = field(default_factory=asyncio.Queue)
    _state_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def enqueue(self, agent_id: UUID) -> None:
        """Add agent_id to queue if not already inflight/queued/retrying."""
        async with self._state_lock:
            if agent_id in self.in_flight or agent_id in self.queued:
                return
            if agent_id in self.scheduled_retries:
                return
            self.queued.add(agent_id)
            await self.queue.put(agent_id)

    async def mark_started(self, agent_id: UUID) -> int:
        """Transition queued → in_flight; return the attempt number (1-based)."""
        async with self._state_lock:
            self.queued.discard(agent_id)
            self.in_flight.add(agent_id)
            attempt = self.attempts.get(agent_id, 0) + 1
            self.attempts[agent_id] = attempt
            return attempt

    async def mark_success(self, agent_id: UUID) -> None:
        """Clear in_flight and attempt counter."""
        async with self._state_lock:
            self.in_flight.discard(agent_id)
            self.attempts.pop(agent_id, None)

    async def mark_failure_and_schedule_retry(
        self,
        agent_id: UUID,
        delay: float,
        shutdown_event: asyncio.Event,
    ) -> None:
        """Schedule ONE delayed-requeue task. No-op if already scheduled."""
        async with self._state_lock:
            self.in_flight.discard(agent_id)
            if agent_id in self.scheduled_retries:
                return
            task = asyncio.create_task(self._delayed_requeue(agent_id, delay, shutdown_event))
            self.scheduled_retries[agent_id] = task

    async def _delayed_requeue(
        self,
        agent_id: UUID,
        delay: float,
        shutdown_event: asyncio.Event,
    ) -> None:
        """Wait delay seconds (or until shutdown), then atomically
        transition retry_scheduled → queued."""
        try:
            _, pending = await asyncio.wait(
                [asyncio.create_task(shutdown_event.wait())],
                timeout=delay,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if pending:
                for t in pending:
                    t.cancel()
            if shutdown_event.is_set():
                async with self._state_lock:
                    if self.scheduled_retries.get(agent_id) is asyncio.current_task():
                        del self.scheduled_retries[agent_id]
                return

            async with self._state_lock:
                if self.scheduled_retries.get(agent_id) is asyncio.current_task():
                    del self.scheduled_retries[agent_id]
                    self.queued.add(agent_id)
                    await self.queue.put(agent_id)
        except asyncio.CancelledError:
            pass


class StaleReconcileError(Exception):
    """CAS failure — another mutation landed during reconcile."""


async def reconcile_pairings(
    db,
    agent_id: UUID,
    state: ReconcilerState,
    agents_root: str,
) -> None:
    """Single writer of the allowFrom projection (spec §6.4)."""
    success = False
    try:
        async with db.conn(user_id=None, role="system") as conn:
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                (f"feishu_pairing:{agent_id}",),
            )
            cur = await conn.execute(
                "SELECT pairing_version, "
                "       channel_config -> 'feishu' ->> 'account_id' "
                "FROM agents WHERE id = %s FOR UPDATE",
                (str(agent_id),),
            )
            row = await cur.fetchone()
            if row is None:
                success = True
                return
            captured_version, account_id = row
            if not account_id:
                success = True
                return

            cur = await conn.execute(
                "SELECT sender_id_normalized FROM pairings "
                "WHERE agent_id = %s AND account_id = %s AND status = 'approved' "
                "ORDER BY approved_at",
                (str(agent_id), account_id),
            )
            entries = [r[0] for r in await cur.fetchall()]
            content = json.dumps({"version": 1, "allowFrom": entries}, separators=(",", ":"))
            content_hash = hashlib.sha256(content.encode()).digest()

            _do_write_allowfrom(agents_root, str(agent_id), account_id, content)

            cas = await conn.execute(
                "UPDATE agents SET last_projection_version = %s, "
                "    last_projection_hash = %s "
                "WHERE id = %s AND pairing_version = %s",
                (captured_version, content_hash, str(agent_id), captured_version),
            )
            if cas.rowcount != 1:
                raise StaleReconcileError(str(agent_id), captured_version)
        success = True
    finally:
        if success:
            await state.mark_success(agent_id)


def _do_write_allowfrom(
    agents_root: str,
    agent_id: str,
    account_id: str,
    content: str,
) -> None:
    """Resolve path + atomic write with containment check."""
    from dl_control.channels.feishu.allowfrom_writer import write_allowfrom_file_atomic
    from dl_control.channels.normalize import safe_account_key

    safe_account_key(account_id)  # defense-in-depth; raises ValueError on bad value

    agent_dir = Path(agents_root) / agent_id  # do NOT resolve
    # OpenClaw's pairing-store reads/writes allowFrom at
    # ~/.openclaw/credentials/feishu-<account>-allowFrom.json, which is
    # bind-mounted at <agents_root>/<agent_id>/credentials/ (NOT state/oauth).
    creds_rel = Path(_OPENCLAW_CREDS_SUBDIR)
    target_rel = creds_rel / f"feishu-{account_id}-allowFrom.json"

    # Lexical containment check — no symlink following.
    try:
        (agent_dir / target_rel).relative_to(agent_dir)
    except ValueError as exc:
        raise ValueError(f"Path containment failed: {target_rel} would escape {agent_dir}") from exc

    creds_dir = agent_dir / creds_rel
    creds_dir.mkdir(parents=True, exist_ok=True)

    write_allowfrom_file_atomic(
        content=content,
        target_dir=str(creds_dir),
        account_id=account_id,
        agent_dir=str(agent_dir),
    )


async def reconciler_loop(
    db,
    state: ReconcilerState,
    shutdown_event,
    agents_root: str,
) -> None:
    """Single drainer of state.queue (spec §6.3)."""
    while not shutdown_event.is_set():
        try:
            agent_id = await asyncio.wait_for(state.queue.get(), timeout=1.0)
        except TimeoutError:
            continue

        attempt = await state.mark_started(agent_id)
        try:
            await reconcile_pairings(db, agent_id, state, agents_root)
        except Exception:
            logger.exception("reconcile failed for %s (attempt %d)", agent_id, attempt)
            delay = min(2 ** (attempt - 1), 60)
            await state.mark_failure_and_schedule_retry(
                agent_id,
                delay,
                shutdown_event,
            )


async def periodic_dirty_scan(
    db,
    state: ReconcilerState,
    shutdown_event,
    agents_root: str,
) -> None:
    """Every 5 s, enqueue agents with dirty pairings (spec §6.3)."""
    while not shutdown_event.is_set():
        try:
            async with db.conn(user_id=None, role="system") as conn:
                cur = await conn.execute(
                    "SELECT id FROM agents WHERE pairing_version > last_projection_version"
                )
                async for row in cur:
                    await state.enqueue(row[0])

                cur = await conn.execute(
                    "SELECT id, "
                    "       channel_config -> 'feishu' ->> 'account_id' AS acct, "
                    "       last_projection_hash "
                    "FROM agents "
                    "WHERE feishu_configured = true "
                    "  AND pairing_version = last_projection_version"
                )
                async for row in cur:
                    agent_id: UUID = row[0]
                    aid_str = str(agent_id)
                    account_id = row[1]
                    stored_hash = row[2]
                    if not account_id:
                        continue
                    allowfrom_path = (
                        Path(agents_root)
                        / aid_str
                        / _OPENCLAW_CREDS_SUBDIR
                        / f"feishu-{account_id}-allowFrom.json"
                    )
                    try:
                        on_disk = allowfrom_path.read_bytes()
                        disk_hash = hashlib.sha256(on_disk).digest()
                        if disk_hash != stored_hash:
                            await state.enqueue(agent_id)
                    except FileNotFoundError:
                        if stored_hash is not None:
                            await state.enqueue(agent_id)
                    except OSError:
                        pass
        except Exception:
            logger.exception("periodic_dirty_scan tick failed")
        finally:
            await asyncio.sleep(5.0)
