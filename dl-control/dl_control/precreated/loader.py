"""P8 precreated agents — declarative seed layer."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from dl_control.agents.schemas import dedup_skills
from dl_control.precreated.errors import SeedLoadError
from dl_control.secrets_redaction import SecretInPayloadError, assert_no_secrets


def _validate_slug(precreated_id: str) -> None:
    import re

    if not re.match(r"^[a-z][a-z0-9-]{1,63}$", precreated_id):
        raise SeedLoadError(f"Seed id {precreated_id!r} must match ^[a-z][a-z0-9-]{{1,63}}$")


class Seed:
    """Parsed and validated seed definition."""

    __slots__ = (
        "id",
        "display_name",
        "tier",
        "admin_only",
        "skill_list",
        "channel_config",
        "model_selection",
        "raw_yaml",
    )

    def __init__(
        self,
        *,
        id: str,
        display_name: str,
        tier: str,
        admin_only: bool,
        skill_list: list[str],
        channel_config: dict,
        model_selection: dict,
        raw_yaml: dict,
    ) -> None:
        self.id = id
        self.display_name = display_name
        self.tier = tier
        self.admin_only = admin_only
        self.skill_list = skill_list
        self.channel_config = channel_config
        self.model_selection = model_selection
        self.raw_yaml = raw_yaml

    def __repr__(self) -> str:
        return f"Seed(id={self.id!r})"


_ALLOWED_TOP_LEVEL = {
    "id",
    "display_name",
    "tier",
    "admin_only",
    "skill_list",
    "channel_config",
    "model_selection",
}

_VALID_TIERS = {"tier0", "tier1"}


def _parse_seed_yaml(dirpath: Path, raw_yaml: dict) -> Seed:
    """Validate raw YAML dict and return a Seed.  Raises SeedLoadError
    on any violation."""
    precreated_id = dirpath.name

    unknown = set(raw_yaml) - _ALLOWED_TOP_LEVEL
    if unknown:
        raise SeedLoadError(f"Seed {precreated_id!r}: unknown top-level fields: {sorted(unknown)}")

    seed_id = raw_yaml.get("id")
    if seed_id is None:
        raise SeedLoadError(f"Seed {precreated_id!r}: missing required field 'id'")
    if seed_id != precreated_id:
        raise SeedLoadError(
            f"Seed {precreated_id!r}: id field {seed_id!r} does not match "
            f"directory name {precreated_id!r}"
        )
    _validate_slug(seed_id)

    display_name = raw_yaml.get("display_name")
    if not isinstance(display_name, str) or not (1 <= len(display_name) <= 200):
        raise SeedLoadError(
            f"Seed {precreated_id!r}: display_name must be a non-empty string of at most 200 chars"
        )

    tier = raw_yaml.get("tier", "tier0")
    if tier not in _VALID_TIERS:
        raise SeedLoadError(
            f"Seed {precreated_id!r}: tier must be one of {sorted(_VALID_TIERS)}, got {tier!r}"
        )

    admin_only = raw_yaml.get("admin_only", True)
    if not isinstance(admin_only, bool):
        raise SeedLoadError(f"Seed {precreated_id!r}: admin_only must be a boolean")

    raw_skills = raw_yaml.get("skill_list", []) or []
    if not isinstance(raw_skills, list) or not all(isinstance(s, str) for s in raw_skills):
        raise SeedLoadError(f"Seed {precreated_id!r}: skill_list must be a list of strings")
    skill_list = dedup_skills(list(raw_skills))

    channel_config = raw_yaml.get("channel_config", {}) or {}
    if not isinstance(channel_config, dict):
        raise SeedLoadError(f"Seed {precreated_id!r}: channel_config must be a dict")
    assert_no_secrets(channel_config, path=f"seed {precreated_id!r}.channel_config")

    model_selection = raw_yaml.get("model_selection", {}) or {}
    if not isinstance(model_selection, dict):
        raise SeedLoadError(f"Seed {precreated_id!r}: model_selection must be a dict")
    allowed_model_keys = {"provider", "model"}
    extra_model_keys = set(model_selection) - allowed_model_keys
    if extra_model_keys:
        raise SeedLoadError(
            f"Seed {precreated_id!r}: unknown fields in model_selection: {sorted(extra_model_keys)}"
        )

    return Seed(
        id=seed_id,
        display_name=display_name,
        tier=tier,
        admin_only=admin_only,
        skill_list=skill_list,
        channel_config=channel_config,
        model_selection=model_selection,
        raw_yaml=raw_yaml,
    )


def discover_seeds(seeds_root: Path) -> list[Seed]:
    if not seeds_root.exists() or not seeds_root.is_dir():
        return []
    seeds: list[Seed] = []
    for entry in sorted(seeds_root.iterdir()):
        if not entry.is_dir():
            continue
        yaml_path = entry / "agent.yaml"
        if not yaml_path.is_file():
            continue
        try:
            seeds.append(load_seed(seeds_root, entry.name))
        except SeedLoadError:
            raise
        except Exception as exc:
            raise SeedLoadError(f"Seed {entry.name!r}: failed to load agent.yaml: {exc}") from exc
    return seeds


def load_seed(seeds_root: Path, precreated_id: str) -> Seed:
    dirpath = seeds_root / precreated_id
    if not dirpath.is_dir():
        raise SeedLoadError(f"Seed directory {precreated_id!r} not found at {seeds_root}")
    yaml_path = dirpath / "agent.yaml"
    if not yaml_path.is_file():
        raise SeedLoadError(f"Seed {precreated_id!r}: agent.yaml not found")
    try:
        raw_text = yaml_path.read_text()
        raw_yaml = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise SeedLoadError(f"Seed {precreated_id!r}: malformed YAML: {exc}") from exc
    if not isinstance(raw_yaml, dict):
        raise SeedLoadError(
            f"Seed {precreated_id!r}: agent.yaml must be a mapping, got {type(raw_yaml).__name__}"
        )
    try:
        return _parse_seed_yaml(dirpath, raw_yaml)
    except SecretInPayloadError as exc:
        raise SeedLoadError(str(exc)) from exc


def canonical_seed_sha(raw_yaml: dict) -> str:
    raw_skills = raw_yaml.get("skill_list", []) or []
    model = raw_yaml.get("model_selection", {}) or {}
    model_canonical = {k: v for k, v in model.items() if v is not None}
    subset = {
        "display_name": raw_yaml["display_name"],
        "skill_list": dedup_skills(list(raw_skills)),
        "model_selection": model_canonical,
    }
    canonical = json.dumps(subset, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


_WORKSPACE_FILES = (
    "SOUL.md",
    "MEMORY.md",
    "USER.md",
    "IDENTITY.md",
    "HEARTBEAT.md",
    "TOOLS.md",
    "AGENTS.md",
)


def list_workspace_overrides(seeds_root: Path, precreated_id: str) -> list[str]:
    workspace_dir = seeds_root / precreated_id / "workspace"
    if not workspace_dir.is_dir():
        return []
    import structlog

    logger = structlog.get_logger(__name__)
    overrides: list[str] = []
    for fpath in sorted(workspace_dir.iterdir()):
        if not fpath.is_file():
            continue
        name = fpath.name
        if name in _WORKSPACE_FILES:
            overrides.append(name)
        else:
            logger.warning(
                "seed_workspace_extra_file",
                seed_id=precreated_id,
                filename=name,
            )
    return overrides
