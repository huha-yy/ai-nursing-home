"""Licence client — reads and validates the appliance licence key file.

The licence key file at /app/install/licence.key is the single source of
truth for all OTA auth credentials.  The watcher reads it at startup and
snapshots it at the start of each update cycle.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class Licence:
    """Immutable licence data read from the licence key file."""

    device_id: str
    customer_id: str
    manifest_token: str
    registry_user: str
    registry_password: str
    issued_at: datetime
    expires_at: datetime


_REQUIRED_FIELDS = {
    "device_id",
    "customer_id",
    "manifest_token",
    "registry_user",
    "registry_password",
    "issued_at",
    "expires_at",
}


def load_licence(path: str | Path) -> Licence:
    """Read and validate a licence key JSON file.

    Raises FileNotFoundError if the file does not exist.
    Raises ValueError if the JSON is malformed or required fields are missing.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))

    missing = _REQUIRED_FIELDS - raw.keys()
    if missing:
        raise ValueError(f"Licence file missing required fields: {missing}")

    return Licence(
        device_id=raw["device_id"],
        customer_id=raw["customer_id"],
        manifest_token=raw["manifest_token"],
        registry_user=raw["registry_user"],
        registry_password=raw["registry_password"],
        issued_at=datetime.fromisoformat(raw["issued_at"]),
        expires_at=datetime.fromisoformat(raw["expires_at"]),
    )


def is_valid(licence: Licence) -> bool:
    """Return True if the licence has not expired."""
    return licence.expires_at > datetime.now(tz=UTC)


def snapshot(licence: Licence) -> Licence:
    """Return a frozen copy of the licence for an update cycle.

    Since Licence is frozen (dataclass(frozen=True)), this is just the
    identity operation.  It exists as a documented seam for cycle-start
    snapshotting so the in-flight state machine is not affected by
    mid-cycle licence file changes.
    """
    return licence
