"""Render-once workspace seeding (spec §7). Renders every template under
<root>/workspace/*.md into the agent directory on first provision. On
restart, files are left untouched so user edits survive. Uses lstat (not
os.path.exists) to reject symlinks at the destination path."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import structlog
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from dl_control.agents.provisioning.errors import ProvisioningError

logger = structlog.get_logger(__name__)

# All workspace templates enumerated by spec §7.
_WORKSPACE_FILES = (
    "SOUL.md",
    "MEMORY.md",
    "USER.md",
    "IDENTITY.md",
    "HEARTBEAT.md",
    "TOOLS.md",
    "AGENTS.md",
)


def seed_workspace(
    templates_root: Path | str,
    agent_dir: Path,
    row: dict,
    *,
    seed_overlay_dir: Path | None = None,
) -> None:
    """Render workspace templates into `agent_dir` if they don't already
    exist (render-once; spec §7). Templates live under
    `<templates_root>/workspace/`. When `seed_overlay_dir` is provided,
    per-file overrides are rendered via `env.from_string()` (they live
    outside the FileSystemLoader root); missing override files fall back
    to the global template. Every destination is checked with lstat
    to refuse following a symlink; workspace files are not secret-bearing
    so a simple write suffices once the path is validated."""
    src_dir = Path(templates_root) / "workspace"
    agent_dir = Path(agent_dir)
    env = Environment(loader=FileSystemLoader(str(src_dir)), undefined=StrictUndefined)
    agent_ctx = {
        "id": row["id"],
        "display_name": row["display_name"],
        "tier": row["tier"],
    }
    user_ctx = {}  # spec §7: user context is always a dict (populated by P3)
    ctx = {"agent": agent_ctx, "user": user_ctx}

    # Check for extra files in overlay dir once, before the per-file loop.
    if seed_overlay_dir is not None and seed_overlay_dir.is_dir():
        extra_names: list[str] = []
        for fpath in sorted(seed_overlay_dir.iterdir()):
            if fpath.is_file() and fpath.name not in _WORKSPACE_FILES:
                extra_names.append(fpath.name)
        if extra_names:
            logger.warning(
                "seed_workspace_extra_file",
                agent_id=row.get("id"),
                filenames=extra_names,
            )

    for filename in _WORKSPACE_FILES:
        dest = agent_dir / filename
        # Also write to workspace/ subdirectory (OpenClaw reads from workspace/)
        ws_dest = agent_dir / "workspace" / filename
        ws_dest.parent.mkdir(parents=True, exist_ok=True)
        # Reject a symlinked destination (lstat, no follow).
        try:
            st = os.lstat(dest)
        except FileNotFoundError:
            pass  # absent → safe to create
        else:
            if stat.S_ISLNK(st.st_mode):
                raise ProvisioningError(
                    "symlink_check",
                    f"workspace file {dest} is a symlink",
                )
            continue  # exists as a regular file/dir — render-once

        # Per-file overlay check.
        if seed_overlay_dir is not None:
            override = seed_overlay_dir / filename
            if override.is_file():
                tpl = env.from_string(override.read_text())
                rendered = tpl.render(ctx)
                dest.write_text(rendered)
                ws_dest.write_text(rendered)
                continue

        # Fallback: global template via FileSystemLoader.
        template = env.get_template(filename)
        text = template.render(**ctx)
        dest.write_text(text)
        ws_dest.write_text(text)
