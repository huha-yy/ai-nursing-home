# TOOLS.md — 总务科

Skills define *how* tools work. This file describes the tools available to the 总务科.

## 可用技能一览

| 技能 | 用途 | 使用场景 |
|------|------|----------|
| `logistics-inventory` | 库存管理 | 入库/出库/盘点/调拨 |
| `logistics-query` | 库存查询 | 查看库存数量、安全库存预警 |
| `nursing-work-order` | 护理工单查询 | 了解耗材消耗相关的护理任务 |
| `meal-query` | 餐饮查询 | 查看菜单、配合食材采购 |
| `staff-query` | 员工查询 | 查看各科室人员、了解物资需求方 |
| `activity-query` | 活动查询 | 查看活动安排、准备活动物资 |

## 典型工作流

1. **库存预警响应**: 用 `logistics-query` 发现低于安全库存的物资 → 确认是否需采购 → 用 `logistics-inventory` 记录采购入库
2. **护理耗材补充**: 收到护理科耗材需求 → 查 `logistics-query` 确认当前库存 → 如充足则出库，不足则采购
3. **月度盘点**: 用 `logistics-inventory` 导出月度出入库清单 → 核对实物 → 生成盘点报告
4. **活动物资准备**: 查 `activity-query` 了解近期活动 → 确认物资需求 → 提前备好桌椅、奖品、音响等
5. **设施报修**: 当护理/餐饮/活动等部门报修设备 → 用 `staff-query` 找到报修人 → 安排维修人员处理
