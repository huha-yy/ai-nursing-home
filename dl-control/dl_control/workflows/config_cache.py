"""Runtime config cache for workflow-level settings.

Populated at startup from the DB, refreshed by admin mutations.
Provides synchronous access so flow ``prepare()`` (sync) can read DB-backed
defaults without an async call.

Usage::

    # At startup (async):
    await config_cache.populate(db)

    # Inside a sync prepare() closure:
    agent_id = config_cache.get_default("content.pipeline")
           or config_cache.get_hardcoded_fallback()

    # After admin saves a new default:
    config_cache.set_default("content.pipeline", new_uuid)
"""

from __future__ import annotations

from uuid import UUID

_WORKFLOW_DEFAULTS: dict[str, UUID | None] = {}

# Last-resort fallback when the DB/cache has no entry for this workflow.
_HARDCODED_FALLBACK = UUID("7c90fc88-fd6f-452c-bb49-cc1b0ef20037")


async def populate(db) -> None:
    """Load ``default_agent_id`` for every workflow that has one set."""
    _WORKFLOW_DEFAULTS.clear()
    async with db.conn(user_id=None, role="system") as conn:
        cur = await conn.execute(
            "SELECT id, default_agent_id FROM workflow WHERE default_agent_id IS NOT NULL"
        )
        for row in await cur.fetchall():
            _WORKFLOW_DEFAULTS[row[0]] = row[1]


def get_default(workflow_id: str) -> UUID | None:
    """DB-backed default for this workflow, or ``None`` (-> use hardcoded fallback)."""
    return _WORKFLOW_DEFAULTS.get(workflow_id)


def get_hardcoded_fallback() -> UUID:
    """The compile-time fallback UUID, used when DB/cache has no entry."""
    return _HARDCODED_FALLBACK


def set_default(workflow_id: str, agent_id: UUID | None) -> None:
    """Update the in-memory cache (caller must also update the DB)."""
    if agent_id is None:
        _WORKFLOW_DEFAULTS.pop(workflow_id, None)
    else:
        _WORKFLOW_DEFAULTS[workflow_id] = agent_id
