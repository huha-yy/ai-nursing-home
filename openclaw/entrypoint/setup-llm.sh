#!/bin/sh
# Pre-configure OpenClaw for the vendor LLM (Moonshot/Kimi since 2026-08; was
# DeepSeek) — runs on first agent boot. Idempotent: skips if already
# configured. Also runs once on agents previously set up by setup-deepseek.sh
# (its marker counts as "not yet migrated").
#
# Auth layout (verified against /app/dist/model-auth-markers-*.js):
#   - The models.json apiKey marker must be a name openclaw recognizes.
#     "OPENAI_API_KEY" is accepted (builtin openai provider env var);
#     "LLM_API_KEY" is NOT (isKnownEnvApiKeyMarker rejects it) — do not use.
#   - auth-profiles.json must be the modern store format {version, profiles};
#     the flat {provider: {...}} shape is rejected by
#     coercePersistedAuthProfileStore.
#   - openclaw.json models.providers MERGES over the agent's models.json
#     ("mode": "merge"), so a stale deepseek block there overrides our config.
#     Migrate it to the vendor block too.

MARKER="/home/node/.openclaw/.llm-configured"
LEGACY_MARKER="/home/node/.openclaw/.deepseek-configured"
if [ -f "$MARKER" ]; then
    echo "[setup-llm] Already configured, skipping."
    exit 0
fi

# Vendor LLM settings come from the agent's config/.env (sourced by the
# entrypoint wrapper before this script runs).
KEY="${LLM_API_KEY:-${DEEPSEEK_API_KEY:-}}"
BASE="${LLM_BASE_URL:-https://api.moonshot.cn/v1}"
MODEL="${LLM_MODEL:-kimi-k2.6}"
if [ -z "$KEY" ]; then
    echo "[setup-llm] LLM_API_KEY not set; leaving config untouched."
    exit 0
fi

# MiniMax 专属走原生 minimax provider（anthropic-messages 端点）：openclaw 只在这
# 条路径上自动注入 thinking:disabled（proxy-stream-wrappers 的判定是
# api=anthropic-messages && provider=minimax）。走 openai-completions 的话
# MiniMax-M3 服务端自适应思考默认全开，chat 延迟 10s~90s 摆动（2026-09-03 定位）。
# models.json 的 env marker 只认 provider 声明的 MINIMAX_API_KEY。
case "$BASE" in
    https://api.minimaxi.com*|https://api.minimax.io*)
        ANTHROPIC_BASE="${BASE%/v1}/anthropic"
        export MINIMAX_API_KEY="${MINIMAX_API_KEY:-$KEY}"
        PROVIDER_KIND="minimax"
        ;;
    *)
        PROVIDER_KIND="openai"
        ;;
esac
export PROVIDER_KIND ANTHROPIC_BASE

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

# Write vendor model config via Python (JSON manipulation).
# NOTE: provider id stays "openai" (openclaw's built-in OpenAI-compatible id):
# the vendor (Moonshot) is selected purely via baseUrl.
export KEY BASE MODEL
python3 -c "
import json, os

key, base, model = os.environ['KEY'], os.environ['BASE'], os.environ['MODEL']
kind = os.environ['PROVIDER_KIND']

# 模型能力参数跟供应商走（2026-08-31 本地切换）：
# 本地 vLLM（http 非加密）服务端已挂 no-think 模板 + --max-model-len 32768，
# reasoning=False / contextWindow=32768；云端 kimi-k2.6 是思考模型、128K 窗口。
# MiniMax 走 anthropic-messages + thinking 自动禁用：models 条目绝不能带
# reasoning=True（那会把默认思考级提到 low，payload 带 thinking 后包装器不再覆盖）。
local_llm = not base.startswith('https://')
if kind == 'minimax':
    base = os.environ['ANTHROPIC_BASE']
    reasoning = False
else:
    reasoning = not local_llm
