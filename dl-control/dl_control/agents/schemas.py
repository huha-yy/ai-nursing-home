"""Agent registry pydantic models (spec §8.3)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Tier = Literal["tier0", "tier1"]


def dedup_skills(value: list[str]) -> list[str]:
    """Trim, drop empties, de-duplicate, preserve first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in value:
        skill = raw.strip()
        if skill and skill not in seen:
            seen.add(skill)
            out.append(skill)
    return out


class ModelSelection(BaseModel):
    """Closed model — only provider/model. extra='forbid' blocks any
    attempt to slip a secret (api_key, token, DSN) into the registry."""

    model_config = ConfigDict(extra="forbid")

    provider: str | None = None
    model: str | None = None


class AgentCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=200)
    tier: Tier
    skill_list: list[str] = Field(default_factory=list)
    channel_config: dict = Field(default_factory=dict)
    model_selection: ModelSelection = Field(default_factory=ModelSelection)

    @field_validator("skill_list")
    @classmethod
    def _dedup(cls, value: list[str]) -> list[str]:
        return dedup_skills(value)


class AgentUpdate(BaseModel):
    """All fields optional except tier, which is immutable in P1. extra='forbid'
    rejects any unknown field, including 'tier' in a PATCH."""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    skill_list: list[str] | None = None
    channel_config: dict | None = None
    model_selection: ModelSelection | None = None

    @field_validator("skill_list")
    @classmethod
    def _dedup(cls, value: list[str] | None) -> list[str] | None:
        return dedup_skills(value) if value is not None else None


class AgentOut(BaseModel):
    id: str
    display_name: str
    tier: str
    skill_list: list[str]
    channel_config: dict
    model_selection: dict
    status: str
    container_id: str | None = None
    needs_restart: bool = False
    feishu_configured: bool = False
    per_agent_db_name: str | None = None
    cognee_authz_version: int = 0
    created_at: str
    updated_at: str
    precreated_id: str | None = None
    precreated_yaml_sha256: str | None = None
    precreated_current_sha: str | None = None
    precreated_source_drift: bool = False
    precreated_source_removed: bool = False
