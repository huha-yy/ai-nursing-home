# 运营大屏数据溯源（chat.eldcare.cn:8443/dashboard）

> 原则：**演示数据可以假，但口径必须真**——大屏上每个数字被问"怎么来的"
> 都要有答案，数字之间要能互相印证。本文档逐组件记录数据来源与口径。
> 建立于 2026-08-25；改口径时同步更新此表。

## 逐组件溯源

| 组件 | 数据来源 | 口径 / 依据 |
|---|---|---|
| 在院老人 / 入住率 | ERP `/api/residents/` + `/api/beds/occupancy/` | 在院 = ERP Resident 在院状态；入住率 = 占用床 ÷ 总床（楼栋行求和取整） |
| 今日当班 | AI 侧 Postgres `nursing_schedules` | 当日白班/夜班 DISTINCT 护理员数（排班技能生成） |
| 库存预警 + 库存不足表 | ERP `InventoryItem` | 当前库存 < 安全库存（`is_low_stock`） |
| 评估待办 | ERP `/api/assessments/review/` | 待首评 + 到期复评两项计数之和 |
| 未处理告警 | AI 侧 Postgres 健康告警 | 未处理健康信号告警计数 |
| 今日餐食 | ERP `/api/week-menu/?week_start=` | 按 ERP 中文星期（周X）取今日三餐；菜签颜色按 ERP category（主食/汤/素菜/荤菜/小菜） |
| 今日点餐动态 | ERP `/api/meal-orders/?date=&meal_type=` ×3 | 退餐不计总量但保留计数；特殊餐只数未退单 |
| 护理等级分布 | ERP `/api/residents/` | care_level 计数，定序 自理→半护→全护→失智→特护、未定兜底；色阶 = 照护强度递进 |
| **今日护理完成率** | **AI 侧 Postgres `nursing_work_orders`（静态演示）** | ⚠️ 见下节 |

## ⚠️ 今日护理完成率 —— 静态演示数字（当前不接 ERP）

**现状**：完成率 = `nursing_work_orders` 表里有效日期的
`count(completed) / count(*)`（dl-control `main.py`，`_eff_date` 无当日数据时
回落 MAX(date)）。这张表**没有任何运行时派发机制写入**——以下三个来源全是
一次性灌入的演示数据：

1. `infra/postgres/init/03-nursing-seed.sql`：2026-07-19~21 的 30 条样例 +
   每日启动复制块（从 MAX(date)<今日 整行复制，completed 标志原样带过）；
2. 早期演示批次：2026-07-27 / 07-29 各 30 条（不在 seed 里，当时手工/脚本
   生成）；
3. 2026-08-25 调数：7-29 批次中 9 条翻为 completed（血压×3/翻身×2/鼻饲×2/
   防走失/口腔），53% → **83%**，留 5 条未完成保真。

**被问"83% 怎么来的"时的标准答案**：演示口径——按护理工单完成数/总数
（工单为演示样本，不随真实护理工作变化）。**不要**宣称它来自 ERP。

**与其他组件的关系**：大屏另外五个 ERP 组件均为真实口径可追述；完成率是
唯一例外，演示时避免把两者混为一谈。

## 未来转正路径（已论证未实施，2026-08-25 用户拍板暂不做）

**分级护理派单模型**（推荐口径，材料已验证齐备）：

- 应做任务 = 在院老人 × 护理等级对应服务项目（ERP 演示脚本
  `rebuild_demo_data.py` 的 `cat_by_level` 已是该逻辑：自理[喂饭/洗漱/
  生命体征/康复]、半护[+如厕/服药]、全护[翻身/如厕/喂饭/洗漱/服药]、
  失智[喂饭/洗漱/如厕/翻身/生命体征]——转正为派单模板常量）
- 已完成 = 当日 ERP `NursingLog`（`residents/models.py:91`，7 类护理类型）
  中 (老人, 类型) 命中的任务
- 完成率 = 已完成 ÷ 应做；分子分母均可追述到 ERP 真表，与家属端"照护
  摘要"同源

实施清单（届时照做）：ERP 加只读 `GET /api/nursing-logs/?log_date=`（现在
只有按老人查 `GET /residents/{id}/logs/` 和员工写入 `POST /nursing-logs/`）；
dl-control 完成率 + 工单详情表换口径（Python 改动需重建容器）；重跑
`rebuild_demo_data.py`（anchor=当天，先备份）；`/work-orders` 页同步换口径。
