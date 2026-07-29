"""Runtime config cache for workflow-level settings.

Populated at startup from the DB, refreshed by admin mutations.
Provides synchronous access so flow ``prepare()`` (sync) can read DB-backed
defaults without an async call.

Usage::

    # At startup (async):
    await config_cache.populate(db)

    # Inside a sync prepare() closure:
    agent_id = config_cache.get_agent_by_precreated("nursing-dept")
            or config_cache.get_default("nursing.ops")

    # After admin saves a new default:
    config_cache.set_default("nursing.ops", new_uuid)
"""

from __future__ import annotations

from uuid import UUID

_WORKFLOW_DEFAULTS: dict[str, UUID | None] = {}

# Per-workflow-step agent resolution: precreated_id → UUID.
# Populated once at startup from the agents table.
_PRECREATED_AGENTS: dict[str, UUID] = {}


async def populate(db) -> None:
    """Load defaults for every workflow + pre-resolve nursing precreated agent
    UUIDs from the agents table."""
    _WORKFLOW_DEFAULTS.clear()
    _PRECREATED_AGENTS.clear()
    async with db.conn(user_id=None, role="system") as conn:
        cur = await conn.execute(
            "SELECT id, default_agent_id FROM workflow WHERE default_agent_id IS NOT NULL"
        )
        for row in await cur.fetchall():
            _WORKFLOW_DEFAULTS[row[0]] = row[1]

        # Pre-resolve the nursing workflow's precreated agents at startup.
        # These are read by prepare() closures which MUST be synchronous.
        cur = await conn.execute(
            "SELECT precreated_id, id FROM agents "
            "WHERE precreated_id IS NOT NULL AND status = 'active'"
        )
        for row in await cur.fetchall():
            _PRECREATED_AGENTS[row[0]] = row[1]


def get_default(workflow_id: str) -> UUID | None:
    """DB-backed default for this workflow, or ``None``."""
    return _WORKFLOW_DEFAULTS.get(workflow_id)


def get_agent_by_precreated(precreated_id: str) -> UUID | None:
    """Resolve a precreated agent identity (e.g. 'nursing-dept') to its
    current UUID. Returns None when the agent is not in the cache (not yet
    provisioned, suppressed, or not active)."""
    return _PRECREATED_AGENTS.get(precreated_id)


def get_hardcoded_fallback() -> UUID | None:
    """DEPRECATED: Returns None. The old hardcoded UUID was from the legacy
    dato project and does not exist in the nursing deployment. Multi-agent
    workflows should use ``get_agent_by_precreated()`` instead; single-agent
    workflows should use ``get_default()``."""
    return None


def set_default(workflow_id: str, agent_id: UUID | None) -> None:
    """Update the in-memory cache (caller must also update the DB)."""
    if agent_id is None:
        _WORKFLOW_DEFAULTS.pop(workflow_id, None)
    else:
        _WORKFLOW_DEFAULTS[workflow_id] = agent_id


def set_precreated(precreated_id: str, agent_id: UUID) -> None:
    """Update the in-memory precreated-agent cache (after provisioning)."""
    _PRECREATED_AGENTS[precreated_id] = agent_id
