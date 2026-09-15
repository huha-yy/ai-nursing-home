#!/usr/bin/env bash
# 演示当天一键自检（2026-09-07）：数据新鲜度 + 服务健康 + LLM 连通 + chat 探针。
# 演示前 10 分钟在一体机/开发机上跑：bash scripts/demo_day_check.sh [--quick]
#   --quick  跳过 chat 探针（只查数据与服务，~30s）
#   LANG=en 英文演示口径（P5）：演示位人名/探针问句/期望串切英文
#           （只认精确 "en"；en_US.UTF-8 等本地化值按 zh 处理）
# 退出码 0=全绿可演示；1=有红项（先照报告修再演示）。
#
# 数据口径（对照 DASHBOARD-DATA.md / JOURNAL 09-07）：
#   - 告警/工单/活动/周报 各自的"最近日期"距今天 >1 天即黄，>3 天即红
#   - 演示位：张国栋/李秀兰未来有效订单必须为 0（--demo-free 留白，供现场点餐）
#   - chat 探针：三角色各 2-3 问，答非空且不含"暂时不可用"即绿

set -u
QUICK=0
[ "${1:-}" = "--quick" ] && QUICK=1
# P5 双语：LANG=en 环境变量切换英文演示口径（人名/探针断言用 en 串）；
# 环境里 LANG 常见 en_US.UTF-8 等本地化值——只认精确的 "en"，其余一律 zh
DLANG="zh"
[ "${LANG:-}" = "en" ] && DLANG="en"

RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; NC=$'\033[0m'
PASS=0; FAIL=0; WARN=0

ok()   { echo "  ${GREEN}✓${NC} $1"; PASS=$((PASS+1)); }
bad()  { echo "  ${RED}✗ $1${NC}"; FAIL=$((FAIL+1)); }
warn() { echo "${YELLOW}△ $1${NC}"; WARN=$((WARN+1)); }

# ── 路径与密钥（只在本机读，不回显） ─────────────────────────────
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ERP_ENV=/home/nursing-home/huha-project/nursing-erp/.env
ERP_KEY=""
[ -f "$ERP_ENV" ] && ERP_KEY=$(grep '^ERP_API_KEY=' "$ERP_ENV" | cut -d= -f2-)
INFRA_ENV="$ROOT/infra/.env"
LLM_KEY=$(grep '^LLM_API_KEY=' "$INFRA_ENV" 2>/dev/null | cut -d= -f2-)
LLM_BASE=$(grep '^LLM_BASE_URL=' "$INFRA_ENV" 2>/dev/null | cut -d= -f2-)
LLM_MODEL=$(grep '^LLM_MODEL=' "$INFRA_ENV" 2>/dev/null | cut -d= -f2-)

CTRL=http://127.0.0.1:9080
ERP=http://127.0.0.1:8765
TODAY=$(date +%F)

section() { echo; echo "== $1 =="; }

# ── 1. 服务健康 ─────────────────────────────────────────────────
section "服务健康"
for spec in "dato-control:$CTRL/api/health" ; do
  name=${spec%%:*}; url=${spec#*:}
  code=$(curl -s -m 8 -o /dev/null -w '%{http_code}' "$url" 2>/dev/null)
  [ "$code" = "200" ] && ok "$name 健康端点 200" || bad "$name 健康端点 $code（docker ps | grep dato-control）"
done
code=$(curl -s -m 8 -o /dev/null -w '%{http_code}' "$ERP/admin/login/" 2>/dev/null)
[ "$code" = "200" ] && ok "nursing-erp 登录页 200" || bad "nursing-erp 登录页 $code（systemctl --user status nursing-erp）"
# ERP API 必须拒匿名（认证在位是安全红线，2026-08-21 加固）
code=$(curl -s -m 8 -o /dev/null -w '%{http_code}' "$ERP/api/residents/" 2>/dev/null)
[ "$code" = "401" ] && ok "ERP API 匿名 401（认证在位）" || bad "ERP API 匿名返回 $code（应为 401！）"
down=$(docker ps --filter name=dato-agent- --filter status=running -q | wc -l)
[ "$down" -ge 10 ] && ok "agent 容器 ${down} 个在跑" || warn "agent 容器只有 ${down} 个在跑（应为 10+，docker ps -a | grep dato-agent）"

# ── 2. LLM 连通（云端供应商余额/网络） ──────────────────────────
section "LLM 连通（$LLM_MODEL）"
if [ -n "$LLM_KEY" ] && [ -n "$LLM_BASE" ]; then
  t0=$(date +%s)
  llm_out=$(curl -s -m 30 "$LLM_BASE/chat/completions" \
    -H "Authorization: Bearer $LLM_KEY" -H 'Content-Type: application/json' \
    -d "{\"model\":\"$LLM_MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"回复ok\"}],\"max_tokens\":50,\"thinking\":{\"type\":\"disabled\"}}" 2>/dev/null)
  t1=$(date +%s)
  if echo "$llm_out" | python3 -c "import sys,json;d=json.load(sys.stdin);assert d['choices'][0]['message']['content'].strip()" 2>/dev/null; then
    ok "LLM 应答正常（$((t1-t0))s）"
  else
    err=$(echo "$llm_out" | head -c 120)
    case "$err" in *quota*|*429*) bad "LLM 欠费/限流（$err）→ 充值或 bash scripts/switch_llm.sh local";;
      *) bad "LLM 无应答（$err）→ 查 clash 代理: systemctl --user status clash";; esac
  fi
