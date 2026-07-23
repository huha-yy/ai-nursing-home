#!/bin/sh
# Pre-configure OpenClaw for DeepSeek — runs on first agent boot
# Idempotent: skips if already configured

MARKER="/home/node/.openclaw/.deepseek-configured"
if [ -f "$MARKER" ]; then
    echo "[setup-deepseek] Already configured, skipping."
    exit 0
fi

CONFIG="/home/node/.openclaw/openclaw.json"
AGENT_DIR="/home/node/.openclaw/agents/main/agent"
mkdir -p "$AGENT_DIR"

# Wait for openclaw.json to be created by the gateway startup
for i in 1 2 3 4 5 6 7 8 9 10; do
    if [ -f "$CONFIG" ]; then break; fi
    sleep 3
done

# Create default config if not exists
if [ ! -f "$CONFIG" ]; then
    echo '{"gateway":{"auth":{"mode":"token"}},"meta":{"lastTouchedVersion":"2026.4.8"}}' > "$CONFIG"
fi

# Set default model to DeepSeek via Python (JSON manipulation)
python3 -c "
import json, os

# 1. Auth profiles
auth = {'openai': {'apiKey': os.environ.get('DEEPSEEK_API_KEY',''), 'baseUrl': 'https://api.deepseek.com'}}
with open('$AGENT_DIR/auth-profiles.json', 'w') as f: json.dump(auth, f)

# 2. Models
models = {'providers': {'openai': {'baseUrl': 'https://api.deepseek.com', 'api': 'openai-completions', 'apiKey': 'DEEPSEEK_API_KEY', 'models': [{'id': 'deepseek-v4-flash', 'name': 'DeepSeek V4 Flash', 'reasoning': False, 'input': ['text'], 'contextWindow': 131072, 'maxTokens': 8192, 'compat': {'supportsUsageInStreaming': True}, 'api': 'openai-completions'}]}}}
with open('$AGENT_DIR/models.json', 'w') as f: json.dump(models, f, indent=2)

# 3. Default primary model
with open('$CONFIG') as f: cfg = json.load(f)
cfg['agents'] = {'defaults': {'model': {'primary': 'openai/deepseek-v4-flash'}}}
with open('$CONFIG', 'w') as f: json.dump(cfg, f, indent=2)

print('[setup-deepseek] Configuration written')
" 2>/dev/null && touch "$MARKER" && echo "[setup-deepseek] Done"

exit 0
