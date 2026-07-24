"""Shipped-flow catalog — registered at boot into workflow/workflow_version
(spec §9). One FlowDescriptor per shipped (flow, version); prior versions
stay listed while any appliance may still hold non-terminal runs pinned to
them (version-addressable retention, §9)."""

from __future__ import annotations

from dl_control.workflows.registry import FlowDescriptor

SHIPPED_FLOWS = [
    FlowDescriptor(
        id="hr.onboarding_email",
        version="1.0.0",
        code_ref="dl_control.workflows.flows.hr_onboarding:flow",
        display_name="入职邮件流程",
        default_trigger="event",
        description="新员工入职时自动发送欢迎邮件，第 3 天经理介绍需审批。试点流程。",
    ),
    FlowDescriptor(
        id="content.pipeline",
        version="1.0.0",
        code_ref="dl_control.workflows.flows.content_pipeline:flow",
        display_name="内容管道流程",
        default_trigger="event",
        description="自动化内容创作：热点监控 → 相关性评分 → 事实核查 → 内容策略 → 微信/小红书文章 → 图片生成 → 合规检查 → 飞书发布。",
    ),
    FlowDescriptor(
        id="nursing.ops",
        version="1.0.0",
        code_ref="dl_control.workflows.flows.nursing_ops:nursing_ops_flow",
        display_name="护理运营流程",
        default_trigger="event",
        description="每周自动执行：护理科生成排班 → 总务科安排配送 → 财务科成本预估 → 院长生成周报表。",
    ),
]
