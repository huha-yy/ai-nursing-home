"""Durable apply journal — survives watcher restart for state machine recovery.

The journal is written during ``preflight`` and updated atomically during
the apply state machine.  On watcher restart, if a journal exists and is
not marked ``committed`` or ``rolled-back``, the watcher resumes from the
journaled state.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ApplyJournal:
    """Mutable record of an in-flight OTA update cycle."""

    update_id: str
    manifest_version: str
    state: str
    previous_digests: dict[str, str] = field(default_factory=dict)
    applied_services: list[str] = field(default_factory=list)
    tier1_migration_warnings: list[str] = field(default_factory=list)
    target_digests: dict[str, str] = field(default_factory=dict)
    prev_digests: dict[str, str] = field(default_factory=dict)
    job_id: str | None = None
    successor_name: str | None = None
    self_swap_target_digest: str | None = None
    openclaw_prev_digest: str | None = None
    openclaw_committed: bool = False
    rollback_job_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "update_id": self.update_id,
            "manifest_version": self.manifest_version,
            "state": self.state,
            "previous_digests": self.previous_digests,
            "applied_services": self.applied_services,
            "tier1_migration_warnings": self.tier1_migration_warnings,
            "target_digests": self.target_digests,
            "prev_digests": self.prev_digests,
            "job_id": self.job_id,
            "successor_name": self.successor_name,
            "self_swap_target_digest": self.self_swap_target_digest,
            "openclaw_prev_digest": self.openclaw_prev_digest,
            "openclaw_committed": self.openclaw_committed,
            "rollback_job_id": self.rollback_job_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ApplyJournal:
        return cls(
            update_id=data["update_id"],
            manifest_version=data["manifest_version"],
            state=data["state"],
            previous_digests=data.get("previous_digests", {}),
            applied_services=data.get("applied_services", []),
            tier1_migration_warnings=data.get("tier1_migration_warnings", []),
            target_digests=data.get("target_digests", {}),
            prev_digests=data.get("prev_digests", {}),
            job_id=data.get("job_id"),
            successor_name=data.get("successor_name"),
            self_swap_target_digest=data.get("self_swap_target_digest"),
            openclaw_prev_digest=data.get("openclaw_prev_digest"),
            openclaw_committed=data.get("openclaw_committed", False),
            rollback_job_id=data.get("rollback_job_id"),
        )


def create_journal(
    manifest_version: str,
    previous_digests: dict[str, str] | None = None,
) -> ApplyJournal:
    """Create a new apply journal for an update cycle."""
    return ApplyJournal(
        update_id=str(uuid.uuid4()),
        manifest_version=manifest_version,
        state="preflight",
        previous_digests=previous_digests or {},
    )


def load_journal(path: str | Path) -> ApplyJournal | None:
    """Load a journal from disk. Returns None if the file does not exist."""
    p = Path(path)
    if not p.exists():
        return None
    raw = json.loads(p.read_text(encoding="utf-8"))
    return ApplyJournal.from_dict(raw)


def save_journal(journal: ApplyJournal, path: str | Path) -> None:
    """Atomically write the journal to disk (temp file + rename)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".json", prefix="ota_journal_", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(journal.to_dict(), f, indent=2)
        os.replace(tmp, str(p))
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


_TERMINAL_STATES = {"committed", "rolled-back", "rollback-failed"}


def is_active(journal: ApplyJournal) -> bool:
    """Return True if the journal represents an in-flight update."""
    return journal.state not in _TERMINAL_STATES


def mark_committed(journal: ApplyJournal, path: str | Path) -> None:
    """Mark the journal as committed and persist."""
    journal.state = "committed"
    save_journal(journal, path)


def mark_rolled_back(journal: ApplyJournal, path: str | Path) -> None:
    """Mark the journal as rolled-back and persist."""
    journal.state = "rolled-back"
    save_journal(journal, path)
