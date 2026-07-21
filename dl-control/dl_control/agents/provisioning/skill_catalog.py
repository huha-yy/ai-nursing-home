"""Catalogue of custom skills (spec §6.5).

Any skill name in CUSTOM_SKILL_NAMES is rendered with source: custom
instead of the default source: vendor.
"""

CUSTOM_SKILL_NAMES = frozenset(
    {
        "admin-mgmt",
        "cognee",
        "workflow",
        # Content pipeline skills (content-ops suite)
        "hotspot-monitor",
        "relevance-judge",
        "fact-research",
        "content-strategy",
        "wechat-content",
        "xhs-content",
        "douyin-content",
        "image-generator",
        "article-composer",
        "compliance-check",
        "publish-package",
        "feishu-publisher",
        # Phase 1+2 integration from openclaw-mvp
        "humanizer",
        "self-improving",
        "web-content-fetcher",
        "openai-whisper",
        "nano-pdf",
        # Phase 2 features
        "vision-ocr",
        "ppt-generator",
        # PPT Master — AI 原生可编辑 PPTX 生成
        "ppt-master",
        # GBrain knowledge base MCP shim
        "gbrain-mcp",
    }
)
