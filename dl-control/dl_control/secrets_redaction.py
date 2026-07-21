"""Secret-key denylist + recursive redaction / rejection.

One denylist powers two call sites: audit-meta redaction in write_event
(spec §7.2) and registry channel_config rejection in the agents service
(spec §8.4).
"""

from __future__ import annotations

import re
from typing import Any

# Case-insensitive substring denylist (spec §7.2).
SECRET_KEY_PATTERNS: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "appsecret",
    "app_secret",
    "private_key",
    "credential",
    "authorization",
    "cookie",
    "set_cookie",
    "session",
    "sid",
    "dsn",
    "db_url",
    "database_url",
    "connection_string",
    "encoding_aes_key",
    "encrypt_key",
    "aes_key",
)

REDACTED = "***REDACTED***"


def _normalize(name: str) -> str:
    """Convert camelCase and kebab-case to snake_case for denylist matching."""
    s = re.sub(r"([a-z])([A-Z])", r"\1_\2", name)
    s = re.sub(r"[-.]+", "_", s)
    return s.lower()


def is_secret_key(key: str) -> bool:
    norm = _normalize(key)
    return any(pattern in norm for pattern in SECRET_KEY_PATTERNS)


def redact(value: Any) -> Any:
    """Return a deep copy of value with secret-looking dict keys masked."""
    if isinstance(value, dict):
        return {
            key: (REDACTED if is_secret_key(str(key)) else redact(val))
            for key, val in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


class SecretInPayloadError(ValueError):
    """A free-form registry field carried a secret-looking key."""


def assert_no_secrets(value: Any, *, path: str = "") -> None:
    """Raise SecretInPayloadError if any dict key, at any depth, is secret-like."""
    if isinstance(value, dict):
        for key, val in value.items():
            where = f"{path}.{key}" if path else str(key)
            if is_secret_key(str(key)):
                raise SecretInPayloadError(
                    f"secret-looking key not allowed in registry data: {where}"
                )
            assert_no_secrets(val, path=where)
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            assert_no_secrets(item, path=f"{path}[{index}]")