else
  bad "infra/.env 缺 LLM_API_KEY/LLM_BASE_URL"
fi

# ── 3. 数据新鲜度 ───────────────────────────────────────────────
section "数据新鲜度（今天 $TODAY）"
days_ago() { python3 -c "from datetime import date;import sys;print((date.today()-date.fromisoformat(sys.argv[1])).days)" "$1"; }
ago() { if [ "$1" -lt 0 ]; then echo "未来$((-$1))天"; else echo "$1天前"; fi; }

if [ -n "$ERP_KEY" ]; then
  # 未处理告警：最新一条距今
  latest=$(curl -s -m 10 -H "X-API-Key: $ERP_KEY" "$ERP/api/incidents/?handled=false" | python3 -c "import sys,json;d=json.load(sys.stdin);items=d.get('items',d);print(max((i['created_at'][:10] for i in items),default='') if items else '')" 2>/dev/null)
  if [ -n "$latest" ]; then
    n=$(days_ago "$latest")
    if [ "$n" -le 1 ]; then ok "未处理告警最新 $latest（$(ago "$n")）"
    elif [ "$n" -le 3 ]; then warn "告警最新 $latest 已 ${n} 天——演示前重锚（restore_demo.sh / seed-incidents-extra）"
    else bad "告警最新 $latest 已 ${n} 天，演示会显得陈旧"; fi
  else bad "未处理告警为 0（演示撑不起告警页）"; fi

  # 今日菜单 + 今日订单量（week-menu 必须带 week_start=本周一，无参返回 []）
  MON=$(python3 -c "from datetime import date,timedelta;t=date.today();print(t-timedelta(days=t.weekday()))")
  menu_n=$(curl -s -m 10 -H "X-API-Key: $ERP_KEY" "$ERP/api/week-menu/?week_start=$MON" | python3 -c "import sys,json;d=json.load(sys.stdin);items=d.get('items') if isinstance(d,dict) else d;print(len(items or []))" 2>/dev/null)
  [ "${menu_n:-0}" -ge 21 ] && ok "本周菜单 ${menu_n} 行" || bad "本周菜单只有 ${menu_n:-0} 行（应为 21+）→ --seed-menus-ahead"
  orders_n=$(curl -s -m 10 -H "X-API-Key: $ERP_KEY" "$ERP/api/meal-orders/?date=$TODAY&meal_type=午餐" | python3 -c "import sys,json;d=json.load(sys.stdin);items=d.get('items',d);print(len([i for i in items if i.get('status')!='cancelled']))" 2>/dev/null)
  [ "${orders_n:-0}" -ge 20 ] && ok "今日午餐有效订单 ${orders_n} 份" || warn "今日午餐仅 ${orders_n:-0} 份（<20，食堂看板会稀）→ 重锚 rebuild_demo_data"

  # 演示位：张国栋/李秀兰未来有效订单必须 0（P5：LANG=en 断言英文名）
  if [ "$DLANG" = "en" ]; then COUPLE="Zhang Guodong|Li Xiulan"; else COUPLE="张国栋|李秀兰"; fi
  demo_free=$(curl -s -m 10 -H "X-API-Key: $ERP_KEY" "$ERP/api/meal-orders/?date=$(date -d tomorrow +%F)&meal_type=午餐&page_size=50" | COUPLE="$COUPLE" python3 -c "
import os,sys,json
names=set(os.environ['COUPLE'].split('|'))
d=json.load(sys.stdin);items=d.get('items',d)
print(len([i for i in items if i.get('resident_name') in names and i.get('status')!='cancelled']))" 2>/dev/null)
  [ "${demo_free:-9}" = "0" ] && ok "演示位干净（${COUPLE//|//} 明天无有效订单）" || bad "演示位被占（明天午餐 ${demo_free} 单）→ restore_demo.sh 或退餐 API 清掉"
else
  bad "读不到 nursing-erp/.env 的 ERP_API_KEY"
fi

# PG 三表：工单 / 活动
pg_q() { docker exec dato-postgres sh -c "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -tAc \"$1\"" 2>/dev/null; }
for t in nursing_work_orders nursing_activities; do
  latest=$(pg_q "SELECT MAX(date) FROM $t" | tr -d ' ')
  if [ -n "$latest" ]; then
    n=$(days_ago "$latest")
    if [ "$n" -le 1 ]; then ok "$t 最新 $latest（$(ago "$n")）"
    elif [ "$n" -le 3 ]; then warn "$t 最新 $latest 已 ${n} 天"
    else bad "$t 最新 $latest 已 ${n} 天（工单→python3 scripts/seed_work_orders_demo.py；活动→活动表续期）"; fi
  else bad "$t 查不到（dato-postgres 容器或表缺失）"; fi
done

# 周报最新期次
rpt=$(pg_q "SELECT MAX(created_at)::date FROM workflow_run WHERE workflow_id='nursing.ops' AND status='succeeded'" | tr -d ' ')
if [ -n "$rpt" ]; then
  n=$(days_ago "$rpt")
  if [ "$n" -le 1 ]; then ok "周报最新期次 $rpt（$(ago "$n")）"
  elif [ "$n" -le 3 ]; then warn "周报最新期次 $rpt 已 ${n} 天——演示当天让院长在 chat 里说「生成周报」现场触发"
  else warn "周报最新期次 $rpt 已 ${n} 天（现场触发一条即可，~4 分钟出）"; fi
else bad "周报无成功期次——演示前必须触发一次 nursing.ops"; fi

# ── 4. chat 探针（三角色） ──────────────────────────────────────
if [ "$QUICK" = "0" ]; then
section "chat 探针（每问 5-20s，共 7 问；LANG=$DLANG）"
CJ=$(mktemp); FJ=$(mktemp); BJ=$(mktemp)
curl -s -c "$CJ" -o /dev/null "$CTRL/auth/nursing-login" -d 'username=wang_jianguo&password=123456'
curl -s -c "$FJ" -o /dev/null "$CTRL/auth/family-login" -d 'username=13820000001&password=123456'
curl -s -c "$BJ" -o /dev/null "$CTRL/auth/nursing-login" -d 'username=b1_liu&password=123456'

ask() { # $1=cookie $2=问句 -> stdout 答复全文
  curl -s -N -m 90 "$CTRL/api/nursing/chat/stream" -b "$1" \
    -H 'Content-Type: application/json' -d "{\"message\":\"$2\",\"chat_id\":\"probe-$$\"}" 2>/dev/null | python3 -c "
import sys,json
buf=[]
for line in sys.stdin:
    if line.startswith('data:'):
        try:
            ev=json.loads(line[5:].strip())
            if ev.get('type')=='delta': buf.append(ev['content'])
        except Exception: pass
print(''.join(buf))"
}
probe() { # $1=cookie $2=问句 $3=期望包含的子串(任一,|分隔)
  out=$(ask "$1" "$2")
  case "$out" in
    *"暂时不可用"*|"") bad "「$2」→ 空答/不可用";;
    *) echo "$out" | grep -qE "$3" && ok "「$2」" || warn "「$2」→ 答复未见 $3（人工看一眼：${out:0:60}…）";; esac
}
# P5：探针问句/期望串双语（en 演示前 ERP 需 --lang en 重灌、PG 侧
# seed_work_orders_demo.py --lang en + seed_pg_demo_en.py 重播）
if [ "$DLANG" = "en" ]; then
  probe "$CJ" "How many residents live here" "36|[0-9]+ residents"
  probe "$CJ" "Are there any vacant beds" "bed|occupancy|[0-9]+/[0-9]+"
  probe "$CJ" "Who is in arrears this month" "Wu Guiying|arrears|unpaid"
  probe "$BJ" "Did anyone fall recently" "fall|alert|[Nn]o "
  probe "$BJ" "Show the schedule for the last 3 days" "shift|schedule|Day|Night|白班|夜班"
  probe "$FJ" "How were the meals this week" "meal|menu|food|reakfast|unch|inner"
  probe "$CJ" "Any activities today" "Baduanjin|Chess|Health Talk|Handcraft|activit"
