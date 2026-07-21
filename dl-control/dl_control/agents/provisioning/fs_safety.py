"""Symlink-safe, atomically-replacing managed-file I/O (spec §4.3).

dl-control writes config files into a directory the agent container also
owns. The agent can replace any managed file with a symlink; these helpers
refuse to follow one, and never leave a secret-bearing file with a readable
window.
"""

from __future__ import annotations

import contextlib
import os
import stat
import tempfile
from pathlib import Path

from dl_control.agents.provisioning.errors import ProvisioningError

# Mode for every managed file. The set includes secret-bearing .env files,
# so the whole config set is owner-only.
_FILE_MODE = 0o600


def resolve_agent_dir(agents_root: Path | str, agent_id: str) -> Path:
    """Return <agents_root>/<agent_id> with a lexical containment check —
    deliberately does NOT follow symlinks (`.resolve()` would). The actual
    symlink-safe walk happens inside `mkdir_safe`, `read_managed_text`, and
    `atomic_write_text`. Raises ProvisioningError(step='path_containment')
    if the id contains traversal characters."""
    root = Path(agents_root).resolve()  # the root MUST be real
    parts = Path(agent_id).parts
    if not parts or any(p in ("..", "", "/", ".") or "/" in p for p in parts):
        raise ProvisioningError(
            "path_containment", f"{agent_id!r} is not a valid agent id"
        ) from None
    candidate = root.joinpath(*parts)
    # Lexical containment: the relative path must not climb out.
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ProvisioningError("path_containment", f"{agent_id!r} escapes {root}") from exc
    return candidate


def _assert_regular_or_absent(path: Path) -> None:
    """Reject a managed path that exists as anything other than a regular
    file — a symlink, FIFO, device, or directory."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return  # absent is fine — first write
    if not stat.S_ISREG(st.st_mode):
        raise ProvisioningError(
            "symlink_check",
            f"managed path {path} is not a regular file (mode={st.st_mode:#o})",
        )


def _assert_parent_chain_safe(agent_dir: Path, managed_path: Path) -> None:
    """Walk from agent_dir down to managed_path.parent, lstat each
    intermediate component. Rejects any component that is not a real
    directory — an attacker may have replaced config/ with a symlink
    after the initial mkdir_safe (spec §4.3, threat model §13)."""
    try:
        managed_path.relative_to(agent_dir)
    except ValueError:
        raise ProvisioningError(
            "path_containment", f"{managed_path} not under {agent_dir}"
        ) from None
    rel = managed_path.parent.relative_to(agent_dir)
    cur = agent_dir
    for part in rel.parts:
        cur = cur / part
        try:
            st = os.lstat(cur)
        except FileNotFoundError:
            raise ProvisioningError(
                "path_containment", f"parent component {cur} does not exist"
            ) from None
        if not stat.S_ISDIR(st.st_mode):
            raise ProvisioningError(
                "symlink_check",
                f"parent component {cur} is not a directory (mode={st.st_mode:#o})",
            )


def read_managed_text(path: Path, *, agent_dir: Path | None = None) -> str:
    """Read a managed file, refusing to follow a symlink (O_NOFOLLOW).
    If `agent_dir` is provided, also validates every parent-directory
    component from agent_dir down is a real directory (no symlinks)."""
    if agent_dir is not None:
        _assert_parent_chain_safe(agent_dir, path)
    _assert_regular_or_absent(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise ProvisioningError("symlink_check", f"cannot open managed file {path}: {exc}") from exc
    with os.fdopen(fd, "r", encoding="utf-8") as fh:
        return fh.read()


def atomic_write_text(path: Path, content: str, *, agent_dir: Path | None = None) -> None:
    """Atomically write `content` to `path` at mode 0600.

    The temp file is created 0600 from the first byte (mkstemp default), so a
    secret never has a world/group-readable window; os.replace is atomic
    within the same directory. Refuses to replace a symlinked managed path.
    If `agent_dir` is provided, also validates the parent chain."""
    if agent_dir is not None:
        _assert_parent_chain_safe(agent_dir, path)
    _assert_regular_or_absent(path)
    directory = path.parent
    fd, tmp_name = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=path.name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def copy_managed_file(src: Path, dst: Path, *, agent_dir: Path | None = None) -> None:
    """Copy a managed file to a .bak path, preserving the 0600 mode and
    refusing symlinks on either end. If `agent_dir` is provided, validates
    both the src and dst parent chains before I/O."""
    if agent_dir is not None:
        _assert_parent_chain_safe(agent_dir, src)
        _assert_parent_chain_safe(agent_dir, dst)
    text = read_managed_text(src, agent_dir=agent_dir)
    atomic_write_text(dst, text, agent_dir=agent_dir)


def atomic_write_with_fsync(
    path: Path,
    content: str,
    *,
    mode: int = 0o600,
    agent_dir: Path | None = None,
) -> None:
    """Atomically write `content` to `path` with directory fsync.

    Extends the existing symlink-safe atomic_write_text pattern by adding
    fsync on the parent directory after os.replace, so the rename is durable.
    Accepts `agent_dir` and preserves the existing parent-chain and symlink
    checks before writing (spec §6.5, P3 extension)."""
    if agent_dir is not None:
        _assert_parent_chain_safe(agent_dir, path)
    _assert_regular_or_absent(path)
    directory = path.parent
    fd, tmp_name = tempfile.mkstemp(dir=directory, prefix=".tmp-")
    try:
        os.write(fd, content.encode())
        os.fsync(fd)
        os.close(fd)
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
        # fsync the directory so the rename is durable
        dir_fd = os.open(str(directory), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def mkdir_safe(agents_root: Path | str, target: Path) -> None:
    """Lexically walk from `agents_root` down to `target`, lstat-checking each
    component WITHOUT following symlinks (spec §4.3). Idempotent for existing
    real directories. Raises ProvisioningError(step='symlink_check') if any
    component is a symlink, file, or other non-directory; raises
    ProvisioningError(step='path_containment') if `target` is not lexically
    under `agents_root`.

    Critical: `target` is consumed LEXICALLY here — we do NOT call .resolve()
    on it, because that would follow any symlinks an attacker may have staged
    inside the root, defeating the check."""
    root = Path(agents_root).resolve()
    target_path = Path(target)
    try:
        relative = target_path.relative_to(root)
    except ValueError as exc:
        raise ProvisioningError("path_containment", f"{target_path} is not under {root}") from exc
    cursor = root
    for part in relative.parts:
        if part in ("..", "", "."):
            raise ProvisioningError(
                "path_containment", f"invalid component {part!r} in {target_path}"
            )
        cursor = cursor / part
        try:
            st = os.lstat(cursor)
        except FileNotFoundError:
            os.mkdir(cursor, mode=0o700)
            continue
        if not stat.S_ISDIR(st.st_mode):
            raise ProvisioningError(
                "symlink_check",
                f"path component {cursor} is not a regular directory (mode={st.st_mode:#o})",
            )
