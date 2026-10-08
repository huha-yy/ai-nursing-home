#!/usr/bin/env bash
# 中英文演示态一键切换（2026-10-08，方案 B）—— 09-15 手工流程的脚本化。
#
# 做什么：
#   1. 校验参数（en|zh）+ 备份 ERP 库
#   2. 停 nursing-erp（systemd 托管；重灌拒绝与 runserver 并行，--force 过掉
#      huha-crm 误报）
#   3. ERP 全量重灌：--lang <lang> --cover-until（默认 2026-12-31）--demo-free 1,2
#      （张国栋/李秀兰留白）
#   4. PG 工单/活动·投诉双脚本同语言重播（单边切 = 楼长 400）
#   5. PG nursing_meals 从 ERP WeekMenu 重导（菜单语言必须跟着切）
#   6. 写语言标记 logs/demo_lang —— keepfresh cron 读它每日保鲜保持同语言；
#      UI/chat 语言 cookie 缺省也跟随它（dl-control i18n + ERP DemoLangDefaultMiddleware）
#   7. 起服务 + 验证
#
# 用法：
#   bash scripts/switch_demo_lang.sh en            # 切英文演示态
#   bash scripts/switch_demo_lang.sh zh            # 切回中文演示态
#   bash scripts/switch_demo_lang.sh en --until 2026-12-31
#   DRY_RUN=1 bash scripts/switch_demo_lang.sh en  # 只打印不执行
#
# 切换后注意：
#   - 楼长/院长需重新登录（权限缓存里楼栋名语言不同，不重登会 400）
#   - UI 语言自动跟随标记（无需点右上角）；只有浏览器里显式点过切换按钮
#     （有语言 cookie）的才需要手动点回来
#   - 实测耗时约 3-5 分钟
set -euo pipefail

AI_REPO="$(cd "$(dirname "$0")/.." && pwd)"
ERP_REPO="/home/nursing-home/huha-project/nursing-erp"
COVER_DEFAULT="2026-12-31"
DRY_RUN="${DRY_RUN:-0}"

LANG_ARG="${1:-}"
[ "$LANG_ARG" = "en" ] || [ "$LANG_ARG" = "zh" ] || {
  echo "用法：$0 en|zh [--until YYYY-MM-DD]"; exit 1; }
shift || true

COVER="$COVER_DEFAULT"
if [ "${1:-}" = "--until" ] && [ -n "${2:-}" ]; then
  COVER="$2"
fi

run() {  # DRY_RUN 下只打印
  if [ "$DRY_RUN" = "1" ]; then echo "  [dry-run] $*"; else "$@"; fi
}

STAMP=$(date +%Y%m%d-%H%M%S)
echo "== 演示态切换 → $LANG_ARG（覆盖到 $COVER，DRY_RUN=$DRY_RUN）=="

echo "== 1/7 备份 ERP 库"
run cp "$ERP_REPO/db.sqlite3" "$ERP_REPO/db.sqlite3.bak-langswitch-$STAMP"

echo "== 2/7 停 nursing-erp"
run systemctl --user stop nursing-erp.service

echo "== 3/7 ERP 全量重灌（$LANG_ARG，锚定今天，张国栋/李秀兰留白）"
run bash -c "cd $ERP_REPO && uv run python scripts/rebuild_demo_data.py \
  --lang $LANG_ARG --cover-until $COVER --demo-free 1,2 --force"

echo "== 4/7 PG 工单/活动·投诉重播（$LANG_ARG）"
run python3 "$AI_REPO/scripts/seed_work_orders_demo.py" --lang "$LANG_ARG"
run python3 "$AI_REPO/scripts/seed_pg_demo_en.py" --lang "$LANG_ARG"

echo "== 5/7 PG 菜单从 ERP WeekMenu 重导（语言跟随）"
run python3 "$AI_REPO/scripts/sync_pg_meals_from_erp.py"

echo "== 6/7 写语言标记（keepfresh cron + UI/chat 缺省语言跟随）"
if [ "$DRY_RUN" = "1" ]; then
  echo "  [dry-run] echo $LANG_ARG > $AI_REPO/logs/demo_lang"
else
  echo "$LANG_ARG" > "$AI_REPO/logs/demo_lang"
fi

echo "== 7/7 起服务 + 验证"
run systemctl --user start nursing-erp.service
if [ "$DRY_RUN" != "1" ]; then
  sleep 2
  systemctl --user is-active --quiet nursing-erp.service && echo "  服务：active ✓"
  curl -s -o /dev/null -w "  ERP /admin/login/ → HTTP %{http_code}\n" \
    http://127.0.0.1:8765/admin/login/
  echo "  演示标记：$(cat "$AI_REPO/logs/demo_lang")"
  echo "提醒：楼长/院长请重新登录；显式点过语言按钮的浏览器请手动切回。"
fi
echo "== 完成 =="
