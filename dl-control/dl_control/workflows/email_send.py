"""SMTP sender — the pilot's perform() with §5.4 classification (D-P13C-10).

KnownCleanError ONLY when the provider provably did not act: SMTP
unconfigured, malformed request, connect failure (SMTPConnectError — which
covers connect timeouts), authentication failure, synchronous recipient
refusal (raised at RCPT, before DATA). Everything else — SMTPTimeoutError
after submission, connection reset mid-DATA — propagates as ambiguous, so
the ledger row stays 'started' and the run parks in waiting_manual.

Name note: email_send (not email) to avoid shadowing the stdlib package in
editors/tools.
"""

from __future__ import annotations

from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any

import aiosmtplib
from pydantic import SecretStr

from dl_control.workflows.errors import KnownCleanError

_SEND_TIMEOUT_SECONDS = 30.0
_REQUIRED_KEYS = ("to", "subject", "body")

# Raised before the message could have been accepted — provably not sent.
_KNOWN_CLEAN_EXCS = (
    aiosmtplib.SMTPConnectError,  # incl. SMTPConnectTimeoutError
    aiosmtplib.SMTPAuthenticationError,
    aiosmtplib.SMTPRecipientsRefused,
)


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    sender: str
    username: str | None
    password: SecretStr | None
    starttls: bool

    @classmethod
    def from_settings(cls, s) -> SmtpConfig | None:
        """None when SMTP is not configured (host + from are both required)."""
        if not s.smtp_host or not s.smtp_from:
            return None
        return cls(
            host=s.smtp_host,
            port=s.smtp_port,
            sender=s.smtp_from,
            username=s.smtp_username,
            password=s.smtp_password,
            starttls=s.smtp_starttls,
        )


async def send_email(cfg: SmtpConfig | None, request: dict[str, Any]) -> dict[str, Any]:
    """perform() for ctx.ledgered. request: {to, subject, body}."""
    if cfg is None:
        raise KnownCleanError(
            "SMTP is not configured (set DL_CONTROL_SMTP_HOST and DL_CONTROL_SMTP_FROM)"
        )
    missing = [k for k in _REQUIRED_KEYS if not request.get(k)]
    if missing:
        raise KnownCleanError(f"email request keys missing: {missing}")
    try:
        message = EmailMessage()
        message["From"] = cfg.sender
        message["To"] = request["to"]
        message["Subject"] = request["subject"]
        message.set_content(request["body"])
    except Exception as exc:
        # Malformed request (e.g. a newline in a header value → header
        # injection guard raises) — nothing was submitted, provably clean
        # (Codex pre-commit P2). Without this, a construction error would
        # land in waiting_manual despite no send attempt.
        raise KnownCleanError(f"email construction failed: {exc}") from exc
    try:
        await aiosmtplib.send(
            message,
            hostname=cfg.host,
            port=cfg.port,
            username=cfg.username,
            password=cfg.password.get_secret_value() if cfg.password else None,
            start_tls=cfg.starttls,
            timeout=_SEND_TIMEOUT_SECONDS,
        )
    except _KNOWN_CLEAN_EXCS as exc:
        raise KnownCleanError(f"smtp rejected before send: {exc}") from exc
    # Any other exception is ambiguous — let it propagate (§5.4).
    return {"to": request["to"], "subject": request["subject"], "sent": True}
