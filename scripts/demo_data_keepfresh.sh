#!/usr/bin/env bash
# 演示数据每日保鲜（2026-09-12）——宿主机 crontab 每日执行。
#
# 背景：ERP 点餐/周菜单/排班（cover-until）与 PG meals/schedules/activities
# 已铺到 2026-09-30 不会陈旧；但两块数据的语义锚定"运行日"，次日就旧：
#   1. ERP 异常上报（incidents）——铺近两周锚定运行日 → 本脚本调
#      rebuild_demo_data.py --refresh-incidents 删全量重播 25 条锚定今天
#   2. PG 工单（nursing_work_orders）——过去 14 天窗口止于运行日（完成率
#      语义不可前铺）→ 本脚本重跑 seed_work_orders_demo.py（幂等 DELETE+INSERT）
#
# 周报不在保鲜范围：现场触发（~4 分钟出）语义最真实。
#
# 日志：logs/demo-keepfresh.log（仓库根 logs/，已 gitignore）
set -uo pipefail

AI_REPO="$(cd "$(dirname "$0")/.." && pwd)"
ERP_REPO="/home/nursing-home/huha-project/nursing-erp"
LOG_DIR="$AI_REPO/logs"
LOG="$LOG_DIR/demo-keepfresh.log"

mkdir -p "$LOG_DIR"
exec >> "$LOG" 2>&1
echo "===== $(date '+%F %T') keepfresh 开始 ====="

echo "-- [1/2] ERP 告警滚动保鲜（--refresh-incidents）"
if (cd "$ERP_REPO" && uv run python scripts/rebuild_demo_data.py --refresh-incidents); then
  echo "   incidents: OK"
else
  echo "   incidents: FAILED（看上方报错；不影响下一步）"
fi

echo "-- [2/2] PG 工单重播（seed_work_orders_demo）"
if python3 "$AI_REPO/scripts/seed_work_orders_demo.py"; then
  echo "   work_orders: OK"
else
  echo "   work_orders: FAILED"
fi

echo "===== $(date '+%F %T') keepfresh 结束 ====="