ctx_window = 32768 if local_llm else 131072

if kind == 'minimax':
    # 1. Auth profiles — modern store format (flat shape is rejected).
    auth = {'version': 1, 'profiles': {'minimax:default': {
        'type': 'api_key', 'provider': 'minimax',
        'apiKey': key, 'baseUrl': base}}}
    with open('$AGENT_DIR/auth-profiles.json', 'w') as f: json.dump(auth, f)
    # 2. Models — apiKey 持 env MARKER 'MINIMAX_API_KEY'（minimax provider 声明的
    # envVars，marker 白名单认可）。
    models = {'providers': {'minimax': {'baseUrl': base, 'api': 'anthropic-messages',
        'apiKey': 'MINIMAX_API_KEY', 'models': [{'id': model, 'name': model,
        'reasoning': reasoning, 'input': ['text'], 'contextWindow': ctx_window,
        'maxTokens': 8192}]}}}
    with open('$AGENT_DIR/models.json', 'w') as f: json.dump(models, f, indent=2)
    # 3. openclaw.json — primary model + providers 块换成 minimax。
    with open('$CONFIG') as f: cfg = json.load(f)
    cfg.setdefault('models', {})['mode'] = 'merge'
    cfg['models']['providers'] = {'minimax': {
        'baseUrl': base, 'apiKey': '\${MINIMAX_API_KEY}', 'api': 'anthropic-messages',
        'models': [{'id': model, 'name': model, 'contextWindow': ctx_window, 'maxTokens': 8192}]}}
    cfg.setdefault('agents', {}).setdefault('defaults', {}).setdefault('model', {})
    cfg['agents']['defaults']['model']['primary'] = 'minimax/' + model
    with open('$CONFIG', 'w') as f: json.dump(cfg, f, indent=2)
    print('[setup-llm] Configuration written (minimax/anthropic)')
else:
    # 1. Auth profiles — modern store format (flat shape is rejected).
    auth = {'version': 1, 'profiles': {'openai:default': {
        'type': 'api_key', 'provider': 'openai', 'apiKey': key, 'baseUrl': base}}}
    with open('$AGENT_DIR/auth-profiles.json', 'w') as f: json.dump(auth, f)

    # 2. Models — apiKey holds the env MARKER 'OPENAI_API_KEY' (an accepted
    # marker; 'LLM_API_KEY' is not), resolved from process.env at runtime.
    models = {'providers': {'openai': {'baseUrl': base, 'api': 'openai-completions', 'apiKey': 'OPENAI_API_KEY', 'models': [{'id': model, 'name': model, 'reasoning': reasoning, 'input': ['text'], 'contextWindow': ctx_window, 'maxTokens': 8192, 'compat': {'supportsUsageInStreaming': True}, 'api': 'openai-completions'}]}}}
    with open('$AGENT_DIR/models.json', 'w') as f: json.dump(models, f, indent=2)

    # 3. openclaw.json — primary model, and migrate any stale (deepseek-era)
    # providers block: 'mode: merge' makes it override the agent models.json.
    with open('$CONFIG') as f: cfg = json.load(f)
    cfg.setdefault('models', {})['mode'] = 'merge'
    cfg['models']['providers'] = {'openai': {
        'baseUrl': base, 'apiKey': '\${OPENAI_API_KEY}', 'api': 'openai-completions',
        'models': [{'id': model, 'name': model, 'contextWindow': ctx_window, 'maxTokens': 8192}]}}
    cfg.setdefault('agents', {}).setdefault('defaults', {}).setdefault('model', {})
    cfg['agents']['defaults']['model']['primary'] = 'openai/' + model
    with open('$CONFIG', 'w') as f: json.dump(cfg, f, indent=2)

    print('[setup-llm] Configuration written')
" 2>/dev/null && touch "$MARKER" && rm -f "$LEGACY_MARKER" && echo "[setup-llm] Done"

exit 0
