"""Generate an agent's vendor config: openclaw.json, skills.yaml, .env.

openclaw.json is rendered from templates/openclaw.json.j2; the OpenClaw-owned
`meta` block is carried forward from any existing file (spec §6.1). skills.yaml
and .env are structured writes (spec §6.3, §6.4).
"""

from __future__ import annotations

import json
import secrets  # used by generate_openclaw_token (Task 5)
from pathlib import Path

import yaml  # used by render_skills_yaml (Task 5)
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from dl_control.agents.provisioning.errors import ProvisioningError
from dl_control.agents.provisioning.skill_catalog import CUSTOM_SKILL_NAMES

# Top-level keys every rendered openclaw.json must contain.
_REQUIRED_KEYS = (
    "models",
    "agents",
    "tools",
    "bindings",
    "commands",
    "channels",
    "gateway",
    "mcp",
    "plugins",
    "meta",
)


def _agent_context(row: dict, *, default_model: str = "qwen3.5:9b") -> dict:
    """Build the Jinja context's `agent` object from a registry row.

    P3: feishu_configured is now derived from channel_config (was hardcoded
    False in P2). P3's wizard populates channel_config.feishu.app_id.
    P6: Tier 1 default model derived from default_model kwarg (env-configurable).
    """
    model = row.get("model_selection") or {}
    channels = row.get("channel_config") or {}
    feishu = channels.get("feishu") or {}
    tier = row.get("tier", "tier0")
    if tier == "tier1":
        provider = "local"
        model_id = model.get("model") or default_model
    else:
        provider = model.get("provider") or "deepseek"
        model_id = model.get("model") or "deepseek-v4-pro"
    return {
        "id": row["id"],
        "display_name": row["display_name"],
        "tier": row["tier"],
        "admin_only": row.get("precreated_id") == "agent-manager",
        "model_provider": provider,
        "model_id": model_id,
        "peer_ids": channels.get("peer_ids") or [],
        "timezone": channels.get("timezone") or "UTC",
        "feishu_configured": bool(feishu.get("app_id")),
        "feishu_account_id": feishu.get("account_id") or "",
        "comfyui_configured": False,  # overridden by render_openclaw_json() caller
        "local_llm_proxy_url": "http://dl-llm-proxy:8080/v1",
    }


def render_openclaw_json(
    templates_dir: Path | str,
    row: dict,
    *,
    site_host: str,
    existing_json: str | None,
    default_model: str = "qwen3.5:9b",
    comfyui_configured: bool = False,
) -> str:
    """Render openclaw.json for a registry row. If `existing_json` is given,
    its `meta` block is carried forward unchanged (spec §6.1)."""
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        undefined=StrictUndefined,
        autoescape=False,  # JSON, not HTML — escaping would corrupt it
    )
    template = env.get_template("openclaw.json.j2")
    agent_ctx = _agent_context(row, default_model=default_model)
    agent_ctx["comfyui_configured"] = comfyui_configured
    model = row.get("model_selection") or {}
    rendered = template.render(
        agent=agent_ctx,
        caddy_domain=site_host,
        local_llm_proxy_url=agent_ctx["local_llm_proxy_url"],
        local_llm_model=(model.get("model") or default_model),
    )
    try:
        doc = json.loads(rendered)
    except json.JSONDecodeError as exc:
        raise ProvisioningError(
            "render_config", f"rendered openclaw.json is not valid JSON: {exc}"
        ) from exc
    # meta is OpenClaw-owned: carry it forward, never author from the template.
    if existing_json is not None:
        try:
            doc["meta"] = json.loads(existing_json).get("meta", {})
        except json.JSONDecodeError:
            doc["meta"] = {}
    else:
        doc["meta"] = {}
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def validate_openclaw_json(text: str) -> None:
    """Parse + check required top-level keys. Raises ProvisioningError."""
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProvisioningError(
            "validate_config", f"openclaw.json is not valid JSON: {exc}"
        ) from exc
    missing = [k for k in _REQUIRED_KEYS if k not in doc]
    if missing:
        raise ProvisioningError("validate_config", f"openclaw.json missing keys: {missing}")


_DEFAULT_SKILL_SOURCE = "vendor"


def _skill_source(skill_name: str) -> str:
    """Return "custom" if the skill is in CUSTOM_SKILL_NAMES, else "vendor"."""
    return "custom" if skill_name in CUSTOM_SKILL_NAMES else _DEFAULT_SKILL_SOURCE


def render_skills_yaml(skill_list: list[str]) -> str:
    """Structured write of skills.yaml from P1's skill_list (list[str]).

    Each entry becomes {name, source}; source defaults to 'vendor' (the
    openclaw-mvp default). Blank entries are skipped (defensive — P1's
    dedup_skills should have already cleaned these)."""
    entries = []
    for raw in skill_list:
        name = raw.strip() if isinstance(raw, str) else ""
        if not name:
            continue
        entries.append({"name": name, "source": _skill_source(name)})
    return yaml.safe_dump(
        {"skills": entries},
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    )


