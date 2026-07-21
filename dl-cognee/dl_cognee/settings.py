"""dl-cognee settings — env-driven, immutable, validated at load."""

from __future__ import annotations

import os

from pydantic import SecretStr
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"case_sensitive": False, "frozen": True}

    # Owner DB — shared library storage.
    owner_db_dsn: SecretStr

    # dl-control for agent token verification.
    dl_control_url: str = "http://dato-control:8080"

    # Shared internal API key for dl-control verify.
    dl_internal_api_key: SecretStr

    # Admin token for ingest/library-delete from dl-control.
    dl_cognee_admin_token: SecretStr

    # Embedding model name (BAAI/bge-m3, 1024-dim).
    embedding_model: str = "BAAI/bge-m3"

    # Verify cache.
    verify_cache_ttl_seconds: int = 30
    verify_cache_negative_ttl_seconds: int = 5

    # Limits.
    max_content_bytes: int = 5 * 1024 * 1024  # 5 MiB
    max_search_limit: int = 20
    max_fan_out_libraries: int = 10
    per_library_timeout_seconds: float = 5.0

    # Reranker (Phase 2).
    reranker_url: str = "http://dl-cognee-reranker:8080"
    reranker_top_k: int = 20
    reranker_enabled: bool = True

    # Chunker.
    chunk_target_chars: int = 1500
    chunk_max_chars: int = 4000

    # Isolated DB pool.
    iso_pool_max: int = 32


def load_settings() -> Settings:
    raw: dict[str, object] = {
        "owner_db_dsn": os.environ["DL_COGNEE_OWNER_DB_DSN"],
        "dl_control_url": os.environ.get("DL_COGNEE_CONTROL_URL", "http://dato-control:8080"),
        "dl_internal_api_key": os.environ["DL_INTERNAL_API_KEY"],
        "dl_cognee_admin_token": os.environ["DL_COGNEE_ADMIN_TOKEN"],
    }
    for env_name, field in (
        ("DL_COGNEE_EMBEDDING_MODEL", "embedding_model"),
        ("DL_COGNEE_VERIFY_CACHE_TTL", "verify_cache_ttl_seconds"),
        ("DL_COGNEE_VERIFY_CACHE_NEGATIVE_TTL", "verify_cache_negative_ttl_seconds"),
        ("DL_COGNEE_MAX_CONTENT_BYTES", "max_content_bytes"),
        ("DL_COGNEE_MAX_SEARCH_LIMIT", "max_search_limit"),
        ("DL_COGNEE_MAX_FAN_OUT", "max_fan_out_libraries"),
        ("DL_COGNEE_PER_LIB_TIMEOUT", "per_library_timeout_seconds"),
        ("DL_COGNEE_CHUNK_TARGET_CHARS", "chunk_target_chars"),
        ("DL_COGNEE_CHUNK_MAX_CHARS", "chunk_max_chars"),
        ("DL_COGNEE_ISO_POOL_MAX", "iso_pool_max"),
        ("DL_COGNEE_RERANKER_URL", "reranker_url"),
        ("DL_COGNEE_RERANKER_TOP_K", "reranker_top_k"),
        ("DL_COGNEE_RERANKER_ENABLED", "reranker_enabled"),
    ):
        if v := os.environ.get(env_name):
            if field in (
                "verify_cache_ttl_seconds",
                "verify_cache_negative_ttl_seconds",
                "max_content_bytes",
                "max_search_limit",
                "max_fan_out_libraries",
                "chunk_target_chars",
                "chunk_max_chars",
                "iso_pool_max",
            ):
                raw[field] = int(v)
            elif field == "per_library_timeout_seconds":
                raw[field] = float(v)
            elif field == "reranker_top_k":
                raw[field] = int(v)
            elif field == "reranker_enabled":
                raw[field] = v.lower() in ("1", "true", "yes")
            else:
                raw[field] = v
    return Settings(**raw)
