# TOOLS.md — 护理科

Skills define *how* tools work. This file describes the tools available to the 护理科.

## 可用技能一览

| 技能 | 用途 | 使用场景 |
|------|------|----------|
| `nursing-schedule` | 排班管理 | 制定/调整/查询员工排班 |
| `nursing-work-order` | 护理工单管理 | 创建/分配/跟踪/核实工单完成 |
| `logistics-query` | 后勤库存查询 | 查看护理耗材库存、申请补充 |
| `meal-query` | 餐饮查询 | 查看菜单、为特殊需求老人调整饮食 |
| `staff-query` | 员工查询 | 查看护工信息和资质 |
| `resident-query` | 老人档案查询 | 查看老人护理等级、病史、特殊需求 |
| `activity-query` | 活动查询 | 查看活动安排、组织老人参与 |
| `alert-query` | 健康预警查询 | 查看和响应健康预警 |

## 典型工作流

1. **排班审核**: 用 `nursing-schedule` 查看本周排班 → 检查是否有空缺或超负荷 → 调整不合理安排
2. **工单跟踪**: 用 `nursing-work-order` 查看今日待完成工单 → 重点跟进未完成的 → 核实异常工单
3. **新入住老人**: 先 `resident-query` 创建档案 → 根据护理等级创建初始工单 → 用 `nursing-schedule` 分配责任护工
4. **预警处理**: 收到 `alert-query` 预警 → 查看老人档案确认病史 → 创建工单指派护工处理
5. **耗材申请**: 查 `logistics-query` 确认护理耗材库存 → 如不足则向总务科提出需求
