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
        display_name="HR onboarding emails",
        default_trigger="event",
        description=(
            "Welcome email on hire; day-3 manager introduction behind an "
            "approval gate. Pilot flow (spec §12)."
        ),
    ),
    FlowDescriptor(
        id="content.pipeline",
        version="1.0.0",
        code_ref="dl_control.workflows.flows.content_pipeline:flow",
        display_name="Content operations pipeline",
        default_trigger="event",
        description=(
            "Automated content creation pipeline: hotspot monitoring → "
            "relevance scoring → fact verification → content strategy → "
            "WeChat/XHS article generation → image generation → "
            "compliance check → Feishu publishing."
        ),
    ),
]
