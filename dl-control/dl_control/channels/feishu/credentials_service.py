"""Wizard save flow — validate → flock → DB → write files (spec §7.3)."""

from __future__ import annotations

import contextlib
import fcntl
import os
from pathlib import Path
from uuid import UUID

import psycopg

from dl_control.agents.provisioning.config_gen import render_openclaw_json
from dl_control.agents.provisioning.fs_safety import atomic_write_with_fsync
from dl_control.channels.feishu.feishu_client import validate_feishu_credentials
from dl_control.channels.normalize import safe_account_key


class DuplicateAppIdError(Exception):
    """Another agent already uses this Feishu app_id (D-P3-15)."""

    def __init__(self, app_id: str, existing_agent_id: str = ""):
        super().__init__(f"Feishu app {app_id} already configured on agent {existing_agent_id}")
        self.app_id = app_id
        self.existing_agent_id = existing_agent_id


async def save_feishu_credentials(
    db,
    agent_id: UUID,
    *,
    app_id: str,
    app_secret: str,
    account_id: str,
    prov_cfg,
    feishu_base_url: str,
    admin_user_id: UUID,
) -> None:
    """Save Feishu credentials for an agent.

    1. Validate account_id grammar (D-P3-2).
    2. Validate credentials against Feishu API (§7.2).
    3. Acquire per-agent flock (§7.3, D-P3-9).
    4. DB UPDATE first — if it fails (409 duplicate), no files touched.
    5. Write .env with FEISHU_APP_ID_<ACCOUNT> / FEISHU_APP_SECRET_<ACCOUNT>.
    6. Regenerate openclaw.json with feishu_configured=true.
    """
    safe_account_key(account_id)
    await validate_feishu_credentials(app_id, app_secret, base_url=feishu_base_url)

    agent_dir = Path(prov_cfg.agents_root) / str(agent_id)
    lock_path = agent_dir / ".cred-lock"
    lock_fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)

    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            # DB UPDATE first — 409 on duplicate app_id leaves files untouched
            async with db.conn(user_id=str(admin_user_id), role="admin") as conn:
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    (f"feishu_creds:{agent_id}",),
                )
                try:
                    await conn.execute(
                        "UPDATE agents SET "
                        "  channel_config = coalesce(channel_config, '{}'::jsonb) || "
                        "      jsonb_build_object('feishu', jsonb_build_object("
                        "          'app_id',      %s::text, "
                        "          'account_id',  %s::text, "
                        "          'configured_at', to_jsonb(now()) "
                        "      )), "
                        "  feishu_configured = true, needs_restart = true "
                        "WHERE id = %s",
                        (app_id, account_id, str(agent_id)),
                    )
                except psycopg.errors.UniqueViolation as exc:
                    if "agents_unique_feishu_app" not in str(exc):
                        raise
                    # Transaction is aborted; use a fresh connection for
                    # the best-effort lookup of which agent owns this app_id.
                    existing = ""
                    try:
                        async with db.conn(
                            user_id=str(admin_user_id),
                            role="admin",
                        ) as lookup_conn:
                            cur = await lookup_conn.execute(
                                "SELECT id FROM agents "
                                "WHERE channel_config -> 'feishu' ->> 'app_id' = %s "
                                "  AND id <> %s",
                                (app_id, str(agent_id)),
                            )
                            other = await cur.fetchone()
                            existing = str(other[0]) if other else ""
                    except Exception:
                        pass
                    raise DuplicateAppIdError(app_id, existing_agent_id=existing) from exc

            # DB committed — materialize files
            env_path = agent_dir / "config" / ".env"
            new_env = _update_env_keys(env_path, account_id, app_id, app_secret)
            atomic_write_with_fsync(env_path, new_env, mode=0o600, agent_dir=agent_dir)

            # Regenerate openclaw.json with feishu_configured=true
            async with db.conn(user_id=str(admin_user_id), role="admin") as conn:
                from dl_control.agents import registry as agent_registry

                row = await agent_registry.get_agent(conn, str(agent_id))

            openclaw_path = agent_dir / "openclaw.json"
            existing = openclaw_path.read_text() if openclaw_path.exists() else None
            new_config = render_openclaw_json(
                prov_cfg.templates_root,
                row,
                site_host=prov_cfg.site_host,
                existing_json=existing,
                default_model=prov_cfg.local_llm_default_model,
            )
            atomic_write_with_fsync(
                openclaw_path,
                new_config,
                mode=0o644,
                agent_dir=agent_dir,
            )
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
    finally:
        os.close(lock_fd)


def _update_env_keys(
    env_path: Path,
    account_id: str,
    app_id: str,
    app_secret: str,
) -> str:
    """Read existing .env, add/update FEISHU_* keys, return new content."""
    from dl_control.agents.provisioning.config_gen import _sh_single_quote

    lines = env_path.read_text().splitlines(keepends=True) if env_path.exists() else []

    prefix = account_id.upper()
    id_key = f"FEISHU_APP_ID_{prefix}"
    secret_key = f"FEISHU_APP_SECRET_{prefix}"

    kept = [
        ln
        for ln in lines
        if not ln.startswith("FEISHU_APP_ID_") and not ln.startswith("FEISHU_APP_SECRET_")
    ]
    kept.append(f"{id_key}={_sh_single_quote(app_id)}\n")
    kept.append(f"{secret_key}={_sh_single_quote(app_secret)}\n")
    return "".join(kept)
