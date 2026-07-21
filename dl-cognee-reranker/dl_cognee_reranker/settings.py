"""dl-cognee-reranker settings — env-driven, immutable, validated at load."""

from __future__ import annotations

import os

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"case_sensitive": False, "frozen": True}

    # Reranker model name.
    reranker_model: str = "BAAI/bge-reranker-v2-m3"


def load_settings() -> Settings:
    raw: dict[str, object] = {}
    if v := os.environ.get("RERANKER_MODEL"):
        raw["reranker_model"] = v
    return Settings(**raw)
