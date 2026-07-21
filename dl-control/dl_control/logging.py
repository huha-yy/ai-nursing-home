"""structlog configuration with a secret-redaction processor."""

from __future__ import annotations

import logging

import structlog

from dl_control.secrets_redaction import redact


def _redact_processor(_logger, _method_name, event_dict):
    """Scrub secret-looking keys from every log event (spec §7.2)."""
    return redact(event_dict)


def configure_logging() -> None:
    """Idempotent structlog setup. Call once at app startup."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            _redact_processor,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        cache_logger_on_first_use=True,
    )
