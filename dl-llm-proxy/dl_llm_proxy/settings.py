"""dl-llm-proxy settings (env-driven)."""

from __future__ import annotations

import os

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=False)

    local_llm_base_url: str
    local_llm_api_key: SecretStr | None = None
    dl_internal_api_key: SecretStr
    dl_control_url: str = "http://dato-control:8080"
    dl_llm_proxy_rpm: int = 30
    redis_url: SecretStr | None = None
    auth_cache_ttl_seconds: int = 30
    auth_cache_max_entries: int = 256


def load_settings() -> Settings:
    base = os.environ.get("LOCAL_LLM_BASE_URL", "").rstrip("/")
    if not base:
        raise SystemExit("LOCAL_LLM_BASE_URL is required — refusing to start")
    internal = os.environ.get("DL_INTERNAL_API_KEY", "")
    if not internal:
        raise SystemExit("DL_INTERNAL_API_KEY is required — refusing to start")
    return Settings(
        local_llm_base_url=base,
        local_llm_api_key=os.environ.get("LOCAL_LLM_API_KEY") or None,
        dl_internal_api_key=internal,
        dl_control_url=os.environ.get("DL_CONTROL_URL", "http://dato-control:8080"),
        dl_llm_proxy_rpm=int(os.environ.get("DL_LLM_PROXY_RPM", "30")),
        redis_url=os.environ.get("REDIS_URL") or None,
    )
