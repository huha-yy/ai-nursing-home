# Precreated agents

Each subdirectory is a seed agent definition shipped inside the `dl-control` image.

- `agent.yaml` (required): agent identity + defaults (display_name, tier, admin_only, skill_list, channel_config, model_selection).
- `workspace/` (optional): per-file workspace overrides. Files present here override the global `templates/workspace/*.md` templates; missing files fall back to the global template.

See `docs/runbooks/foundation.md` §Precreated agents for operation.
