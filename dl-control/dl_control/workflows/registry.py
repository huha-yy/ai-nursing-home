"""Flow registration — upsert code-defined flows into workflow + workflow_version.

A flow is Python code; this module records its identity and version so an
in-flight run can pin to the exact version it started on (spec §9). Versions
are immutable once registered: re-registering an identical version is a no-op;
a different code_ref for an already-registered version is a FlowVersionConflict.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg

_TRIGGERS = frozenset({"cron", "event", "manual", "agent"})


def _version_key(version: str) -> tuple[int, ...]:
    """Numeric ordering for dotted versions ('1.10.0' > '1.9.0')."""
    return tuple(int(part) for part in version.split("."))


class FlowVersionConflict(RuntimeError):  # noqa: N818
    """A registered (workflow_id, version) was re-registered with a new code_ref."""


@dataclass(frozen=True)
class FlowDescriptor:
    """Immutable description of one shipped flow version."""

    id: str
    version: str
    code_ref: str
    display_name: str
    default_trigger: str = "manual"
    description: str | None = None

    def __post_init__(self) -> None:
        if self.default_trigger not in _TRIGGERS:
            raise ValueError(f"bad default_trigger: {self.default_trigger!r}")
        try:
            _version_key(self.version)
        except ValueError:
            raise ValueError(f"bad version (want dotted integers): {self.version!r}") from None


async def register_flows(conn: psycopg.AsyncConnection, descriptors: list[FlowDescriptor]) -> None:
    """Idempotently register each descriptor. Caller supplies a system/admin
    connection (RLS). Runs in the caller's transaction.

    Order matters: the existing-version drift check runs BEFORE any mutation, so
    a FlowVersionConflict leaves the workflow head untouched (no half-applied
    metadata/latest_version update)."""
    for d in descriptors:
        # 1. Guard code_ref drift on an already-registered version FIRST —
        #    before mutating the workflow head — so a conflict is side-effect-free.
        cur = await conn.execute(
            "SELECT code_ref FROM workflow_version WHERE workflow_id = %s AND version = %s",
            (d.id, d.version),
        )
        row = await cur.fetchone()
        version_exists = row is not None
        if version_exists and row[0] != d.code_ref:
            raise FlowVersionConflict(f"{d.id} {d.version}: code_ref {row[0]!r} != {d.code_ref!r}")
        # 2. Upsert the flow head — but latest_version (and the metadata that
        #    travels with it) only ADVANCES. Boot re-registers every retained
        #    version in no guaranteed order; an older descriptor must not
        #    regress the pointer new runs start on.
        cur = await conn.execute(
            "SELECT latest_version FROM workflow WHERE id = %s FOR NO KEY UPDATE",
            (d.id,),
        )
        head = await cur.fetchone()
        head_is_newer = (
            head is not None
            and head[0] is not None
            and _version_key(head[0]) > _version_key(d.version)
        )
        if not head_is_newer:
            await conn.execute(
                """
                INSERT INTO workflow (id, display_name, description,
                                      default_trigger, latest_version)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    display_name    = EXCLUDED.display_name,
                    description     = EXCLUDED.description,
                    default_trigger = EXCLUDED.default_trigger,
                    latest_version  = EXCLUDED.latest_version,
                    updated_at      = now()
                """,
                (d.id, d.display_name, d.description, d.default_trigger, d.version),
            )
        # 3. First registration of this version (skip if it already exists —
        #    we already proved the code_ref matches in step 1).
        if not version_exists:
            await conn.execute(
                "INSERT INTO workflow_version (workflow_id, version, code_ref) VALUES (%s, %s, %s)",
                (d.id, d.version, d.code_ref),
            )
