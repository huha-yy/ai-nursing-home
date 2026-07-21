"""Shared text normalization — mirrors OpenClaw adapter behavior (spec §4.5).

OpenClaw's Feishu adapter does not override normalizeAllowEntry, so the rule
is `String.prototype.trim()`. We replicate it as a one-line Python helper.
Also provides safe_account_key for D-P3-2 account_id grammar validation.
"""

from __future__ import annotations

import re

_ACCOUNT_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def normalize_feishu_sender(sender_id: str) -> str:
    return sender_id.strip()


def safe_account_key(account_id: str) -> str:
    """Validate account_id against D-P3-2 grammar. Returns the id on success."""
    if not _ACCOUNT_ID_RE.match(account_id):
        raise ValueError(f"account_id {account_id!r} must match {_ACCOUNT_ID_RE.pattern}")
    return account_id