else
  probe "$CJ" "院里住了多少人" "36|人"
  probe "$CJ" "还有床位吗" "床|入住率"
  probe "$CJ" "这个月谁欠费" "吴桂英|欠费|元"
  probe "$BJ" "有老人摔倒吗" "摔倒|预警|没有"
  probe "$BJ" "最近三天排班情况" "排班|白班|夜班"
  probe "$FJ" "这周吃饭情况" "餐|吃饭|菜单"
  probe "$CJ" "今天有什么活动" "活动|:.*-"
fi
# 清探针会话
for f in "$CJ" "$FJ" "$BJ"; do
  curl -s -b "$f" -o /dev/null "$CTRL/api/nursing/chats"; rm -f "$f"; done
else
  echo; echo "（--quick：跳过 chat 探针）"
fi

# ── 汇总 ────────────────────────────────────────────────────────
echo; echo "== 汇总：${GREEN}$PASS 绿${NC} / ${YELLOW}$WARN 黄${NC} / ${RED}$FAIL 红${NC} =="
if [ "$FAIL" -gt 0 ]; then echo "${RED}有红项，先修复再演示${NC}"; exit 1; fi
[ "$WARN" -gt 0 ] && echo "${YELLOW}有黄项，不拦演示但建议处理${NC}"
echo "${GREEN}可以演示${NC}"
