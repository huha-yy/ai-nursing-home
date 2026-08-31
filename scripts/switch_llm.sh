#!/usr/bin/env bash
# switch_llm.sh local|kimi — 一键切换 LLM 供应商（2026-08-31 本地切换时建）。
#
# 覆盖四站点矩阵 + ERP 侧，回切 ≈ 10 分钟（含 dato-control 重建与 agent 重启）：
#   1. infra/.env 三值            4. agent 三件套 JSON（models/auth-profiles/openclaw）
#   2. agent config/.env ×15（LLM_* + OPENAI_* 双写）   5. docker restart agents
#   3. ERP .env 三值（nursing-erp）                      6. dato-control force-recreate + ERP runserver 重启提示
#
# 关键事实（改动前先读）：
# - marker 状态机：容器内镜像是旧 setup-deepseek.sh，靠 .deepseek-configured marker
#   挡住；本脚本直写 bind-mount 的三件套 JSON，不碰 marker。
# - openclaw env-marker 白名单只认 OPENAI_API_KEY 名字（所以 .env 双写）。
# - 本地 vLLM（dato-vision.service）服务端已强制 no-think 模板 + --max-model-len 32768，
#   因此 local profile 的 models.json 写 reasoning:false / contextWindow:32768。
# - kimi 回切前提：Moonshot 账户已充值（2026-08-31 曾因欠费 429 停机）。
set -euo pipefail

PROFILE="${1:-}"
AGENTS_ROOT="$HOME/.local/share/dato/agents"
INFRA_ENV="$(dirname "$0")/../infra/.env"
KIMI_BAK="$(dirname "$0")/../infra/.env.kimi.bak-20260831"
ERP_ENV="/home/nursing-home/huha-project/nursing-erp/.env"
ERP_KIMI_BAK="/home/nursing-home/huha-project/nursing-erp/.env.kimi.bak-20260831"

case "$PROFILE" in
  local)
    KEY=$(grep VISION_API_KEY ~/.config/dato/vision.env | cut -d= -f2)
    BASE="http://192.168.10.247:8000/v1"
    MODEL="ocicek/Qwen3.6-27B-NVFP4"
    CTX=32768; REASONING=false
    # 前置：本地 vLLM 必须在跑（平时可能 inactive）
    systemctl --user is-active --quiet dato-vision || {
      echo " dato-vision 未运行，正在启动…"; systemctl --user start dato-vision; }
    ;;
  kimi)
    [[ -f "$KIMI_BAK" ]] || { echo "找不到 $KIMI_BAK"; exit 1; }
    KEY=$(grep '^LLM_API_KEY=' "$KIMI_BAK" | head -1 | cut -d= -f2-)
    BASE=$(grep '^LLM_BASE_URL=' "$KIMI_BAK" | head -1 | cut -d= -f2-)
    MODEL=$(grep '^LLM_MODEL=' "$KIMI_BAK" | head -1 | cut -d= -f2-)
    CTX=131072; REASONING=true
    ;;
  *) echo "用法: $0 local|kimi"; exit 1 ;;
esac

echo "==> 切到 $PROFILE: model=$MODEL base=${BASE%%/*}/…（key 不打印）"

# ── 1. infra/.env ──
cp "$INFRA_ENV" "$INFRA_ENV.pre-switch.bak"
sed -i -e "s|^LLM_API_KEY=.*|LLM_API_KEY=$KEY|" \
       -e "s|^LLM_BASE_URL=.*|LLM_BASE_URL=$BASE|" \
       -e "s|^LLM_MODEL=.*|LLM_MODEL=$MODEL|" "$INFRA_ENV"

# ── 2. agent .env ×15（双写两套变量名）──
for f in "$AGENTS_ROOT"/*/config/.env; do
  sed -i -e "s|^LLM_API_KEY=.*|LLM_API_KEY='$KEY'|" \
         -e "s|^LLM_BASE_URL=.*|LLM_BASE_URL='$BASE'|" \
         -e "s|^LLM_MODEL=.*|LLM_MODEL='$MODEL'|" \
         -e "s|^OPENAI_API_KEY=.*|OPENAI_API_KEY='$KEY'|" \
         -e "s|^OPENAI_BASE_URL=.*|OPENAI_BASE_URL='$BASE'|" "$f"
done
echo "    agent .env ×$(ls -d "$AGENTS_ROOT"/*/config/.env 2>/dev/null | wc -l) 已改"

