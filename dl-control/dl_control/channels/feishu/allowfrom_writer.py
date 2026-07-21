"""Atomic JSON writer for feishu-<accountId>-allowFrom.json (spec §6.5).

Uses the P2 fs_safety.atomic_write_with_fsync pattern. target_dir is
pre-resolved and path-containment-checked by the caller (reconciler).
"""

from __future__ import annotations

from pathlib import Path

from dl_control.agents.provisioning.fs_safety import atomic_write_with_fsync


def write_allowfrom_file_atomic(
    *,
    content: str,
    target_dir: str,
    account_id: str,
    agent_dir: str | None = None,
) -> None:
    """Write content to target_dir/feishu-<account_id>-allowFrom.json.

    target_dir is already resolved to <agents_root>/<agent_id>/credentials
    and path-containment-validated by the caller (§6.5). If agent_dir is
    provided, passes through to atomic_write_with_fsync for symlink-safe
    write validation.
    """
    target = Path(target_dir) / f"feishu-{account_id}-allowFrom.json"
    resolved_agent_dir = Path(agent_dir) if agent_dir else None
    atomic_write_with_fsync(target, content, mode=0o644, agent_dir=resolved_agent_dir)
