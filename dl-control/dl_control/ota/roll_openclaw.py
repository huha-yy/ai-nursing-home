"""P7 — per-agent OpenClaw rolling restart background task.

Owned by dl-control (D-P7-2, D-P7-7). The watcher POSTs to
/api/internal/ota/roll-openclaw; this module manages the Redis-backed
job lifecycle and the Engine API loop.
"""

from __future__ import annotations

import json
import time
from typing import Any

from dl_control.audit.service import write_event
from dl_control.db import Database

_JOB_KEY_PREFIX = "dato:ota:roll-jobs:"
_RESULT_CHANNEL = "dato:ota:roll-result"


def _job_key(job_id: str) -> str:
    return f"{_JOB_KEY_PREFIX}{job_id}"


async def get_job(redis, job_id: str) -> dict | None:
    raw = await redis.get(_job_key(job_id))
    if raw is None:
        return None
    return json.loads(raw)


async def list_in_progress_jobs(redis) -> list[dict]:
    result: list[dict] = []
    cursor = 0
    pattern = f"{_JOB_KEY_PREFIX}*"
    while True:
        cursor, keys = await redis.scan(cursor, match=pattern, count=100)
        for k in keys:
            raw = await redis.get(k)
            if raw:
                doc = json.loads(raw)
                if doc.get("status") == "in_progress":
                    result.append(doc)
        if cursor == 0:
            break
    return result


async def start_job(
    redis,
    *,
    job_id: str,
    ota_version: str,
    target_digest: str,
    mode: str,
    ttl: int,
) -> dict:
    """Idempotent insert — returns existing if already present."""
    existing = await get_job(redis, job_id)
    if existing is not None:
        return existing
    state: dict = {
        "job_id": job_id,
        "status": "in_progress",
        "ota_version": ota_version,
        "target_digest": target_digest,
        "mode": mode,
        "accepted_at": time.time(),
        "completed": [],
        "failed_agent": None,
        "failed_rollback_agent": None,
        "started_at": None,
        "finished_at": None,
    }
    await redis.set(_job_key(job_id), json.dumps(state), ex=ttl)
    return state


async def _put_job(redis, job_id: str, state: dict, ttl: int) -> None:
    await redis.set(_job_key(job_id), json.dumps(state), ex=ttl)


