"""GBrain OAuth credentials save service — writes .env + tags needs_restart.

Follows the same pattern as dl_control.channels.feishu.credentials_service
but simpler (no DuplicateAppIdError, no DB channel_config update — just
.env file manipulation).
"""

from __future__ import annotations

import contextlib
import fcntl
import os
from pathlib import Path
from uuid import UUID

from dl_control.agents.provisioning.config_gen import sh_single_quote as _sh_single_quote
from dl_control.agents.provisioning.fs_safety import atomic_write_with_fsync
from dl_control.agents.provisioning.service import ProvisioningConfig


class GbrainValidationError(Exception):
    """Raised when GBrain credentials fail validation."""


async def save_gbrain_credentials(
    db,
    agent_id: UUID,
    *,
    client_id: str,
    client_secret: str,
    prov_cfg: ProvisioningConfig,
    admin_user_id: UUID,
) -> None:
    """Save GBrain OAuth credentials for an agent.

    1. Acquire per-agent flock.
    2. DB UPDATE needs_restart = true.
    3. Write .env with GBRAIN_CLIENT_ID / GBRAIN_CLIENT_SECRET.
    """
    agent_dir = Path(prov_cfg.agents_root) / str(agent_id)
    lock_path = agent_dir / ".cred-lock"
    lock_fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)

    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            # DB UPDATE first — if it fails, no files touched
            async with db.conn(user_id=str(admin_user_id), role="admin") as conn:
                await conn.execute(
                    "UPDATE agents SET needs_restart = true WHERE id = %s",
                    (str(agent_id),),
                )

            # Materialize .env file changes
            env_path = agent_dir / "config" / ".env"
            new_env = _update_env_keys(env_path, client_id, client_secret)
            atomic_write_with_fsync(env_path, new_env, mode=0o600, agent_dir=agent_dir)

        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
    finally:
        os.close(lock_fd)


def _update_env_keys(
    env_path: Path,
    client_id: str,
    client_secret: str,
) -> str:
    """Read existing .env, add/update GBRAIN_CLIENT_* keys, return new content."""
    lines = env_path.read_text().splitlines(keepends=True) if env_path.exists() else []

    kept = [
        ln
        for ln in lines
        if not ln.startswith("GBRAIN_CLIENT_ID=")
        and not ln.startswith("GBRAIN_CLIENT_SECRET=")
        and not ln.startswith("# GBrain OAuth")
    ]
    kept.append("# GBrain OAuth client credentials\n")
    kept.append(f"GBRAIN_CLIENT_ID={_sh_single_quote(client_id)}\n")
    kept.append(f"GBRAIN_CLIENT_SECRET={_sh_single_quote(client_secret)}\n")
    return "".join(kept)
