"""P8 precreated agents — boot-time reconciler.

Walks seed dirs and materializes unsuppressed seeds per the decision
matrix.  Maintains an in-process SHA cache for drift computation.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from pathlib import Path

import psycopg
import structlog

from dl_control.agents import registry
from dl_control.audit.service import write_event
from dl_control.precreated import loader, suppressions
from dl_control.precreated.loader import canonical_seed_sha

logger = structlog.get_logger("dl_control")

_CURRENT_SEED_SHAS: dict[str, str] = {}


def get_current_seed_sha(precreated_id: str) -> str | None:
    return _CURRENT_SEED_SHAS.get(precreated_id)


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    provisioned: int = 0
    skipped_existing: int = 0
    skipped_suppressed: int = 0
    drift_detected: int = 0
    orphaned: int = 0
    lost_race: int = 0
    skipped_no_admin: bool = False


def _rebuild_cache(seeds_root: Path) -> list[loader.Seed]:
    _CURRENT_SEED_SHAS.clear()
    seeds = loader.discover_seeds(seeds_root)
    for seed in seeds:
        _CURRENT_SEED_SHAS[seed.id] = canonical_seed_sha(seed.raw_yaml)
    return seeds


async def _select_bootstrap_admin(db) -> str | None:
    async with db.conn(user_id=None, role="system") as conn:
        cur = await conn.execute(
            "SELECT id FROM users "
            "WHERE role = 'admin' AND status = 'active' "
            "ORDER BY created_at, id LIMIT 1"
        )
        row = await cur.fetchone()
    return str(row[0]) if row else None


async def _atomic_create(
    db,
    *,
    seed: loader.Seed,
    bootstrap_admin_id: str,
    current_sha: str,
) -> str | None:
    agent_id = uuid.uuid4()

    from dl_control.agents.schemas import AgentCreate

    req = AgentCreate(
        display_name=seed.display_name,
        tier=seed.tier,
        skill_list=seed.skill_list,
        channel_config=seed.channel_config,
        model_selection={
            "provider": seed.model_selection.get("provider"),
            "model": seed.model_selection.get("model"),
        },
    )

    try:
        async with db.conn(user_id=None, role="system") as conn:
            await registry.insert_agent(
                conn,
                agent_id=agent_id,
                req=req,
                precreated_id=seed.id,
                precreated_yaml_sha256=current_sha,
            )
            if not seed.admin_only:
                await conn.execute(
                    "INSERT INTO roles_on_agent (user_id, agent_id, role) VALUES (%s, %s, 'owner')",
                    (bootstrap_admin_id, str(agent_id)),
                )
            await write_event(
                conn,
                actor_user_id=None,
                action="agent_precreated_created",
                target=str(agent_id),
                meta={
                    "precreated_id": seed.id,
                    "display_name": seed.display_name,
                    "tier": seed.tier,
                    "admin_only": seed.admin_only,
                    "bootstrap_admin_id": (bootstrap_admin_id if not seed.admin_only else None),
                },
            )
        return str(agent_id)
    except psycopg.errors.UniqueViolation as exc:
        if exc.diag is not None and exc.diag.constraint_name == "agents_precreated_id_unique":
            return None
        raise


async def _provision_one(db, docker, cfg, agent_id: str, precreated_id: str) -> None:
    from dl_control.agents.provisioning.service import provision_agent

    try:
        await provision_agent(
            db,
            docker,
            cfg,
            actor_user_id=None,
            agent_id=agent_id,
        )
    except Exception:
        logger.exception(
            "precreated_provision_failed",
            precreated_id=precreated_id,
            agent_id=agent_id,
        )


async def reconcile_precreated(
    db,
    *,
    docker,
    cfg,
    seeds_root: Path | None = None,
) -> ReconcileResult:
    sr = seeds_root or Path(cfg.precreated_agents_root)

    # 1. Rebuild cache FIRST (before admin check)
    if sr.exists() and sr.is_dir():
        seeds = _rebuild_cache(sr)
    else:
        _CURRENT_SEED_SHAS.clear()
        seeds = []
        logger.warning("precreated_root_missing", root=str(sr))

    # 2. Bootstrap admin
    bootstrap_admin_id = await _select_bootstrap_admin(db)
    if bootstrap_admin_id is None:
        logger.info("reconciler_skip_no_admin")
        return ReconcileResult(skipped_no_admin=True)

    provision_tasks: list[tuple[str, str]] = []

    provisioned = 0
    skipped_existing = 0
    skipped_suppressed = 0
    drift_detected = 0
    lost_race = 0

    seed_ids: set[str] = set()

    # 3. Decision matrix per seed
    for seed in seeds:
        seed_ids.add(seed.id)

        async with db.conn(user_id=None, role="system") as conn:
            cur = await conn.execute(
                "SELECT id, precreated_yaml_sha256, status FROM agents WHERE precreated_id = %s",
                (seed.id,),
            )
            existing = await cur.fetchone()
            is_supp = await suppressions.is_suppressed(
                conn,
                precreated_id=seed.id,
            )

        if existing is None:
            if is_supp:
                skipped_suppressed += 1
            else:
                current_sha = canonical_seed_sha(seed.raw_yaml)
                agent_id = await _atomic_create(
                    db,
                    seed=seed,
                    bootstrap_admin_id=bootstrap_admin_id,
                    current_sha=current_sha,
                )
                if agent_id is None:
                    logger.warning(
                        "precreated_lost_race",
                        precreated_id=seed.id,
                    )
                    lost_race += 1
                else:
                    provisioned += 1
                    provision_tasks.append((agent_id, seed.id))
        else:
            stored_sha = existing[1]
            status = existing[2]
            current_sha = canonical_seed_sha(seed.raw_yaml)
            agent_id_str = str(existing[0])
            if stored_sha != current_sha:
                drift_detected += 1
                logger.info(
                    "precreated_drift_detected",
                    precreated_id=seed.id,
                    agent_id=agent_id_str,
                )
            elif status in ("error", "registered"):
                provisioned += 1
                provision_tasks.append((agent_id_str, seed.id))
            else:
                skipped_existing += 1

    # 4. Provision created agents (outside transactions)
    await asyncio.gather(
        *(_provision_one(db, docker, cfg, aid, pid) for aid, pid in provision_tasks),
        return_exceptions=True,
    )

    # 5. Orphan detection
    orphaned = 0
    if sr.exists() and sr.is_dir():
        async with db.conn(user_id=None, role="system") as conn:
            if not seed_ids:
                cur = await conn.execute(
                    "SELECT id, precreated_id FROM agents WHERE precreated_id IS NOT NULL"
                )
            else:
                cur = await conn.execute(
                    "SELECT id, precreated_id FROM agents "
                    "WHERE precreated_id IS NOT NULL "
                    "AND precreated_id != ALL(%s::text[])",
                    (list(seed_ids),),
                )
            for orphan_row in await cur.fetchall():
                orphaned += 1
                logger.warning(
                    "precreated_orphaned",
                    agent_id=str(orphan_row[0]),
                    precreated_id=orphan_row[1],
                )

    result = ReconcileResult(
        provisioned=provisioned,
        skipped_existing=skipped_existing,
        skipped_suppressed=skipped_suppressed,
        drift_detected=drift_detected,
        orphaned=orphaned,
        lost_race=lost_race,
        skipped_no_admin=False,
    )

    logger.info(
        "precreated_reconcile_done",
        provisioned=result.provisioned,
        skipped_existing=result.skipped_existing,
        skipped_suppressed=result.skipped_suppressed,
        drift_detected=result.drift_detected,
        orphaned=result.orphaned,
        lost_race=result.lost_race,
        skipped_no_admin=result.skipped_no_admin,
    )
    return result
