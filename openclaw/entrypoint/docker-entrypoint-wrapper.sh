#!/bin/sh
set -e

# Re-source mounted .env so `docker restart` picks up changes
# without needing a full `docker compose up --force-recreate`.
ENV_FILE=/app/config/.env
if [ -f "$ENV_FILE" ]; then
  set -a
  . "$ENV_FILE"
  set +a
  # Symlink so Python dotenv (load_dotenv) can find it at /app/.env
  ln -sf "$ENV_FILE" /app/.env 2>/dev/null || true
fi

/usr/local/bin/setup-deepseek.sh 2>/dev/null || true
# Map suffixed Feishu credentials (e.g. FEISHU_APP_ID_AGENT1) to the
# unsuffixed names that send_image.py and push_to_feishu.py expect.
# Priority: _AGENT* first, then _DN*, then _XIAODAI, then bare value.
if [ -n "${FEISHU_APP_ID_AGENT1:-}" ]; then
  export FEISHU_APP_ID="$FEISHU_APP_ID_AGENT1"
  export FEISHU_APP_SECRET="$FEISHU_APP_SECRET_AGENT1"
elif [ -n "${FEISHU_APP_ID_AGENT2:-}" ]; then
  export FEISHU_APP_ID="$FEISHU_APP_ID_AGENT2"
  export FEISHU_APP_SECRET="$FEISHU_APP_SECRET_AGENT2"
elif [ -n "${FEISHU_APP_ID_AGENT3:-}" ]; then
  export FEISHU_APP_ID="$FEISHU_APP_ID_AGENT3"
  export FEISHU_APP_SECRET="$FEISHU_APP_SECRET_AGENT3"
elif [ -n "${FEISHU_APP_ID_AGENT4:-}" ]; then
  export FEISHU_APP_ID="$FEISHU_APP_ID_AGENT4"
  export FEISHU_APP_SECRET="$FEISHU_APP_SECRET_AGENT4"
elif [ -n "${FEISHU_APP_ID_DN1:-}" ]; then
  export FEISHU_APP_ID="$FEISHU_APP_ID_DN1"
  export FEISHU_APP_SECRET="$FEISHU_APP_SECRET_DN1"
elif [ -n "${FEISHU_APP_ID_DN2:-}" ]; then
  export FEISHU_APP_ID="$FEISHU_APP_ID_DN2"
  export FEISHU_APP_SECRET="$FEISHU_APP_SECRET_DN2"
elif [ -n "${FEISHU_APP_ID_DN3:-}" ]; then
  export FEISHU_APP_ID="$FEISHU_APP_ID_DN3"
  export FEISHU_APP_SECRET="$FEISHU_APP_SECRET_DN3"
elif [ -n "${FEISHU_APP_ID_DN4:-}" ]; then
  export FEISHU_APP_ID="$FEISHU_APP_ID_DN4"
  export FEISHU_APP_SECRET="$FEISHU_APP_SECRET_DN4"
elif [ -n "${FEISHU_APP_ID_XIAODAI:-}" ]; then
  export FEISHU_APP_ID="$FEISHU_APP_ID_XIAODAI"
  export FEISHU_APP_SECRET="$FEISHU_APP_SECRET_XIAODAI"
fi

# Content pipeline: symlink skills and configs into workspace so that
# SKILL.md relative paths (e.g. "skills/article-composer/scripts/...")
# resolve correctly from the agent's working directory.
WORKSPACE="${OPENCLAW_WORKSPACE_DIR:-/home/node/.openclaw/workspace}"
if [ -d "$WORKSPACE" ]; then
  ln -sfn /opt/openclaw/skills/custom "$WORKSPACE/skills" 2>/dev/null || true
  ln -sfn /opt/openclaw/configs "$WORKSPACE/configs" 2>/dev/null || true

  # Multi-brand: overlay brand-specific config files that SKILL.md reads
  # via unprefixed paths (configs/brand_guidelines.md, company_keywords.yaml).
  # Brand assets (brand_images.yaml + brand_assets/) are resolved at runtime by
  # insert_images.py and other scripts via configs/<brand>/ subdirectory paths
  # — no overlay needed, removed because it breaks multi-brand runtime.
  _BRAND="${BRAND:-daien}"
  if [ "$_BRAND" != "daien" ] || [ ! -f "$WORKSPACE/configs/brand_guidelines.md" ]; then
    for _f in brand_guidelines.md company_keywords.yaml; do
      if [ -e "/opt/openclaw/configs/$_BRAND/$_f" ]; then
        ln -sfn "/opt/openclaw/configs/$_BRAND/$_f" "$WORKSPACE/configs/$_f" 2>/dev/null || true
      fi
    done
  fi
  unset _BRAND _f
  # Scripts use load_dotenv() with varying levels of `..` from __file__.
  # Symlink /app/config/.env to both common lookup paths so that scripts
  # (push_to_feishu.py, feishu_auth.py, vision-ocr/handler.py etc.) can
  # find credentials regardless of their module depth.
  if [ -f "$ENV_FILE" ]; then
    ln -sf "$ENV_FILE" /opt/openclaw/.env 2>/dev/null || true
    ln -sf "$ENV_FILE" /opt/openclaw/skills/.env 2>/dev/null || true
  fi
fi

# P13d: task-receiver sidecar — workflow→agent dispatch intake (spec §7).
# Requires the per-agent DL_INTERNAL_TOKEN (minted at provision); without it
# there is nothing to authenticate, so no receiver is started.
if [ -n "${DL_INTERNAL_TOKEN:-}" ]; then
  python3 /usr/local/bin/dato_task_receiver.py &
fi

# Auto-send images sidecar: watches /tmp/ for new images and sends them
# to the configured Feishu chat. Requires FEISHU_APP_ID + AUTO_SEND_CHAT_ID.
if [ -n "${FEISHU_APP_ID:-}" ] && [ -n "${AUTO_SEND_CHAT_ID:-}" ]; then
  python3 /opt/openclaw/scripts/auto_send_image.py &
fi

exec docker-entrypoint.sh "$@"
