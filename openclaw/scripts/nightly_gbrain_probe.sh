#!/bin/bash
# GBrain 夜间自动质量扫描
# 每天凌晨 3:00 执行，由宿主机 crontab 触发

LOG="/home/li/data/dato_prod-main/logs/gbrain-nightly-probe.log"
mkdir -p "$(dirname "$LOG")"
echo "=== $(date +'%Y-%m-%d %H:%M:%S') 夜间质量扫描开始 ===" >> "$LOG"

# 1. 健康检查
docker exec dl-gbrain gbrain doctor 2>&1 | tail -15 >> "$LOG"

# 2. dream 维护周期（lint + backlinks + embed + orphans + purge）
docker exec dl-gbrain gbrain dream 2>&1 | grep -E "^(\\[cycle|\\[dry|\\[orphans|\\[error)" >> "$LOG"

# 3. 统计
docker exec dl-gbrain gbrain stats 2>&1 >> "$LOG"

# 4. GBrain → cognee 同步（让管线能搜到新知识）
echo "--- 开始 GBrain → cognee 同步 ---" >> "$LOG"
python3 /home/li/data/dato_prod-main/openclaw/scripts/sync_gbrain_to_cognee.py 2>&1 | tail -10 >> "$LOG"

echo "=== $(date +'%Y-%m-%d %H:%M:%S') 完成 ===" >> "$LOG"
