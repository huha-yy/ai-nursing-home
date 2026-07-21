"""dl-ota-watcher settings — env-driven, validated at load."""

from __future__ import annotations

import os

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=False, frozen=True)

    channel_url: str
    registry_url: str
    poll_interval_seconds: int = 86400
    health_window_seconds: int = 300
    backup_retention: int = 3
    compose_project: str = "dato"
    docker_host: str = "tcp://dato-docker-proxy:2375"
    owner_dsn: SecretStr
    app_dsn: SecretStr
    redis_url: SecretStr
    data_root: str = "/app/data"
    licence_key_path: str = "/app/secrets/licence.key"
    device_secret_path: str = "/app/data/secrets/device.secret"
    internal_api_key: SecretStr | None = None
    minisign_pubkey: str = ""
    control_url: str = "http://dato-control:8080"


def load_settings() -> Settings:
    raw: dict[str, object] = {
        "channel_url": os.environ["DATO_OTA_CHANNEL_URL"],
        "registry_url": os.environ["DATO_OTA_REGISTRY_URL"],
        "owner_dsn": os.environ["DATO_OTA_OWNER_DSN"],
        "app_dsn": os.environ["DATO_OTA_APP_DSN"],
        "redis_url": os.environ["DATO_OTA_REDIS_URL"],
    }
    if v := os.environ.get("DATO_OTA_POLL_INTERVAL_SECONDS"):
        raw["poll_interval_seconds"] = int(v)
    if v := os.environ.get("DATO_OTA_HEALTH_WINDOW_SECONDS"):
        raw["health_window_seconds"] = int(v)
    if v := os.environ.get("DATO_OTA_BACKUP_RETENTION"):
        raw["backup_retention"] = int(v)
    if v := os.environ.get("DATO_OTA_COMPOSE_PROJECT"):
        raw["compose_project"] = v
    if v := os.environ.get("DATO_OTA_DOCKER_HOST"):
        raw["docker_host"] = v
    if v := os.environ.get("DATO_OTA_DATA_ROOT"):
        raw["data_root"] = v
    if v := os.environ.get("DATO_OTA_LICENCE_KEY_PATH"):
        raw["licence_key_path"] = v
    if v := os.environ.get("DATO_OTA_DEVICE_SECRET_PATH"):
        raw["device_secret_path"] = v
    if v := os.environ.get("DL_INTERNAL_API_KEY"):
        raw["internal_api_key"] = v
    if v := os.environ.get("DATO_OTA_MINISIGN_PUBKEY"):
        raw["minisign_pubkey"] = v
    if v := os.environ.get("DATO_OTA_CONTROL_URL"):
        raw["control_url"] = v
    return Settings(**raw)
