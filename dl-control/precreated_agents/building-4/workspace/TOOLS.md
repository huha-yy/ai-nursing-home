# TOOLS.md — 4号楼

Skills define *how* tools work. This file describes the tools available to 4号楼楼长.

## 可用技能一览

| 技能 | 用途 | 使用场景 |
|------|------|----------|
| `nursing-work-order` | 护理工单管理 | 查看/核实本楼护理任务完成情况 |
| `logistics-query` | 后勤库存查询 | 查看本楼耗材余量、提交领用需求 |
| `meal-query` | 餐饮查询 | 查看菜单、为特殊饮食需求老人核实餐食 |
| `staff-query` | 员工查询 | 查看本楼护工信息、联系相关人员 |
| `resident-query` | 老人档案查询 | 查看本楼老人详细信息 |
| `activity-query` | 活动查询 | 查看院内活动、组织本楼老人参加 |
| `alert-query` | 健康预警查询 | 关注本楼老人的健康预警并及时响应 |

## 典型工作流

1. **晨间巡查**: `nursing-work-order` 查昨日工单完成情况 → `alert-query` 查新增预警 → 实地巡查各房间
2. **纸尿裤管理**: `logistics-query` 查纸尿裤库存（本楼R019用量大）→ 提前申领避免断供
3. **术后康复**: `resident-query` 查R022康复进度 → `nursing-work-order` 核实每日理疗工单 → 记录恢复情况
4. **耗材申领**: `logistics-query` 查本楼物资余量 → 提交领用申请给总务科
5. **突发事件**: `alert-query` 核实预警 → `staff-query` 找当值护工 → 现场处置后记录到工单
