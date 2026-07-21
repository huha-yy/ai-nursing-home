"""Persistent OTA state with self-swap suppression marker."""

from __future__ import annotations

import datetime
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

SUPPRESSION_THRESHOLD = 2


@dataclass
class OtaState:
    current_version: str = "0.0.0"
    current_openclaw_digest: str | None = None
    failed_self_swap_targets: dict[str, dict] = field(default_factory=dict)

    def is_self_swap_suppressed(self, digest: str) -> bool:
        entry = self.failed_self_swap_targets.get(digest)
        if entry is None:
            return False
        return entry.get("attempts", 0) >= SUPPRESSION_THRESHOLD

    def record_self_swap_failure(self, digest: str) -> None:
        entry = self.failed_self_swap_targets.setdefault(digest, {})
        entry["attempts"] = entry.get("attempts", 0) + 1
        if "last_failure_at" not in entry:
            entry["last_failure_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

    def clear_self_swap_suppression(self, digest: str) -> None:
        self.failed_self_swap_targets.pop(digest, None)


def save_state(state: OtaState, path: Path) -> None:
    data = {
        "current_version": state.current_version,
        "current_openclaw_digest": state.current_openclaw_digest,
        "failed_self_swap_targets": state.failed_self_swap_targets,
    }
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".json", prefix="ota_state_", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp, str(p))
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def load_state(path: Path) -> OtaState:
    if not path.exists():
        return OtaState()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return OtaState()
    return OtaState(
        current_version=data.get("current_version", "0.0.0"),
        current_openclaw_digest=data.get("current_openclaw_digest"),
        failed_self_swap_targets=data.get("failed_self_swap_targets", {}),
    )
