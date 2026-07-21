# TOOLS.md — 院长

Skills define *how* tools work. This file describes the tools available to the 院长.

## 可用技能一览

| 技能 | 用途 | 使用场景 |
|------|------|----------|
| `nursing-work-order` | 护理工单管理 | 查看/创建/跟踪护理任务完成情况 |
| `logistics-query` | 后勤库存查询 | 查看库存状况、短缺预警 |
| `meal-query` | 餐饮查询 | 查看每日菜单、营养搭配 |
| `staff-query` | 员工查询 | 查看员工信息、角色、排班 |
| `resident-query` | 老人档案查询 | 查看老人基本信息、护理等级、诊断 |
| `activity-query` | 活动查询 | 查看近期活动安排、参与情况 |
| `finance-query` | 财务查询 | 查看费用收缴情况、欠费提醒 |
| `alert-query` | 健康预警查询 | 查看未处理的健康预警、严重程度 |
| `report-generate` | 报告生成 | 生成运营日报/周报/月报 |

## 典型工作流

1. **晨间巡查**: 先查 `alert-query` 看昨夜有无新增预警 → 查 `nursing-work-order` 看昨日工单完成率 → 如有问题指派对应科室
2. **库存核查**: 用 `logistics-query` 查看低于安全库存的物品 → 通知总务科补货
3. **财务审查**: 用 `finance-query` 查本月欠费 → 通知财务科催缴
4. **活动安排**: 用 `activity-query` 查看本周活动 → 评估参与度和反馈
5. **周报生成**: 用 `report-generate` 整合一周护理、财务、活动数据生成报告
