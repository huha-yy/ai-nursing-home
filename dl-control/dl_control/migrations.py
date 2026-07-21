"""Forward-only SQL migration runner (also runnable as a CLI).

Behaviour:
  - Every .sql file in MIGRATIONS_DIR is applied in lexical order inside a
    transaction; its SHA-256 is recorded in _schema_migrations.
  - A previously-applied file whose on-disk SHA changed raises
    MigrationChecksumMismatch — landed migrations are never auto-edited.

The runner connects as the *owner* role so 0001 can CREATE ROLE / GRANT.
The serving app never runs this — only the dato-control-migrate one-shot
or `python -m dl_control.migrations` (spec §4.1).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


class MigrationChecksumMismatch(RuntimeError):  # noqa: N818
    """A previously-applied migration's content changed on disk."""


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _quote_literal(value: str) -> str:
    """A SQL expression producing a quoted literal — 0001 concatenates it
    into dynamic SQL, so the replacement must include SQL quotes."""
    return "quote_literal('" + value.replace("'", "''") + "')"


async def list_applied(dsn: str) -> list[dict[str, Any]]:
    async with (
        await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn,
        conn.cursor(row_factory=dict_row) as cur,
    ):
        await cur.execute(
            """CREATE TABLE IF NOT EXISTS _schema_migrations (
                   id          text PRIMARY KEY,
                   applied_at  timestamptz NOT NULL DEFAULT now(),
                   sha256      text NOT NULL
               )"""
        )
        await cur.execute("SELECT id, applied_at, sha256 FROM _schema_migrations ORDER BY id")
        return list(await cur.fetchall())


async def apply_migrations(
    dsn: str,
    *,
    app_password: str,
    cognee_password: str = "",
    ota_watcher_app_password: str = "",
) -> None:
    """Apply pending migrations. Idempotent. Fails closed on SHA mismatch."""
    applied = {row["id"]: row["sha256"] for row in await list_applied(dsn)}
    files = sorted(p for p in MIGRATIONS_DIR.glob("*.sql"))

    async with await psycopg.AsyncConnection.connect(dsn, autocommit=False) as conn:
        for path in files:
            raw_text = path.read_text()
            sha = _sha256(raw_text)
            mid = path.name
            if mid in applied:
                if applied[mid] != sha:
                    raise MigrationChecksumMismatch(
                        f"Migration {mid} changed on disk; refusing to apply"
                    )
                continue
            text = raw_text.replace(":app_password", _quote_literal(app_password))
            text = text.replace(":cognee_password", _quote_literal(cognee_password))
            text = text.replace(
                ":ota_watcher_app_password",
                _quote_literal(ota_watcher_app_password),
            )
            async with conn.transaction(), conn.cursor() as cur:
                await cur.execute(text)
                await cur.execute(
                    "INSERT INTO _schema_migrations (id, sha256) VALUES (%s, %s)",
                    (mid, sha),
                )


def _cli() -> None:
    import argparse
    import asyncio
    import os

    p = argparse.ArgumentParser(prog="dl_control.migrations")
    p.add_argument(
        "--dsn",
        default=os.environ.get("DL_CONTROL_MIGRATION_DB_URL"),
        help="owner DSN (defaults to $DL_CONTROL_MIGRATION_DB_URL)",
    )
    p.add_argument(
        "--app-password",
        default=os.environ.get("DL_CONTROL_APP_PASSWORD"),
        help="password for the dl_control_app role (defaults to $DL_CONTROL_APP_PASSWORD)",
    )
    p.add_argument(
        "--cognee-password",
        default=os.environ.get("DL_COGNEE_PG_PASSWORD", ""),
        help="password for the cognee role (defaults to $DL_COGNEE_PG_PASSWORD)",
    )
    p.add_argument(
        "--ota-watcher-app-password",
        default=os.environ.get("DL_OTA_WATCHER_APP_PASSWORD", ""),
        help="password for the dato_ota_watcher_app role "
        "(defaults to $DL_OTA_WATCHER_APP_PASSWORD)",
    )
    args = p.parse_args()
    if not args.dsn or not args.app_password:
        raise SystemExit("DL_CONTROL_MIGRATION_DB_URL and DL_CONTROL_APP_PASSWORD are required")
    asyncio.run(
        apply_migrations(
            args.dsn,
            app_password=args.app_password,
            cognee_password=args.cognee_password,
            ota_watcher_app_password=args.ota_watcher_app_password,
        )
    )
    print("migrations applied")


if __name__ == "__main__":
    _cli()
