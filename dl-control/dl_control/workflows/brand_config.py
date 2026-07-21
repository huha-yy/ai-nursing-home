"""Brand configuration loader — pure-data YAML files, no code changes to add a brand.

All brand-specific copy (mission, sector, tags, rules) lives in
``brand_configs/<brand>.yaml``.  Loading is done at import time so
``content_pipeline.py`` and other flow modules get a synchronous dict
without async I/O.

*Add a new brand in 3 steps (no Python code needed):*

1.  ``cp brand_configs/_template.yaml brand_configs/<slug>.yaml``
2.  Fill in the fields.
3.  Create ``configs/<slug>/`` with brand guidelines, keywords, assets.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_CONFIG_DIR = Path(__file__).resolve().parent / "brand_configs"
_DEFAULT_BRAND = "yonghe"

# Caches, populated once at the module level.
_BRAND_CONFIG: dict[str, dict[str, Any]] = {}


def _load_all() -> dict[str, dict[str, Any]]:
    """Load every ``.yaml`` in ``brand_configs/`` (except ``_template``)."""
    configs: dict[str, dict[str, Any]] = {}
    _dir = _CONFIG_DIR
    if not _dir.is_dir():
        return configs

    for path in sorted(_dir.iterdir()):
        if path.suffix not in (".yaml", ".yml"):
            continue
        if path.stem.startswith("_"):
            continue
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if isinstance(data, dict):
            configs[path.stem] = data
    return configs


# Populate cache at import time.
_BRAND_CONFIG.update(_load_all())


def get_brand(brand_slug: str | None) -> dict[str, Any]:
    """Return the config dict for *brand_slug*, falling back to yonghe.

    The fallback is silent: an unknown slug returns the yonghe config.
    """
    slug = (brand_slug or _DEFAULT_BRAND).strip().lower()
    cfg = _BRAND_CONFIG.get(slug)
    if cfg is not None:
        return cfg
    return _BRAND_CONFIG.get(_DEFAULT_BRAND, {})


def list_brands() -> list[str]:
    """Return sorted slug list of all loaded brands."""
    return sorted(_BRAND_CONFIG)
