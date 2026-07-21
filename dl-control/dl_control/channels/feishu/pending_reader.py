"""Live read of feishu-pairing.json with TTL + validation (spec §8)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from dl_control.channels.normalize import normalize_feishu_sender

logger = logging.getLogger(__name__)

# Matching OpenClaw's PAIRING_PENDING_TTL_MS = 3600 * 1e3
_PENDING_TTL_SECONDS = 3600
# Clock-skew floor: created_at must not be more than 60 s in the future
_MAX_FUTURE_SKEW_SECONDS = 60


@dataclass
class PendingRequest:
    id: str
    code: str
    account_id: str
    sender_id: str  # verbatim per D-P3-14
    sender_name: str | None
    created_at: datetime


def _has_required_string_fields(entry: dict, fields: tuple[str, ...]) -> bool:
    return all(isinstance(entry.get(k), str) for k in fields)


def _parse_iso_or_none(s: str) -> datetime | None:
    try:
        s = s.replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def read_pending(path: Path) -> list[PendingRequest]:
    """Read feishu-pairing.json, returning valid non-expired pending requests."""
    try:
        with open(path) as f:
            raw = f.read()
    except FileNotFoundError:
        return []

    try:
        doc = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("pending file unparseable; treating as empty")
        return []

    requests = doc.get("requests") if isinstance(doc, dict) else None
    if not isinstance(requests, list):
        return []

    now = datetime.now(UTC)
    out: list[PendingRequest] = []

    for entry in requests:
        if not isinstance(entry, dict):
            continue
        # OpenClaw's native schema keys the request by the sender open_id
        # ("id"), carries the account under meta.accountId, and has no separate
        # top-level senderId/accountId. Tolerate top-level forms too, in case a
        # future OpenClaw emits them.
        if not _has_required_string_fields(entry, ("id", "code", "createdAt")):
            continue
        meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}

        account_id = entry.get("accountId")
        if not isinstance(account_id, str):
            account_id = meta.get("accountId")
        if not isinstance(account_id, str) or not account_id.strip():
            continue

        # In OpenClaw's native schema the request id IS the sender open_id. If
        # a top-level senderId is explicitly present, honor it (and reject a
        # blank one); otherwise fall back to the request id.
        sender_id = entry["id"]
        if "senderId" in entry:
            raw_sender = entry.get("senderId")
            if not isinstance(raw_sender, str) or not raw_sender.strip():
                continue
            sender_id = raw_sender  # verbatim per D-P3-14
        if not sender_id.strip():
            continue

        created_at = _parse_iso_or_none(entry["createdAt"])
        if created_at is None:
            continue
        if (now - created_at).total_seconds() > _PENDING_TTL_SECONDS:
            continue
        if (created_at - now).total_seconds() > _MAX_FUTURE_SKEW_SECONDS:
            continue

        sender_name = entry.get("senderName")
        if not isinstance(sender_name, str):
            sender_name = meta.get("name") if isinstance(meta.get("name"), str) else None

        out.append(
            PendingRequest(
                id=entry["id"],
                code=entry["code"],
                account_id=account_id.strip(),
                sender_id=sender_id,  # verbatim per D-P3-14
                sender_name=sender_name,
                created_at=created_at,
            )
        )

    # OpenClaw's issueChallenge() appends a fresh requests[] entry every time an
    # unknown sender DMs (spec §4.1), so one person can have several pending
    # entries. Collapse to one row per (account_id, normalized sender) — the
    # same identity the DB enforces UNIQUE on (D-P3-14) — keeping the newest
    # challenge so the admin sees the code the user currently has.
    deduped: dict[tuple[str, str], PendingRequest] = {}
    for req in out:
        key = (req.account_id, normalize_feishu_sender(req.sender_id))
        existing = deduped.get(key)
        if existing is None or req.created_at >= existing.created_at:
            deduped[key] = req

    return list(deduped.values())
