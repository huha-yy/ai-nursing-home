"""Per-library Postgres database lifecycle (spec §5.4).

Create/drop per-library databases and roles for isolated storage libraries.
Called by the library service at library-create time and library-drop time.
"""

from __future__ import annotations

import contextlib
import logging
import os
import secrets
from pathlib import Path

import psycopg

from dl_control.agents.provisioning.errors import ProvisioningError

logger = logging.getLogger(__name__)


class PerLibraryDBCollisionError(ProvisioningError):
    """short8 collision exhausted retries."""

    def __init__(self) -> None:
        super().__init__(
            "per_library_db",
            "short8 collision after retries",
        )


def _short8_from_uuid(uuid_bytes: bytes, segment: int = 0) -> str:
    hex_id = uuid_bytes.hex()
    start = segment * 8
    if start + 8 > len(hex_id):
        raise PerLibraryDBCollisionError()
    return hex_id[start : start + 8]


def _per_library_secrets_dir() -> Path:
    """secrets/per_library_db/ inside the agents root.

    Uses DL_CONTROL_AGENTS_ROOT env var. Falls back to a tmp directory in tests.
    """
    agents_root = os.environ.get("DL_CONTROL_AGENTS_ROOT", "")
    if not agents_root:
        # Test fallback — use a temp dir.
        import tempfile

        return Path(tempfile.gettempdir()) / "dato-test-secrets" / "per_library_db"
    return Path(agents_root) / "secrets" / "per_library_db"


def _write_secret(short8: str, password: str) -> None:
    d = _per_library_secrets_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / short8
    _atomic_write_text(path, password)


def _atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, text.encode())
    finally:
        os.close(fd)
    tmp.rename(path)


def _delete_secret(short8: str) -> None:
    path = _per_library_secrets_dir() / short8
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)


async def create_library_db(
    owner_dsn: str,
    *,
    max_retries: int = 3,
) -> tuple[str, str]:
    """Create per-library database + role. Returns (db_name, role_name).

    The password is written to secrets/per_library_db/<short8> and NOT returned
    to the caller (spec §5.4). Callers later read it from that file.
    Retries on short8 collision (checking knowledge_libraries.per_library_db_name).
    After creation, applies per-library migrations as superuser.
    """
    conn = await psycopg.AsyncConnection.connect(owner_dsn, autocommit=True)
    try:
        for attempt in range(max_retries):
            uuid_bytes = secrets.token_bytes(16)
            uuid_bytes = (
                uuid_bytes[:6]
                + bytes([0x40 | (uuid_bytes[6] & 0x0F)])
                + bytes([0x80 | (uuid_bytes[7] & 0x3F)])
                + uuid_bytes[8:]
            )
            short = _short8_from_uuid(uuid_bytes, segment=0)
            db_name = f"cognee_lib_{short}"
            role_name = f"cognee_lib_{short}_role"
            password = secrets.token_hex(32)

            cur = await conn.execute(
                "SELECT 1 FROM knowledge_libraries WHERE per_library_db_name = %s",
                (db_name,),
            )
            if await cur.fetchone():
                if attempt < max_retries - 1:
                    continue
                raise PerLibraryDBCollisionError()

            rid = psycopg.sql.Identifier(role_name)
            pw = psycopg.sql.Literal(password)
            await conn.execute(
                psycopg.sql.SQL(
                    "DO $$ BEGIN "
                    "  CREATE ROLE {role} "
                    "  WITH LOGIN PASSWORD {pw} "
                    "  NOCREATEDB NOCREATEROLE NOSUPERUSER NOINHERIT; "
                    "EXCEPTION WHEN duplicate_object THEN NULL; END; $$"
                ).format(role=rid, pw=pw)
            )

            did = psycopg.sql.Identifier(db_name)
            await conn.execute(psycopg.sql.SQL("CREATE DATABASE {db}").format(db=did))
            # Revoke PUBLIC connect so only the per-library role can log in.
            await conn.execute(
                psycopg.sql.SQL("REVOKE ALL ON DATABASE {db} FROM PUBLIC").format(db=did)
            )
            await conn.execute(
                psycopg.sql.SQL("GRANT CONNECT ON DATABASE {db} TO {role}").format(db=did, role=rid)
            )

            from dl_control.per_library_migrations.migrations import (
                apply_per_library_migrations,
            )

            admin_dsn = _build_admin_dsn_for_db(owner_dsn, db_name)
            await apply_per_library_migrations(admin_dsn)

            _write_secret(short, password)
            logger.info("per-library DB %s created", db_name)
            return db_name, role_name
    finally:
        await conn.close()


async def drop_library_db(
    owner_dsn: str,
    *,
    db_name: str,
    role_name: str,
) -> None:
    """Hard-drop a per-library database, its role, and delete the password file.
    Idempotent — safe to call on already-dropped objects."""
    conn = await psycopg.AsyncConnection.connect(owner_dsn, autocommit=True)
    try:
        await conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (db_name,),
        )
        did = psycopg.sql.Identifier(db_name)
        rid = psycopg.sql.Identifier(role_name)
        await conn.execute(psycopg.sql.SQL("DROP DATABASE IF EXISTS {db}").format(db=did))
        await conn.execute(psycopg.sql.SQL("DROP ROLE IF EXISTS {role}").format(role=rid))
    finally:
        await conn.close()
    # Delete the password file. short8 is extracted from db_name ("cognee_lib_<short8>").
    short8 = db_name.removeprefix("cognee_lib_")
    _delete_secret(short8)


def build_library_db_dsn(
    db_name: str,
    role_name: str,
    password: str,
    *,
    host: str = "dato-postgres",
    port: int = 5432,
) -> str:
    """Build the DSN string for the per-library role."""
    from urllib.parse import quote

    return f"postgresql://{quote(role_name)}:{quote(password)}@{host}:{port}/{quote(db_name)}"


def _build_admin_dsn_for_db(owner_dsn: str, db_name: str) -> str:
    """Replace the database name in the owner DSN to point at a specific DB."""
    from urllib.parse import urlparse, urlunparse

    parsed = urlparse(owner_dsn)
    new = parsed._replace(path=f"/{db_name}")
    return urlunparse(new)
