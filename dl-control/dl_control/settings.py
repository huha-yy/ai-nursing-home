"""dl-control settings — env-driven, immutable, validated at load.

Source of truth for every config value the app reads at startup. See spec
§11. The long-running app reads DL_CONTROL_DB_URL as the dl_control_app DSN
directly — there is no owner credential in the serving process.
"""

from __future__ import annotations

import os

from pydantic import HttpUrl, SecretStr, field_validator
from pydantic.fields import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Obvious dev placeholders that must never reach production.
_SECRET_KEY_PLACEHOLDERS = frozenset(
    {"change_me", "changeme", "dev_secret_key", "secret", "placeholder"}
)


class Settings(BaseSettings):
    """All env-driven config. Immutable; tests monkeypatch the env."""

    model_config = SettingsConfigDict(case_sensitive=False, frozen=True)

    db_url: SecretStr
    redis_url: SecretStr
    secret_key: SecretStr
    site_host: str
    session_ttl_seconds: int = 86400
    login_rate_limit_fails: int = 5
    login_rate_limit_window_seconds: int = 900

    # --- P2 provisioning (spec §12.2) ---
    docker_host: str = "tcp://dato-docker-proxy:2375"
    agents_root: str = "/data/agents"
    host_agents_root: str = "/data/agents"
    openclaw_image: str = "dato-openclaw:2026.4.8"
    deepseek_api_key: SecretStr
    pexels_api_key: SecretStr | None = None
    tavily_api_key: SecretStr | None = None
    templates_root: str = "/app/templates"
    precreated_agents_root: str = "/app/precreated_agents"

    # --- P3 Feishu credential wizard (spec §11.1) ---
    feishu_validate_base_url: str = "https://open.feishu.cn"

    # --- P4 Tier 1 isolation (spec §11) ---
    local_llm_base_url: HttpUrl | None = None
    local_llm_api_key: SecretStr | None = None
    egress_dns_extra_deny: list[str] = Field(default_factory=list)
    egress_dns_disable: bool = False
    caddy_log_retention_days: int = 30
    audit_mirror_poll_seconds: float = 2.0
    owner_dsn: SecretStr | None = None  # owner role DSN for CREATE DATABASE / ROLE

    # --- P5 dl-cognee integration (spec §6) ---
    dl_internal_api_key: SecretStr | None = None
    dl_cognee_admin_token: SecretStr | None = None
    dl_cognee_url: str = "http://dl-cognee:8080"

    # --- P6 dl-llm-local (spec §9) ---
    local_llm_default_model: str = "qwen3.5:9b"
    local_llm_keep_alive_seconds: int = 1800
    xiaomi_mimo_api_key: SecretStr | None = None
    feishu_webhook_url: str | None = None

    # --- P7 dl-ota-watcher (spec §11.2) ---
    dato_ota_roll_job_ttl_seconds: int = 86400
    dato_ota_health_window_seconds: int = 300

    # --- P11 active-agent reconciler ---
    reconcile_concurrency: int = 4

    # --- P13b/P13c workflow runner (workflow spec §6.2, §5.8) ---
    workflow_lease_ttl_seconds: int = 300
    workflow_poll_seconds: float = 1.0
    workflow_schedule_tick_seconds: float = 5.0

    # --- P13c pilot SMTP (workflow spec §11 — creds from env, never the DB) ---
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_from: str | None = None
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_starttls: bool = True

    # --- P13d agent peer interface (workflow spec §7, D-P13D-13) ---
    workflow_agent_dispatch_timeout_seconds: float = 10.0
    workflow_agent_repost_backoff_seconds: float = 60.0
    workflow_agent_repost_max: int = 3
    workflow_agent_receiver_port: int = 18790

    # --- P10 ComfyUI remote image generation ---
    comfyui_url: str | None = None

    @field_validator(
        "workflow_lease_ttl_seconds",
        "workflow_poll_seconds",
        "workflow_schedule_tick_seconds",
        "workflow_agent_dispatch_timeout_seconds",
        "workflow_agent_repost_backoff_seconds",
        "workflow_agent_repost_max",
        "workflow_agent_receiver_port",
    )
    @classmethod
    def _check_workflow_intervals(cls, v: float) -> float:
        # All workflow runner/peer config values must be positive.
        if v <= 0:
            raise ValueError("workflow runner intervals must be > 0")
        return v

    @field_validator("deepseek_api_key")
    @classmethod
    def _check_deepseek(cls, v: SecretStr) -> SecretStr:
        if not v.get_secret_value():
            raise ValueError("DEEPSEEK_API_KEY must be non-empty")
        return v

    @field_validator("host_agents_root")
    @classmethod
    def _check_host_root_absolute(cls, v: str) -> str:
        if not v.startswith("/"):
            raise ValueError(
                "DL_CONTROL_HOST_AGENTS_ROOT must be an absolute path "
                f"(got {v!r}) — Docker bind sources are not resolved relative "
                "to the compose file"
            )
        return v

    @field_validator("secret_key")
    @classmethod
    def _check_secret_key(cls, v: SecretStr) -> SecretStr:
        s = v.get_secret_value()
        low = s.lower()
        if low in _SECRET_KEY_PLACEHOLDERS or low.startswith("test-secret-key"):
            raise ValueError(
                "DL_CONTROL_SECRET_KEY is a known placeholder; generate a real "
                "key (openssl rand -base64 24)"
            )
        if len(s) < 32:
            raise ValueError("DL_CONTROL_SECRET_KEY must be at least 32 characters")
        if len(set(s)) < 16:
            raise ValueError("DL_CONTROL_SECRET_KEY must have at least 16 distinct characters")
        return v

    @field_validator("feishu_validate_base_url")
    @classmethod
    def _feishu_base_url_must_be_https(cls, v: str) -> str:
        if not v.startswith("https://"):
            raise ValueError(f"feishu_validate_base_url must be HTTPS (got {v!r})")
        return v


