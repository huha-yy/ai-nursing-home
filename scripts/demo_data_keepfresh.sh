#!/usr/bin/env bash
# 演示数据每日保鲜（2026-09-12）——宿主机 crontab 每日执行。
#
# 背景：ERP 点餐/周菜单/排班（cover-until）与 PG meals/schedules
# 已铺到 2026-09-30 不会陈旧；但三块数据的语义锚定"运行日"，次日就旧：
#   1. ERP 异常上报（incidents）——铺近两周锚定运行日 → 本脚本调
#      rebuild_demo_data.py --refresh-incidents 删全量重播 25 条锚定今天
#   2. PG 工单（nursing_work_orders）——过去 14 天窗口止于运行日（完成率
#      语义不可前铺）→ 本脚本重跑 seed_work_orders_demo.py（幂等 DELETE+INSERT）
#   3. PG 活动/投诉（nursing_activities/complaints）——窗口 today-7～+15
#      同样锚定运行日 → 本脚本重跑 seed_pg_demo_en.py（幂等 DELETE+INSERT，
#      2026-09-15 纳入日常保鲜）
#
# 语言感知（2026-09-15，P5 英文演示窗口）：语言标记文件 logs/demo_lang
# 内容一行 "zh" 或 "en"。英文演示期间写入 en，cron 滚动保鲜即保持英文
# （不再每日把英文数据刷回中文）；恢复中文演示时改回 zh。标记缺失/
# 内容非法一律按 zh——与 09-15 之前的行为完全一致，cron 无感。
#
# DRY_RUN=1 bash scripts/demo_data_keepfresh.sh —— 只打印将执行的命令，
# 不写库不写日志（验证语言标记组合用，绝不真跑）。
#
# 周报不在保鲜范围：现场触发（~4 分钟出）语义最真实。
#
# 日志：logs/demo-keepfresh.log（仓库根 logs/，已 gitignore）
set -uo pipefail

AI_REPO="$(cd "$(dirname "$0")/.." && pwd)"
ERP_REPO="/home/nursing-home/huha-project/nursing-erp"
LOG_DIR="$AI_REPO/logs"
LOG="$LOG_DIR/demo-keepfresh.log"

DRY_RUN="${DRY_RUN:-0}"

if [ "$DRY_RUN" = "1" ]; then
  # 演练模式：输出到 stdout 供人工核对，不碰日志与数据库
  :
else
  mkdir -p "$LOG_DIR"
  exec >> "$LOG" 2>&1
fi
echo "===== $(date '+%F %T') keepfresh 开始（DRY_RUN=$DRY_RUN） ====="

# 语言标记归一：只认精确 "en"，其余（缺失/空/垃圾/en_US.UTF-8 等）一律 zh
DLANG="zh"
if [ -f "$LOG_DIR/demo_lang" ]; then
  _v="$(tr -d '[:space:]' < "$LOG_DIR/demo_lang" 2>/dev/null)"
  [ "$_v" = "en" ] && DLANG="en"
fi
echo "语言标记：$DLANG（$LOG_DIR/demo_lang）"

if [ "$DRY_RUN" = "1" ]; then
  echo "   [dry-run] (cd $ERP_REPO && uv run python scripts/rebuild_demo_data.py --refresh-incidents --lang $DLANG)"
  echo "   [dry-run] python3 $AI_REPO/scripts/seed_work_orders_demo.py --lang $DLANG"
  echo "   [dry-run] python3 $AI_REPO/scripts/seed_pg_demo_en.py --lang $DLANG"
  echo "===== $(date '+%F %T') keepfresh 结束（dry-run，未执行） ====="
  exit 0
fi

echo "-- [1/3] ERP 告警滚动保鲜（--refresh-incidents --lang $DLANG）"
if (cd "$ERP_REPO" && uv run python scripts/rebuild_demo_data.py --refresh-incidents --lang "$DLANG"); then
  echo "   incidents: OK"
else
  echo "   incidents: FAILED（看上方报错；不影响下一步）"
fi

echo "-- [2/3] PG 工单重播（seed_work_orders_demo --lang $DLANG）"
if python3 "$AI_REPO/scripts/seed_work_orders_demo.py" --lang "$DLANG"; then
  echo "   work_orders: OK"
else
  echo "   work_orders: FAILED"
fi

echo "-- [3/3] PG 活动/投诉重播（seed_pg_demo_en --lang $DLANG）"
if python3 "$AI_REPO/scripts/seed_pg_demo_en.py" --lang "$DLANG"; then
  echo "   activities/complaints: OK"
else
  echo "   activities/complaints: FAILED"
fi

echo "===== $(date '+%F %T') keepfresh 结束 ====="