def generate_openclaw_token() -> str:
    """A fresh random gateway token for the per-agent .env (spec §6.4)."""
    return secrets.token_urlsafe(32)


def _sh_single_quote(value: str) -> str:
    """Wrap `value` in single quotes for sh `source`/`.`. A single quote
    inside the value becomes '\\'' (close-quote, escaped-quote, re-open)."""
    return "'" + value.replace("'", "'\\''") + "'"


# Public alias for credential services (feishu, gbrain, etc.)
sh_single_quote = _sh_single_quote


def render_env_file(
    *,
    openclaw_token: str,
    deepseek_api_key: str,
    pexels_api_key: str = "",
    tavily_api_key: str = "",
    xiaomi_mimo_api_key: str = "",
    feishu_webhook_url: str = "",
    agent_id: str,  # noqa: ARG001 — reserved for P3 Feishu credential support
    feishu_env_lines: str = "",  # P3: carried forward from existing .env
    tier1_env_lines: str = "",  # P4: DL_AGENT_DB_DSN, REDIS_KEY_PREFIX, LLM_BASE_URL
    cognee_env_lines: str = "",  # P5: carried forward from existing .env or empty
    cognee_internal_token: str = "",  # P5: fresh token minted at provision time
    gbrain_env_lines: str = "",  # P9: carried forward from existing .env
    comfyui_url: str = "",  # P10: ComfyUI remote URL (e.g. http://192.168.10.70:8188)
) -> str:
    """Generate per-agent .env content (spec §6.4). Every value is
    single-quoted so `sh source`/`. ` cannot misinterpret it. P3: existing
    FEISHU_APP_ID_* / FEISHU_APP_SECRET_* lines are carried forward via
    feishu_env_lines during restart. P4: Tier 1 lines carried forward likewise.
    P5: dl-cognee vars from carry-forward or a freshly-minted token."""
    result = (
        f"OPENCLAW_TOKEN={_sh_single_quote(openclaw_token)}\n"
        f"DEEPSEEK_API_KEY={_sh_single_quote(deepseek_api_key)}\n"
        "TZ=Asia/Shanghai\n"
        + feishu_env_lines
        + tier1_env_lines
        + cognee_env_lines
    )
    if pexels_api_key:
        result += f"PEXELS_API_KEY={_sh_single_quote(pexels_api_key)}\n"
    if tavily_api_key:
        result += f"TAVILY_API_KEY={_sh_single_quote(tavily_api_key)}\n"
    if xiaomi_mimo_api_key:
        result += f"XIAOMI_MIMO_API_KEY={_sh_single_quote(xiaomi_mimo_api_key)}\n"
    if feishu_webhook_url:
        result += f"FEISHU_WEBHOOK_URL={_sh_single_quote(feishu_webhook_url)}\n"
    if cognee_internal_token:
        result += (
            f"DL_INTERNAL_TOKEN={_sh_single_quote(cognee_internal_token)}\n"
            f"DL_COGNEE_URL=http://dl-cognee:8080\n"
        )
    if gbrain_env_lines:
        result += gbrain_env_lines
    if comfyui_url:
        result += f"COMFYUI_URL={_sh_single_quote(comfyui_url)}\n"
    # P13d: task-receiver agent command — ensure --session-id is set so the
    # OpenClaw CLI can route to an existing session rather than erroring.
    result += (
        "DATO_TASK_AGENT_CMD="
        + _sh_single_quote("openclaw agent --json --session-id dato --message {message}")
        + "\n"
    )
    return result


def regenerate_openclaw_json(cfg, row: dict) -> None:
    """Re-render openclaw.json from the current registry row, carrying
    forward the OpenClaw-owned `meta` block from the existing file.

    This is a public helper for apply_seed (P8) and mirrors the pattern
    used by save_feishu_credentials at
    dl_control/channels/feishu/credentials_service.py:107-121.
    """
    from pathlib import Path

    from dl_control.agents.provisioning.fs_safety import (
        atomic_write_with_fsync,
        read_managed_text,
    )

    agents_root = Path(cfg.agents_root)
    agent_dir = agents_root / row["id"]
    openclaw_path = agent_dir / "openclaw.json"
    existing = None
    if openclaw_path.exists():
        try:
            existing = read_managed_text(openclaw_path, agent_dir=agent_dir)
        except Exception:
            existing = None
    new_config = render_openclaw_json(
        cfg.templates_root,
        row,
        site_host=cfg.site_host,
        existing_json=existing,
        default_model=cfg.local_llm_default_model,
    )
    atomic_write_with_fsync(
        openclaw_path,
        new_config,
        mode=0o644,
        agent_dir=agent_dir,
    )