async def run_loop(
    *,
    redis,
    db: Database,
    docker: Any,
    settings: Any,
    job_id: str,
    target_digest: str,
    mode: str,
    ttl: int,
    health_window_seconds: int,
) -> None:
    state = await get_job(redis, job_id)
    if state is None:
        return
    state["started_at"] = time.time()
    await _put_job(redis, job_id, state, ttl)

    async def audit_hook(action: str, target: str, meta: dict) -> None:
        async with db.conn(user_id=None, role="system") as conn:
            await write_event(
                conn,
                actor_user_id=None,
                action=action,
                target=target,
                meta=meta,
            )

    # --- snapshot prev_digests or reattach ---
    prev_digests: dict[str, str] = {}
    completed: list[str] = []
    representative_prev_digest: str | None = None

    if state.get("prev_digests") is not None:
        prev_digests = state["prev_digests"]
        representative_prev_digest = state["representative_prev_digest"]
        completed = state.get("completed", [])
    else:
        async with db.conn(user_id=None, role="system") as conn:
            cur = await conn.execute(
                "SELECT id, status, tier, container_id, current_openclaw_digest "
                "FROM agents WHERE status IN ('active', 'error') ORDER BY created_at"
            )
            agents = await cur.fetchall()
        for agent_row in agents:
            agent_id = str(agent_row[0])
            db_digest = agent_row[4]

            # P10: DB column is source of truth for docker-loaded images.
            if db_digest:
                prev_digests[agent_id] = db_digest
                continue

            container_name = f"dato-agent-{agent_id}"
            try:
                info = await docker.inspect_container(
                    audit=audit_hook,
                    name=container_name,
                )
                if info is not None:
                    image_info = await docker._docker.images.inspect(info["Image"])
                    repo_digests = image_info.get("RepoDigests") or []
                    if repo_digests:
                        _, _, digest = repo_digests[0].partition("@")
                        prev_digests[agent_id] = digest
                    else:
                        state["status"] = "rollback_failed"
                        state["failed_agent"] = agent_id
                        state["finished_at"] = time.time()
                        state["error"] = "no_digest_available"
                        await _put_job(redis, job_id, state, ttl)
                        await redis.publish(_RESULT_CHANNEL, json.dumps(state))
                        return
            except Exception as exc:
                state["status"] = "rollback_failed"
                state["failed_agent"] = agent_id
                state["finished_at"] = time.time()
                state["error"] = f"inspect_failed: {exc}"
                await _put_job(redis, job_id, state, ttl)
                await redis.publish(_RESULT_CHANNEL, json.dumps(state))
                return
        if prev_digests:
            representative_prev_digest = next(iter(prev_digests.values()))
        state["prev_digests"] = prev_digests
        state["representative_prev_digest"] = representative_prev_digest
        state["completed"] = completed
        await _put_job(redis, job_id, state, ttl)

    async with db.conn(user_id=None, role="system") as conn:
        for agent_id in prev_digests:
            if agent_id in completed:
                continue
            agent_id_str = str(agent_id)
            container_name = f"dato-agent-{agent_id_str}"
            cur = await conn.execute("SELECT tier FROM agents WHERE id = %s", (agent_id_str,))
            row = await cur.fetchone()
            tier = row[0] if row else "tier0"
            image = f"dato-openclaw@{target_digest}"

            try:
                new_cid = await docker.recreate_container(
                    audit=audit_hook,
                    name=container_name,
                    image=image,
                    host_agent_dir=f"{settings.host_agents_root}/{agent_id_str}",
                    agent_id=agent_id_str,
                    tier=tier,
                )
                await conn.execute(
                    "UPDATE agents SET container_id = %s WHERE id = %s",
                    (new_cid, agent_id_str),
                )
                healthy = await docker.wait_for_health(
                    audit=audit_hook,
                    name=container_name,
                    timeout_s=health_window_seconds,
                )
                if mode == "apply":
                    if not healthy:
                        state["failed_agent"] = agent_id_str
                        state["status"] = "rolled_back"
                        # Roll back completed agents
                        rollback_failed: str | None = None
                        for done_id in reversed(completed):
                            prev_digest = prev_digests.get(done_id)
                            if not prev_digest:
                                rollback_failed = done_id
                                break
                            done_name = f"dato-agent-{done_id}"
                            cur2 = await conn.execute(
                                "SELECT tier FROM agents WHERE id = %s", (done_id,)
                            )
                            row2 = await cur2.fetchone()
                            done_tier = row2[0] if row2 else "tier0"
                            try:
                                r_cid = await docker.recreate_container(
                                    audit=audit_hook,
                                    name=done_name,
                                    image=f"dato-openclaw@{prev_digest}",
                                    host_agent_dir=f"{settings.host_agents_root}/{done_id}",
                                    agent_id=done_id,
                                    tier=done_tier,
                                )
                                await conn.execute(
                                    "UPDATE agents SET container_id = %s WHERE id = %s",
                                    (r_cid, done_id),
                                )
                                ok = await docker.wait_for_health(
                                    audit=audit_hook,
                                    name=done_name,
                                    timeout_s=health_window_seconds,
                                )
                                if not ok:
                                    rollback_failed = done_id
                                    break
                            except Exception:
                                rollback_failed = done_id
                                break
                        if rollback_failed is not None:
                            state["status"] = "rollback_failed"
                            state["failed_rollback_agent"] = rollback_failed
                        state["finished_at"] = time.time()
                        await _put_job(redis, job_id, state, ttl)
                        await redis.publish(_RESULT_CHANNEL, json.dumps(state))
                        return
                elif mode == "rollback" and not healthy:
                    state["status"] = "rollback_failed"
                    state["failed_agent"] = agent_id_str
                    state["finished_at"] = time.time()
                    await _put_job(redis, job_id, state, ttl)
                    await redis.publish(_RESULT_CHANNEL, json.dumps(state))
                    return
                completed.append(agent_id_str)
                state["completed"] = completed
                await _put_job(redis, job_id, state, ttl)
                await audit_hook(
                    "ota.openclaw_rolled",
                    agent_id_str,
                    {
                        "from": prev_digests.get(agent_id_str),
                        "to": target_digest,
                        "mode": mode,
                        "job_id": job_id,
                    },
                )
            except Exception as exc:
                state["status"] = "rollback_failed"
                state["failed_agent"] = agent_id_str
                state["finished_at"] = time.time()
                state["error"] = str(exc)
                await _put_job(redis, job_id, state, ttl)
                await redis.publish(_RESULT_CHANNEL, json.dumps(state))
                return

    # Commit registry ONLY on full success
    async with db.conn(user_id=None, role="system") as conn:
        for agent_id in completed:
            await conn.execute(
                "UPDATE agents SET current_openclaw_digest = %s WHERE id = %s",
                (target_digest, agent_id),
            )

    state["status"] = "committed"
    state["finished_at"] = time.time()
    state["representative_prev_digest"] = representative_prev_digest
    await _put_job(redis, job_id, state, ttl)
    await redis.publish(_RESULT_CHANNEL, json.dumps(state))
