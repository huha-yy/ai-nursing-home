"""Immutable, env-driven settings for dl-ocr."""

from __future__ import annotations

import os

from pydantic_settings import BaseSettings


class Settings(BaseSettings, frozen=True):
    """All configuration via environment variables.  No defaults for secrets."""

    # --- Model ---
    # Directory where EasyOCR model files are cached.
    model_dir: str = "/models"

    # --- Auth ---
    # Shared secret used to call dl-control's agent-verify endpoint.
    dl_internal_api_key: str = ""

    # --- Service ---
    # dl-control's base URL (for agent token verification).
    dl_control_url: str = "http://dato-control:8080"

    # Maximum image size in bytes (default 10 MiB).
    max_image_bytes: int = 10 * 1024 * 1024

    # Inference timeout in seconds.
    inference_timeout_seconds: int = 60


def load_settings() -> Settings:
    """Build Settings from os.environ with explicit type coercion."""
    raw: dict[str, object] = {}
    for key, value in os.environ.items():
        if not key.startswith("DL_OCR_"):
            continue
        config_key = key.removeprefix("DL_OCR_").lower()
        raw[config_key] = value

    # Integer coercions
    for int_key in ("max_image_bytes", "inference_timeout_seconds"):
        if int_key in raw and isinstance(raw[int_key], str):
            try:
                raw[int_key] = int(raw[int_key])
            except ValueError:
                pass

    return Settings(**raw)  # type: ignore[arg-type]
