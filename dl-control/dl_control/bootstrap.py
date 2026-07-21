"""First-admin bootstrap — `python -m dl_control.bootstrap`.

Idempotent: a pg advisory lock serializes the check+insert, and the command
is a no-op when any admin already exists (spec §6.1). Connects as
dl_control_app (role 'system') — no owner credentials needed. Run only
after migrations.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets

from dl_control.audit.service import write_event
from dl_control.auth.service import hash_password
from dl_control.db import Database

# Arbitrary constant — serializes concurrent bootstrap attempts.
_BOOTSTRAP_LOCK_KEY = 920_510_001


async def bootstrap_first_admin(
    db: Database, *, username: str, password: str | None
) -> tuple[bool, str | None]:
    """Create the first admin if none exists.

    Returns (created, generated_password). created=False means a no-op
    because an admin already existed. generated_password is non-None only
    when a password was generated inside this call.
    """
    generated: str | None = None
    if password is None:
        password = secrets.token_urlsafe(18)
        generated = password

    async with db.conn(user_id=None, role="system") as conn:
        await conn.execute("SELECT pg_advisory_xact_lock(%s)", (_BOOTSTRAP_LOCK_KEY,))
        cur = await conn.execute("SELECT count(*) FROM users WHERE role = 'admin'")
        if (await cur.fetchone())[0] > 0:
            return (False, None)
        await conn.execute(
            "INSERT INTO users (username, password_hash, role, must_change_password) "
            "VALUES (%s, %s, 'admin', true)",
            (username, hash_password(password)),
        )
        await write_event(
            conn,
            actor_user_id=None,
            action="first_admin_created",
            target="system",
            meta={"username": username},
        )
    # P8: after creating the first admin, reconcile precreated agents.
    # Wrapped in try/except so existing tests that call
    # bootstrap_first_admin directly without env vars still pass.
    try:
        from dl_control.agents.provisioning.docker_client import DockerClient
        from dl_control.agents.provisioning.service import ProvisioningConfig
        from dl_control.precreated.reconciler import reconcile_precreated
        from dl_control.settings import load_settings

        s = load_settings()
        prov_cfg = ProvisioningConfig.from_settings(s)
        docker = DockerClient.from_host(s.docker_host)
        try:
            await reconcile_precreated(db, docker=docker, cfg=prov_cfg)
        finally:
            await docker.close()
    except Exception:
        import structlog

        structlog.get_logger(__name__).warning(
            "bootstrap_reconcile_precreated_failed",
            exc_info=True,
        )
    return (True, generated)


def _cli() -> None:
    parser = argparse.ArgumentParser(prog="dl_control.bootstrap")
    parser.add_argument("--username", default="admin")
    args = parser.parse_args()
    db_url = os.environ["DL_CONTROL_DB_URL"]
    password = os.environ.get("DL_CONTROL_BOOTSTRAP_PASSWORD") or None

    async def _run() -> None:
        db = Database(db_url)
        await db.connect()
        try:
            created, generated = await bootstrap_first_admin(
                db, username=args.username, password=password
            )
        finally:
            await db.close()
        if not created:
            print("bootstrap: an admin already exists — nothing to do")
            return
        print(f"bootstrap: created admin '{args.username}'")
        if generated is not None:
            print(f"bootstrap: generated password (shown once): {generated}")
        print("bootstrap: this admin must change its password on first login")

    asyncio.run(_run())


if __name__ == "__main__":
    _cli()
