"""Library management CLI (spec §6.6)."""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
from typing import TYPE_CHECKING

import click
import httpx

if TYPE_CHECKING:
    from dl_control.db import Database

from dl_control.libraries.service import (
    create_library,
    delete_library,
    get_library,
    grant_access,
    list_libraries,
    revoke_access,
)


def _db() -> Database:
    from dl_control.db import Database

    dsn = os.environ.get("DL_CONTROL_DB_URL", "")
    if not dsn:
        click.echo("DL_CONTROL_DB_URL is required", err=True)
        raise SystemExit(1)
    return Database(dsn)


def _cognee_url() -> str:
    return os.environ.get("DL_COGNEE_URL", "http://dl-cognee:8080")


def _cognee_admin_token() -> str | None:
    return os.environ.get("DL_COGNEE_ADMIN_TOKEN")


def _owner_dsn() -> str | None:
    return os.environ.get("DL_CONTROL_OWNER_DSN")


def _run(coro):
    """Sync wrapper for async coroutines in Click commands.
    Uses a thread-pool executor so it works both inside and outside
    a running event loop (pytest-asyncio creates one for async tests)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


@click.group(name="mgmt")
def mgmt():
    """Management commands."""


@mgmt.command("library-create")
@click.option("--slug", required=True)
@click.option("--display-name", required=True)
@click.option(
    "--sensitivity",
    type=click.Choice(["public", "shared", "restricted"]),
    required=True,
)
@click.option(
    "--storage-kind",
    type=click.Choice(["shared", "isolated"]),
    required=True,
)
def library_create(slug, display_name, sensitivity, storage_kind):
    db = _db()

    async def _cmd():
        await db.connect()
        try:
            lib = await create_library(
                db,
                slug=slug,
                display_name=display_name,
                sensitivity=sensitivity,
                storage_kind=storage_kind,
                owner_dsn=_owner_dsn(),
            )
            click.echo(
                json.dumps(
                    {
                        "id": lib.id,
                        "slug": lib.slug,
                        "display_name": lib.display_name,
                        "sensitivity": lib.sensitivity,
                        "storage_kind": lib.storage_kind,
                    },
                    indent=2,
                )
            )
        except ValueError as exc:
            click.echo(f"Error: {exc}", err=True)
            raise SystemExit(1) from exc
        finally:
            await db.close()

    _run(_cmd())


@mgmt.command("library-grant")
@click.option("--agent", required=True, help="Agent UUID")
@click.option("--library", required=True, help="Library slug")
@click.option(
    "--access",
    type=click.Choice(["read", "read_write"]),
    required=True,
)
def library_grant(agent, library, access):
    db = _db()

    async def _cmd():
        await db.connect()
        try:
            await grant_access(db, agent_id=agent, library_slug=library, access=access)
            click.echo(f"Granted {access} on {library} to agent {agent}")
        except ValueError as exc:
            click.echo(f"Error: {exc}", err=True)
            raise SystemExit(1) from exc
        finally:
            await db.close()

    _run(_cmd())


@mgmt.command("library-list")
def library_list():
    db = _db()

    async def _cmd():
        await db.connect()
        try:
            libs = await list_libraries(db)
            for lib in libs:
                click.echo(
                    f"{lib.slug:<24} {lib.display_name:<30} "
                    f"{lib.sensitivity:<12} {lib.storage_kind:<10}"
                )
        finally:
            await db.close()

    _run(_cmd())


@mgmt.command("library-show")
@click.argument("slug")
def library_show(slug):
    db = _db()

    async def _cmd():
        await db.connect()
        try:
            lib = await get_library(db, slug)
            if lib is None:
                click.echo(f"Library {slug!r} not found", err=True)
                raise SystemExit(1)
            click.echo(
                json.dumps(
                    {
                        "id": lib.id,
                        "slug": lib.slug,
                        "display_name": lib.display_name,
                        "sensitivity": lib.sensitivity,
                        "storage_kind": lib.storage_kind,
                        "per_library_db_name": lib.per_library_db_name,
                        "per_library_db_role": lib.per_library_db_role,
                        "owner_agent_id": lib.owner_agent_id,
                        "created_at": lib.created_at,
                        "updated_at": lib.updated_at,
                    },
                    indent=2,
                )
            )
        finally:
            await db.close()

    _run(_cmd())


@mgmt.command("library-revoke")
@click.option("--agent", required=True)
@click.option("--library", required=True)
def library_revoke(agent, library):
    db = _db()

    async def _cmd():
        await db.connect()
        try:
            await revoke_access(db, agent_id=agent, library_slug=library)
            click.echo(f"Revoked access on {library} from agent {agent}")
        except ValueError as exc:
            click.echo(f"Error: {exc}", err=True)
            raise SystemExit(1) from exc
        finally:
            await db.close()

    _run(_cmd())


@mgmt.command("library-drop")
@click.argument("slug")
def library_drop(slug):
    db = _db()

    async def _cmd():
        await db.connect()
        try:
            await delete_library(
                db,
                slug,
                owner_dsn=_owner_dsn(),
                cognee_admin_token=_cognee_admin_token(),
                cognee_url=_cognee_url(),
            )
            click.echo(f"Dropped library {slug!r}")
        except ValueError as exc:
            click.echo(f"Error: {exc}", err=True)
            raise SystemExit(1) from exc
        finally:
            await db.close()

    _run(_cmd())


@mgmt.command("library-ingest")
@click.option("--library", required=True)
@click.option("--path", "doc_path", required=True)
@click.option(
    "--content",
    "content_file",
    type=click.File("r"),
    required=True,
    help="File containing the content to ingest (@- for stdin)",
)
@click.option("--content-type", default="text/markdown")
def library_ingest(library, doc_path, content_file, content_type):
    """Admin ingest — calls POST /v1/admin/ingest on dl-cognee."""
    content = content_file.read()
    token = _cognee_admin_token()
    if not token:
        click.echo("DL_COGNEE_ADMIN_TOKEN is required for ingest", err=True)
        raise SystemExit(1)

    url = f"{_cognee_url().rstrip('/')}/v1/admin/ingest"

    async def _cmd():
        async with httpx.AsyncClient() as http:
            resp = await http.post(
                url,
                json={
                    "library_slug": library,
                    "path": doc_path,
                    "content": content,
                    "content_type": content_type,
                },
                headers={"Authorization": f"Bearer {token}"},
                timeout=60,
            )
            if resp.status_code >= 400:
                click.echo(f"Ingest failed: {resp.status_code} {resp.text}", err=True)
                raise SystemExit(1)
            click.echo(f"Ingested {doc_path!r} → library {library!r} ({resp.status_code})")

    _run(_cmd())


if __name__ == "__main__":
    mgmt()
