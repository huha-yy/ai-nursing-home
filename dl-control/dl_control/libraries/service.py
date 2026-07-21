"""Library + ACL CRUD service (spec §6.2)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from dl_control.db import Database

_SLUG_RE = re.compile(r"^[a-z0-9_-]{2,64}$")
_RESERVED_SLUGS = frozenset({"_public"})


@dataclass
class Library:
    id: str
    slug: str
    display_name: str
    sensitivity: str
    storage_kind: str
    per_library_db_name: str | None
    per_library_db_role: str | None
    owner_agent_id: str | None
    created_at: str
    updated_at: str


def _row_to_library(row: tuple) -> Library:
    id_, slug, display_name, sensitivity, storage_kind, per_lib_db, per_lib_role, owner, ca, ua = (
        row  # noqa: E501
    )
    return Library(
        id=str(id_),
        slug=slug,
        display_name=display_name,
        sensitivity=sensitivity,
        storage_kind=storage_kind,
        per_library_db_name=per_lib_db,
        per_library_db_role=per_lib_role,
        owner_agent_id=str(owner) if owner else None,
        created_at=ca.isoformat() if ca else "",
        updated_at=ua.isoformat() if ua else "",
    )


async def create_library(
    db: Database,
    *,
    slug: str,
    display_name: str,
    sensitivity: str,
    storage_kind: str,
    owner_dsn: str | None = None,
    actor_user_id: str | None = None,
) -> Library:
    """Create a knowledge library. For isolated, provisions the DB first."""
    if not _SLUG_RE.match(slug) or slug in _RESERVED_SLUGS:
        raise ValueError(f"invalid slug: {slug!r}")

    async with db.conn(user_id=actor_user_id, role="admin") as conn:
        cur = await conn.execute("SELECT 1 FROM knowledge_libraries WHERE slug = %s", (slug,))
        if await cur.fetchone():
            raise ValueError(f"slug already exists: {slug!r}")

        per_library_db_name = None
        per_library_db_role = None

        if storage_kind == "isolated":
            if not owner_dsn:
                raise ValueError("owner_dsn required for isolated storage")
            # Import inline to avoid ImportError in tests without db_lifecycle.
            from dl_control.libraries.db_lifecycle import (
                create_library_db,
                drop_library_db,
            )

            db_name, role_name = await create_library_db(owner_dsn)
            per_library_db_name = db_name
            per_library_db_role = role_name

            # If the subsequent INSERT fails, clean up the orphaned DB + role.
            try:
                await conn.execute(
                    "INSERT INTO knowledge_libraries "
                    "(slug, display_name, sensitivity, storage_kind, "
                    "per_library_db_name, per_library_db_role) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (
                        slug,
                        display_name,
                        sensitivity,
                        storage_kind,
                        per_library_db_name,
                        per_library_db_role,
                    ),
                )
            except Exception:
                await drop_library_db(owner_dsn, db_name=db_name, role_name=role_name)
                raise

            cur = await conn.execute(
                "SELECT id, slug, display_name, sensitivity, storage_kind, "
                "per_library_db_name, per_library_db_role, owner_agent_id, "
                "created_at, updated_at "
                "FROM knowledge_libraries WHERE slug = %s",
                (slug,),
            )
            row = await cur.fetchone()
        else:
            await conn.execute(
                "INSERT INTO knowledge_libraries "
                "(slug, display_name, sensitivity, storage_kind, "
                "per_library_db_name, per_library_db_role) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    slug,
                    display_name,
                    sensitivity,
                    storage_kind,
                    per_library_db_name,
                    per_library_db_role,
                ),
            )

            cur = await conn.execute(
                "SELECT id, slug, display_name, sensitivity, storage_kind, "
                "per_library_db_name, per_library_db_role, owner_agent_id, "
                "created_at, updated_at "
                "FROM knowledge_libraries WHERE slug = %s",
                (slug,),
            )
            row = await cur.fetchone()
    return _row_to_library(row)


async def list_libraries(
    db: Database,
    *,
    actor_user_id: str | None = None,
) -> list[Library]:
    async with db.conn(user_id=actor_user_id, role="admin") as conn:
        cur = await conn.execute(
            "SELECT id, slug, display_name, sensitivity, storage_kind, "
            "per_library_db_name, per_library_db_role, owner_agent_id, "
            "created_at, updated_at "
            "FROM knowledge_libraries ORDER BY created_at DESC"
        )
        rows = await cur.fetchall()
    return [_row_to_library(r) for r in rows]


async def get_library(
    db: Database,
    slug: str,
    *,
    actor_user_id: str | None = None,
) -> Library | None:
    async with db.conn(user_id=actor_user_id, role="admin") as conn:
        cur = await conn.execute(
            "SELECT id, slug, display_name, sensitivity, storage_kind, "
            "per_library_db_name, per_library_db_role, owner_agent_id, "
            "created_at, updated_at "
            "FROM knowledge_libraries WHERE slug = %s",
            (slug,),
        )
        row = await cur.fetchone()
    return _row_to_library(row) if row else None


async def delete_library(
    db: Database,
    slug: str,
    *,
    owner_dsn: str | None = None,
    cognee_admin_token: str | None = None,
    cognee_url: str | None = None,
    actor_user_id: str | None = None,
) -> None:
    """Hard-delete a library. Rejects if owner_agent_id IS NOT NULL."""
    async with db.conn(user_id=actor_user_id, role="admin") as conn:
        cur = await conn.execute(
            "SELECT id, storage_kind, per_library_db_name, per_library_db_role, "
            "owner_agent_id FROM knowledge_libraries WHERE slug = %s",
            (slug,),
        )
        row = await cur.fetchone()
        if row is None:
            raise ValueError(f"library not found: {slug!r}")

        lib_id, storage_kind, lib_db_name, lib_db_role, owner_agent_id = row
        if owner_agent_id is not None:
            raise ValueError(
                f"cannot delete auto-private library (owner_agent_id={owner_agent_id}). "
                "Managed by agent lifecycle."
            )

    # Call dl-cognee DELETE (best-effort — log error but don't block).
    if cognee_url and cognee_admin_token:
        try:
            async with httpx.AsyncClient() as http:
                resp = await http.delete(
                    f"{cognee_url.rstrip('/')}/v1/library/{slug}",
                    headers={"Authorization": f"Bearer {cognee_admin_token}"},
                    timeout=10,
                )
                if resp.status_code >= 500:
                    import logging

                    logging.getLogger(__name__).warning(
                        "dl-cognee DELETE /v1/library/%s returned %d",
                        slug,
                        resp.status_code,
                    )
        except Exception:
            import logging

            logging.getLogger(__name__).warning(
                "dl-cognee DELETE /v1/library/%s failed",
                slug,
                exc_info=True,
            )

    # For isolated: drop the per-library DB.
    if storage_kind == "isolated" and owner_dsn and lib_db_name and lib_db_role:
        from dl_control.libraries.db_lifecycle import drop_library_db

        await drop_library_db(owner_dsn, db_name=lib_db_name, role_name=lib_db_role)

    async with db.conn(user_id=actor_user_id, role="admin") as conn:
        await conn.execute("DELETE FROM knowledge_libraries WHERE slug = %s", (slug,))


async def grant_access(
    db: Database,
    *,
    agent_id: str,
    library_slug: str,
    access: str,
    actor_user_id: str | None = None,
) -> None:
    async with db.conn(user_id=actor_user_id, role="admin") as conn:
        cur = await conn.execute(
            "SELECT id FROM knowledge_libraries WHERE slug = %s",
            (library_slug,),
        )
        lib_row = await cur.fetchone()
        if lib_row is None:
            raise ValueError(f"library not found: {library_slug!r}")

        library_id = lib_row[0]
        await conn.execute(
            "DELETE FROM agent_library_access WHERE agent_id = %s AND library_id = %s",
            (agent_id, library_id),
        )
        await conn.execute(
            "INSERT INTO agent_library_access (agent_id, library_id, access) VALUES (%s, %s, %s)",
            (agent_id, library_id, access),
        )


async def revoke_access(
    db: Database,
    *,
    agent_id: str,
    library_slug: str,
    actor_user_id: str | None = None,
) -> None:
    async with db.conn(user_id=actor_user_id, role="admin") as conn:
        cur = await conn.execute(
            "SELECT id FROM knowledge_libraries WHERE slug = %s",
            (library_slug,),
        )
        lib_row = await cur.fetchone()
        if lib_row is None:
            raise ValueError(f"library not found: {library_slug!r}")

        library_id = lib_row[0]
        await conn.execute(
            "DELETE FROM agent_library_access WHERE agent_id = %s AND library_id = %s",
            (agent_id, library_id),
        )


async def get_library_dsn(
    db: Database,
    slug: str,
    *,
    agents_root: str,
) -> dict[str, Any]:
    """Look up DSN for a library (internal endpoint, system role)."""
    from pathlib import Path

    from dl_control.libraries.db_lifecycle import build_library_db_dsn

    async with db.conn(user_id=None, role="system") as conn:
        cur = await conn.execute(
            "SELECT id, storage_kind, per_library_db_name, per_library_db_role, "
            "owner_agent_id "
            "FROM knowledge_libraries WHERE slug = %s",
            (slug,),
        )
        row = await cur.fetchone()
        if row is None:
            raise ValueError(f"library not found: {slug!r}")

        _lib_id, storage_kind, lib_db_name, lib_db_role, owner_agent_id = row

        if storage_kind == "shared":
            return {"storage_kind": "shared", "dsn": None}

        dsn = None
        agents_path = Path(agents_root)
        if owner_agent_id is not None and lib_db_name and lib_db_role:
            password = _read_secret(agents_path / "secrets" / "per_agent_db" / str(owner_agent_id))
            if not password:
                short8 = lib_db_name.removeprefix("cognee_lib_")
                password = _read_secret(agents_path / "secrets" / "per_library_db" / short8)
            if password:
                dsn = build_library_db_dsn(lib_db_name, lib_db_role, password)
        elif lib_db_name and lib_db_role:
            short8 = lib_db_name.removeprefix("cognee_lib_")
            password = _read_secret(agents_path / "secrets" / "per_library_db" / short8)
            if password:
                dsn = build_library_db_dsn(lib_db_name, lib_db_role, password)

        return {"storage_kind": "isolated", "dsn": dsn}


def _read_secret(path: Path) -> str | None:
    try:
        return path.read_text().strip() or None
    except (OSError, FileNotFoundError):
        return None