# ── 3+4. agent 三件套 JSON（宿主机直写 bind-mount，不碰 marker）──
KEY="$KEY" BASE="$BASE" MODEL="$MODEL" CTX="$CTX" REASONING="$REASONING" \
python3 - <<'PYEOF'
import json, os, shutil
key, base, model = os.environ['KEY'], os.environ['BASE'], os.environ['MODEL']
ctx, reasoning = int(os.environ['CTX']), os.environ['REASONING'] == 'true'
root = os.path.expanduser('~/.local/share/dato/agents')
n = 0
for uuid in sorted(os.listdir(root)):
    a = os.path.join(root, uuid)
    adir, cfgp = os.path.join(a, 'agents/main/agent'), os.path.join(a, 'openclaw.json')
    if not (os.path.isdir(adir) and os.path.exists(cfgp)):
        continue
    with open(os.path.join(adir, 'auth-profiles.json'), 'w') as f:
        json.dump({'version': 1, 'profiles': {'openai:default': {
            'type': 'api_key', 'provider': 'openai', 'apiKey': key, 'baseUrl': base}}}, f)
    with open(os.path.join(adir, 'models.json'), 'w') as f:
        json.dump({'providers': {'openai': {
            'baseUrl': base, 'api': 'openai-completions', 'apiKey': 'OPENAI_API_KEY',
            'models': [{'id': model, 'name': model, 'reasoning': reasoning, 'input': ['text'],
                        'contextWindow': ctx, 'maxTokens': 8192,
                        'compat': {'supportsUsageInStreaming': True},
                        'api': 'openai-completions'}]}}}, f, indent=2)
    shutil.copy2(cfgp, cfgp + '.bak-switch')
    cfg = json.load(open(cfgp))
    cfg.setdefault('models', {})['mode'] = 'merge'
    cfg['models']['providers'] = {'openai': {
        'baseUrl': base, 'apiKey': '${OPENAI_API_KEY}', 'api': 'openai-completions',
        'models': [{'id': model, 'name': model, 'contextWindow': ctx, 'maxTokens': 8192}]}}
    cfg.setdefault('agents', {}).setdefault('defaults', {}).setdefault('model', {})
    cfg['agents']['defaults']['model']['primary'] = f'openai/{model}'
    with open(cfgp, 'w') as f:
        json.dump(cfg, f, indent=2)
    n += 1
print(f'    三件套 JSON ×{n} 已写')
PYEOF

# ── 5. 重启 agent 容器（wrapper 会重读 .env）──
docker restart $(docker ps --format '{{.Names}}' | grep dato-agent) >/dev/null
echo "    agent 容器已重启"

# ── 6. dato-control 重建 ──
cd "$(dirname "$0")/../infra"
docker compose --project-name dato --project-directory . --env-file .env \
  up -d --force-recreate --build dato-control >/dev/null
echo "    dato-control 已重建"

# ── ERP 侧（存在才处理）──
if [[ -f "$ERP_ENV" ]]; then
  sed -i -e "s|^LLM_API_KEY=.*|LLM_API_KEY=$KEY|" \
         -e "s|^LLM_BASE_URL=.*|LLM_BASE_URL=$BASE/chat/completions|" \
         -e "s|^LLM_MODEL=.*|LLM_MODEL=$MODEL|" "$ERP_ENV"
  echo "==> ERP .env 已切（kimi 原值: $ERP_KIMI_BAK）"
  echo "    ⚠ ERP runserver 不会热载 .env（settings 用 setdefault，父进程环境会钉住旧值）——需整体重启："
  echo "      pkill -f 'manage.py runserver'; cd /home/nursing-home/huha-project/nursing-erp && nohup .venv/bin/python manage.py runserver 0.0.0.0:8765 >> logs/runserver.log 2>&1 &"
fi

echo "==> 完成。验证：chat.eldcare.cn 发一句 + ERP 菜单 OCR 各跑一次。"