def load_settings() -> Settings:
    """Construct Settings from os.environ. Raises KeyError for a missing
    required var, ValueError for an invalid value."""
    raw: dict[str, object] = {
        "db_url": os.environ["DL_CONTROL_DB_URL"],
        "redis_url": os.environ["DL_CONTROL_REDIS_URL"],
        "secret_key": os.environ["DL_CONTROL_SECRET_KEY"],
        "site_host": os.environ["DL_CONTROL_SITE_HOST"],
    }
    if v := os.environ.get("SESSION_TTL_SECONDS"):
        raw["session_ttl_seconds"] = int(v)
    if v := os.environ.get("LOGIN_RATE_LIMIT_FAILS"):
        raw["login_rate_limit_fails"] = int(v)
    if v := os.environ.get("LOGIN_RATE_LIMIT_WINDOW_SECONDS"):
        raw["login_rate_limit_window_seconds"] = int(v)
    for env_name, field in (
        ("DL_CONTROL_DOCKER_HOST", "docker_host"),
        ("DL_CONTROL_AGENTS_ROOT", "agents_root"),
        ("DL_CONTROL_HOST_AGENTS_ROOT", "host_agents_root"),
        ("DATO_OPENCLAW_IMAGE", "openclaw_image"),
        ("DL_CONTROL_TEMPLATES_ROOT", "templates_root"),
        ("DL_CONTROL_FEISHU_BASE_URL", "feishu_validate_base_url"),
    ):
        if v := os.environ.get(env_name):
            raw[field] = v
    raw["deepseek_api_key"] = os.environ["DEEPSEEK_API_KEY"]
    if "host_agents_root" not in raw and "agents_root" in raw:
        raw["host_agents_root"] = raw["agents_root"]
    # P4 Tier 1 isolation.
    if v := os.environ.get("DL_CONTROL_LOCAL_LLM_BASE_URL"):
        raw["local_llm_base_url"] = v
    if v := os.environ.get("DL_CONTROL_LOCAL_LLM_API_KEY"):
        raw["local_llm_api_key"] = v
    if v := os.environ.get("XIAOMI_MIMO_API_KEY"):
        raw["xiaomi_mimo_api_key"] = v
    if v := os.environ.get("FEISHU_WEBHOOK_URL"):
        raw["feishu_webhook_url"] = v
    if v := os.environ.get("DL_CONTROL_EGRESS_DNS_EXTRA_DENY"):
        raw["egress_dns_extra_deny"] = [s.strip() for s in v.split(",") if s.strip()]
    if v := os.environ.get("DL_CONTROL_EGRESS_DNS_DISABLE"):
        raw["egress_dns_disable"] = v.lower() in ("1", "true", "yes")
    if v := os.environ.get("DL_CONTROL_CADDY_LOG_RETENTION_DAYS"):
        raw["caddy_log_retention_days"] = int(v)
    if v := os.environ.get("DL_CONTROL_AUDIT_MIRROR_POLL_SECONDS"):
        raw["audit_mirror_poll_seconds"] = float(v)
    if v := os.environ.get("DL_CONTROL_OWNER_DSN"):
        raw["owner_dsn"] = v
    # P5 dl-cognee.
    if v := os.environ.get("DL_INTERNAL_API_KEY"):
        raw["dl_internal_api_key"] = v
    if v := os.environ.get("DL_COGNEE_ADMIN_TOKEN"):
        raw["dl_cognee_admin_token"] = v
    if v := os.environ.get("DL_COGNEE_URL"):
        raw["dl_cognee_url"] = v
    # P6 dl-llm-local.
    if v := os.environ.get("DL_LLM_LOCAL_MODEL"):
        raw["local_llm_default_model"] = v
    if v := os.environ.get("DL_LLM_LOCAL_KEEP_ALIVE"):
        raw["local_llm_keep_alive_seconds"] = int(v)
    # P7 dl-ota-watcher.
    if v := os.environ.get("DATO_OTA_ROLL_JOB_TTL_SECONDS"):
        raw["dato_ota_roll_job_ttl_seconds"] = int(v)
    if v := os.environ.get("DATO_OTA_HEALTH_WINDOW_SECONDS"):
        raw["dato_ota_health_window_seconds"] = int(v)
    # P11 active-agent reconciler.
    if v := os.environ.get("DL_CONTROL_RECONCILE_CONCURRENCY"):
        raw["reconcile_concurrency"] = int(v)
    # P13b workflow runner.
    if v := os.environ.get("DL_CONTROL_WORKFLOW_LEASE_TTL_SECONDS"):
        raw["workflow_lease_ttl_seconds"] = int(v)
    if v := os.environ.get("DL_CONTROL_WORKFLOW_POLL_SECONDS"):
        raw["workflow_poll_seconds"] = float(v)
    if v := os.environ.get("DL_CONTROL_WORKFLOW_SCHEDULE_TICK_SECONDS"):
        raw["workflow_schedule_tick_seconds"] = float(v)
    # P13c pilot SMTP.
    if v := os.environ.get("DL_CONTROL_SMTP_HOST"):
        raw["smtp_host"] = v
    if v := os.environ.get("DL_CONTROL_SMTP_PORT"):
        raw["smtp_port"] = int(v)
    if v := os.environ.get("DL_CONTROL_SMTP_FROM"):
        raw["smtp_from"] = v
    if v := os.environ.get("DL_CONTROL_SMTP_USERNAME"):
        raw["smtp_username"] = v
    if v := os.environ.get("DL_CONTROL_SMTP_PASSWORD"):
        raw["smtp_password"] = v
    if v := os.environ.get("DL_CONTROL_SMTP_STARTTLS"):
        raw["smtp_starttls"] = v.lower() in ("1", "true", "yes")
    # P10 ComfyUI.
    if v := os.environ.get("COMFYUI_URL"):
        raw["comfyui_url"] = v
    # P13d agent peer interface.
        raw["workflow_agent_dispatch_timeout_seconds"] = float(v)
    if v := os.environ.get("DL_CONTROL_WORKFLOW_AGENT_REPOST_BACKOFF_SECONDS"):
        raw["workflow_agent_repost_backoff_seconds"] = float(v)
    if v := os.environ.get("DL_CONTROL_WORKFLOW_AGENT_REPOST_MAX"):
        raw["workflow_agent_repost_max"] = int(v)
    if v := os.environ.get("DL_CONTROL_WORKFLOW_AGENT_RECEIVER_PORT"):
        raw["workflow_agent_receiver_port"] = int(v)
    return Settings(**raw)
